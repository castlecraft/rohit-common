import frappe


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def contact_query(doctype, txt, searchfield, start, page_len, filters):
	from frappe.desk.reportview import get_match_cond

	doctype = "Contact"
	# This validation line is fine, we just won't use the 'searchfield' variable in the SQL
	if not frappe.get_meta(doctype).get_field(searchfield) and searchfield not in frappe.db.DEFAULT_COLUMNS:
		return []

	link_doctype = filters.pop("link_doctype")
	link_name = filters.pop("link_name")

	# --- START OF CHANGES ---
	# Define all fields you want to be able to search by
	search_fields_list = [
		"full_name",
		"first_name",
		"last_name",
		"email_id",
		"phone",
		"mobile_no",
		"company_name",
	]

	# Build the OR conditions dynamically
	search_conditions = " OR ".join([f"`tabContact`.`{field}` like %(txt)s" for field in search_fields_list])
	# --- END OF CHANGES ---

	return frappe.db.sql(
		f"""select
			`tabContact`.name, `tabContact`.full_name, `tabContact`.company_name,
			`tabContact`.email_id, `tabContact`.phone, `tabContact`.mobile_no
		from
			`tabContact`, `tabDynamic Link`
		where
			`tabDynamic Link`.parent = `tabContact`.name and
			`tabDynamic Link`.parenttype = 'Contact' and
			`tabDynamic Link`.link_doctype = %(link_doctype)s and
			`tabDynamic Link`.link_name = %(link_name)s and
			({search_conditions})
			{get_match_cond(doctype)}
		order by
			if(locate(%(_txt)s, `tabContact`.full_name), locate(%(_txt)s, `tabContact`.company_name), 99999),
			`tabContact`.idx desc, `tabContact`.full_name
		limit %(start)s, %(page_len)s """,
		{
			"txt": "%" + txt + "%",
			"_txt": txt.replace("%", ""),
			"start": start,
			"page_len": page_len,
			"link_name": link_name,
			"link_doctype": link_doctype,
		},
	)