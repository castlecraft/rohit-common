# rohit_common/utils/fill_gst_tax_type.py
import frappe
import csv
import os
from typing import Optional

def _first_two_gstin_digits(gstin: Optional[str]) -> Optional[str]:
    if not gstin:
        return None
    gstin = gstin.strip()
    if len(gstin) >= 2 and gstin[:2].isdigit():
        return gstin[:2]
    return None

def _keyword_in(text, keywords):
    if not text:
        return None
    t = text.lower()
    for k in keywords:
        if k.lower() in t:
            return k.lower()
    return None

def infer_tax_type_from_row(tax_row, invoice_doc, company_state_code):
    """
    Return one of 'igst','cgst','sgst' or None if couldn't infer.
    """
    acct = tax_row.get("account_head") or ""
    # Priority 1: keywords in account_head or description
    if _keyword_in(acct, ["IGST", "Integrated GST", "Integrated","Excise Duty 10 - RIGB","Sales - RIGB","Postal Expenses - RIGB"] ) :
        return "igst"
    if _keyword_in(acct, ["CGST", "Central GST", "Central","CST-CForm - RIGB"] ) :
        return "cgst"
    if _keyword_in(acct, ["SGST", "State GST", "State"] ) :
        return "sgst"

    # Priority 2: compare GSTIN state codes (invoice -> company)
    # invoice may carry customer_gstin / shipping_address_gstin / billing_address_gstin or place_of_supply "NN-State"
    invoice_state_code = None
    for f in ("shipping_address_gstin", "customer_gstin", "billing_address_gstin"):
        if invoice_doc.get(f):
            invoice_state_code = _first_two_gstin_digits(invoice_doc.get(f))
            if invoice_state_code:
                break

    # place_of_supply may look like "07-Delhi" -> take leading two digits
    if not invoice_state_code and invoice_doc.get("place_of_supply"):
        pos = invoice_doc.get("place_of_supply")
        if isinstance(pos, str) and len(pos) >= 2 and pos[:2].isdigit():
            invoice_state_code = pos[:2]

    if invoice_state_code and company_state_code:
        if invoice_state_code != company_state_code:
            return "igst"
        else:
            # same state -> likely CGST + SGST. If tax_row.rate equals combined rate (rare), leave None.
            # We can't reliably tell whether this row is CGST or SGST without keywords or sibling info.
            return None

    return None

def choose_cgst_or_sgst_by_siblings(tax_rows, idx_missing):
    """
    If some siblings have cgst/sgst, pick the complementary type if possible.
    tax_rows: list of dicts with existing gst_tax_type values (may be None).
    idx_missing: index of the missing row to decide for.
    """
    types = set([r.get("gst_tax_type") for r in tax_rows if r.get("gst_tax_type")])
    if "cgst" in types and "sgst" not in types:
        # there is a cgst, so pick sgst for missing
        return "sgst"
    if "sgst" in types and "cgst" not in types:
        return "cgst"
    # if neither or both present, cannot decide
    return None

