# rohit_common/utils/find_ambiguous_accounts.py
import frappe
import json

GST_START_DATE = "2017-07-01"

def execute():
    print(f"--- Finding all unique ambiguous Account Heads from Sales Taxes (since {GST_START_DATE}) ---")
    
    query = """
        SELECT DISTINCT t.account_head
        FROM `tabSales Taxes and Charges` t
        JOIN `tabSales Invoice` si ON t.parent = si.name
        WHERE IFNULL(t.gst_tax_type, '') = ''
          AND t.parenttype = 'Sales Invoice'
          AND si.posting_date >= %(gst_start_date)s
          AND t.account_head IS NOT NULL
          AND t.account_head != ''
    """
    
    try:
        results = frappe.db.sql(
            query,
            {"gst_start_date": GST_START_DATE},
            as_list=True,
        )
        
        unique_accounts = [r[0] for r in results]
        
        print("\n*** AMBIGUOUS ACCOUNT HEADS FOUND ***")
        print("Found " + str(len(unique_accounts)) + " unique account heads that need mapping.")
        print(json.dumps(unique_accounts, indent=2))
        
        print("\n--- ACTION REQUIRED ---")
        print("Please copy this list and tell me the correct GST type for each entry.")
        print("Example reply:")
        print("'My Tax Account 1': 'cgst'")
        print("'My Tax Account 2': 'sgst'")
        print("'My IGST Account': 'igst'")
        print("'My CESS Account': 'cess'")
        print("'Old VAT Account': 'None' (or just leave it out)")

        return unique_accounts

    except Exception as e:
        print(f"An error occurred: {e}")
        frappe.log_error(title="Find Ambiguous Accounts Failed")
        return {"error": str(e)}