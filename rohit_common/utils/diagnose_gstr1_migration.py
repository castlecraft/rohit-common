# diagnose_gstr1_migration.py
import frappe
from frappe.utils import cint
from frappe import get_all, get_doc

frappe.init_site = lambda *a, **k: None  # no-op placeholder in some benches
# Diagnostics: which docs have blank/missing fields used by generate_gstr1

def sample_rows(doctype, fields, limit=5, filters=None):
    filters = filters or {}
    return frappe.get_all(doctype, fields=fields, limit_page_length=limit, filters=filters)

def print_section(title):
    print("\n" + "="*6 + " " + title + " " + "="*6)

def check_file():
    frappe.connect()
    print_section("Checking GST Settings")
    try:
        settings = frappe.get_single("GST Settings")
        print("GST Settings exists.")
        keys_to_check = ["compare_unfiled_data"]
        for k in keys_to_check:
            print(f"{k}: {getattr(settings, k, None)}")
        # check credential-related fields if present
        print("Available fields on GST Settings:", [d.fieldname for d in frappe.get_meta("GST Settings").fields])
    except Exception as e:
        print("GST Settings missing or error:", e)

    # GSTR-1 docs
    print_section("GSTR-1 Documents")
    gstr_docs = frappe.get_all("GSTR-1", fields=["name", "company", "company_gstin", "month_or_quarter", "year", "filing_preference", "filing_status", "is_latest_data"], limit_page_length=200)
    print(f"Total GSTR-1 docs found: {len(gstr_docs)}")
    for i, d in enumerate(gstr_docs[:10]):
        print(i+1, d)

    # Check for blank key fields
    def get_blank_count(doctype, field):
        cnt = frappe.db.count(doctype, {field: ["in", ("", None)]})
        print(f"{doctype}.{field} blank count: {cnt}")
        if cnt:
            print("Sample rows:", sample_rows(doctype, ["name", field], limit=5, filters={field: ["in", ("", None)]}))

    for field in ["company_gstin", "company", "month_or_quarter", "year", "filing_preference", "filing_status"]:
        get_blank_count("GSTR-1", field)

    # Check GST Return log / GSTR1 Log doctype (common names: "GSTR1 Log" or "GST Return Log")
    print_section("GST Return Log (GSTR1 Log)")
    possible = ["GST Return Log", "GSTR1 Log", "GSTR-1 Log"]
    found = False
    for doctype in possible:
        try:
            cnt = frappe.db.count(doctype)
            print(f"Found {doctype}: {cnt} rows")
            # inspect sample
            print("Sample:", sample_rows(doctype, ["name", "company", "filing_preference", "filing_status", "status"], limit=5))
            found = True
        except Exception:
            continue
    if not found:
        print("Could not find a GST Return Log doctype among common names. Please share the exact doctype name.")

    # Check JSON fields and is_latest_data
    print_section("GSTR-1 JSON fields")
    # Look for NULL / empty json fields on GSTR-1: filed, unfiled, books
    for f in ["filed", "unfiled", "books", "filed_summary", "books_summary", "unfiled_summary"]:
        if frappe.db.has_column("GSTR-1", f):
            cnt = frappe.db.count("GSTR-1", {f: ["in", ("", None)]})
            print(f"GSTR-1.{f} blank count: {cnt}")
        else:
            print(f"GSTR-1 does not have column {f} (maybe rename in your version)")

    frappe.db.commit()
    frappe.destroy()

# if __name__ == "__main__":
#     main()
def execute():
    return check_file()

# bench --site development.localhost execute rohit_common.utils.diagnose_gstr1_migration.execute