def repopulate_gst_tax_type(dry_run=True, batch_size=200, limit=None):
    frappe.flags.in_migrate = True

    # Prepare CSV audit file paths
    timestamp = frappe.utils.now_datetime().strftime("%Y%m%d%H%M%S")
    if dry_run:
        csv_path = f"/tmp/gst_tax_type_dryrun_{timestamp}.csv"
    else:
        csv_path = f"/tmp/gst_tax_type_updates_{timestamp}.csv"

    # find sales invoices that have at least one tax row with empty gst_tax_type
    # Use SQL to be robust to schema differences
    limit_clause = f"LIMIT {int(limit)}" if limit else ""
    invoices = frappe.db.sql(
        f"""
        SELECT DISTINCT parent AS name
        FROM `tabSales Taxes and Charges`
        WHERE IFNULL(gst_tax_type, '') = ''
        AND parenttype = 'Sales Invoice'
        {limit_clause}
        """,
        as_dict=True,
    )

    invoice_names = [r["name"] for r in invoices]
    total_invoices = len(invoice_names)
    print(f"Found {total_invoices} Sales Invoice(s) with missing gst_tax_type in Taxes table.")

    summary = {
        "total_invoices": total_invoices,
        "rows_examined": 0,
        "rows_updated": 0,
        "rows_skipped_ambiguous": 0,
        "errors": []
    }

    # open CSV writer
    os.makedirs("/tmp", exist_ok=True)
    csv_file = open(csv_path, "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(csv_file, fieldnames=[
        "invoice", "tax_row_name", "account_head", "description", "rate", "inferred_gst_tax_type", "action", "reason"
    ])
    writer.writeheader()

    updated_count = 0
    processed_invoices = 0

    for inv_name in invoice_names:
        processed_invoices += 1
        try:
            inv = frappe.get_doc("Sales Invoice", inv_name)
        except Exception as e:
            summary["errors"].append((inv_name, f"read invoice error: {e}"))
            continue

        # derive company state code from company_gstin on invoice or company doc
        company_state_code = None
        if inv.get("company_gstin"):
            company_state_code = _first_two_gstin_digits(inv.get("company_gstin"))
        else:
            # fallback to company doc gstin
            try:
                comp = frappe.get_doc("Company", inv.get("company"))
                if comp and comp.get("company_gstin"):
                    company_state_code = _first_two_gstin_digits(comp.get("company_gstin"))
            except Exception:
                company_state_code = None

        tax_rows = inv.get("taxes") or []
        summary_rows_before = summary["rows_examined"]

        for idx, tax_row in enumerate(tax_rows):
            if tax_row.get("gst_tax_type"):
                continue  # already present
            summary["rows_examined"] += 1
            inferred = None
            reason = ""

            # First try strong inference from account_head/description and place_of_supply/company state
            inferred = infer_tax_type_from_row(tax_row, inv.as_dict(), company_state_code)
            if inferred:
                reason = "keyword_or_state_mismatch"
            else:
                # try sibling-based inference if same state (CGST+SGST situation)
                sibling_choice = choose_cgst_or_sgst_by_siblings(tax_rows, idx)
                if sibling_choice:
                    inferred = sibling_choice
                    reason = "sibling_complement"
                else:
                    reason = "ambiguous"

            if inferred:
                # write change (or dry run print)
                writer.writerow({
                    "invoice": inv_name,
                    "tax_row_name": tax_row.get("name"),
                    "account_head": tax_row.get("account_head"),
                    "description": tax_row.get("description"),
                    "rate": tax_row.get("rate"),
                    "inferred_gst_tax_type": inferred,
                    "action": "would_set" if dry_run else "set",
                    "reason": reason
                })
                print(f"[{processed_invoices}/{total_invoices}] {inv_name} -> Tax {tax_row.get('name')}: set gst_tax_type -> {inferred} ({reason})")
                if not dry_run:
                    try:
                        frappe.db.set_value("Sales Taxes and Charges", tax_row.get("name"), "gst_tax_type", inferred, update_modified=True)
                        updated_count += 1
                    except Exception as e:
                        summary["errors"].append((inv_name, tax_row.get("name"), f"update error: {e}"))
                        writer.writerow({
                            "invoice": inv_name,
                            "tax_row_name": tax_row.get("name"),
                            "account_head": tax_row.get("account_head"),
                            "description": tax_row.get("description"),
                            "rate": tax_row.get("rate"),
                            "inferred_gst_tax_type": inferred,
                            "action": "error",
                            "reason": str(e)
                        })
            else:
                # ambiguous - do not change
                summary["rows_skipped_ambiguous"] += 1
                writer.writerow({
                    "invoice": inv_name,
                    "tax_row_name": tax_row.get("name"),
                    "account_head": tax_row.get("account_head"),
                    "description": tax_row.get("description"),
                    "rate": tax_row.get("rate"),
                    "inferred_gst_tax_type": "",
                    "action": "skipped",
                    "reason": reason
                })
                print(f"[{processed_invoices}/{total_invoices}] {inv_name} -> Tax {tax_row.get('name')}: AMBIGUOUS (reason={reason})")

        # commit periodically
        if not dry_run and (updated_count and (updated_count % batch_size == 0)):
            frappe.db.commit()
            print(f"  Committed batch at {updated_count} updates.")

    if not dry_run:
        frappe.db.commit()
        print("Final commit done.")

    csv_file.close()
    summary["rows_updated"] = updated_count
    summary["audit_csv"] = csv_path
    print("Finished. Summary:", summary)
    return summary

# bench execute entrypoint
def execute(dry_run=True, batch_size=200, limit=None):
    dry_run = bool(dry_run)
    batch_size = int(batch_size) if batch_size else 200
    limit = int(limit) if limit else None
    return repopulate_gst_tax_type(dry_run=dry_run, batch_size=batch_size, limit=limit)

# bench --site development.localhost execute rohit_common.utils.fill_gst_tax_type.execute --kwargs "{'dry_run': True, 'limit': 100}"
# bench --site development.localhost execute rohit_common.utils.fill_gst_tax_type.execute --kwargs "{'dry_run': False, 'batch_size': 200}"

# UPDATE `tabSales Taxes and Charges`
# SET gst_tax_type = 'cess'
# WHERE parenttype = 'Sales Invoice'
#   AND IFNULL(gst_tax_type,'') = ''
#   AND (
#       account_head LIKE '%Edu. Cess%'
#       OR account_head LIKE '%SHE Cess%'
#       OR account_head LIKE '%SBC Service Tax%'
#       OR account_head LIKE '%KKC Service Tax%'
#   );
