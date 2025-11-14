# # reconstruct_item_taxes_safe.py
# # Safe version: robust matching + DB writes via frappe.db.set_value (no inv.save) + audit CSV
# import frappe, json, os, math
# from frappe.utils import now_datetime, flt

# TOLERANCE = 0.5  # rupees tolerance when comparing sums

# def _parse_item_wise_blob(blob):
#     if not blob:
#         return {}
#     if isinstance(blob, dict):
#         data = blob
#     else:
#         try:
#             data = json.loads(blob)
#         except Exception:
#             return {}
#     out = {}
#     for key, val in data.items():
#         if isinstance(val, dict):
#             out[key] = {
#                 "tax_rate": float(val.get("tax_rate") or val.get("rate") or 0) if (val.get("tax_rate") or val.get("rate")) else None,
#                 "tax_amount": float(val.get("tax_amount")) if val.get("tax_amount") not in (None, "") else None,
#                 "net_amount": float(val.get("net_amount")) if val.get("net_amount") not in (None, "") else None,
#             }
#         else:
#             try:
#                 out[key] = {"tax_rate": float(val), "tax_amount": None, "net_amount": None}
#             except Exception:
#                 out[key] = {"tax_rate": None, "tax_amount": None, "net_amount": None}
#     return out

# def _find_best_item(candidates, inferred_taxable_value, inferred_rate, tol_amount=0.5):
#     """
#     candidates: list of invoice item dicts
#     prefer exact taxable_value/amount match within tol_amount,
#     else choose closest by amount or rate
#     """
#     if not candidates:
#         return None
#     # try exact taxable_value or amount match
#     best = None
#     best_score = None
#     for it in candidates:
#         tv = flt(it.get("taxable_value") or it.get("amount") or 0)
#         nr = flt(it.get("net_rate") or it.get("rate") or 0)
#         score = 0
#         if inferred_taxable_value:
#             diff = abs(tv - inferred_taxable_value)
#             score = diff
#             if diff <= tol_amount:
#                 return it
#         if inferred_rate and nr:
#             diff_rate = abs(nr - inferred_rate)
#             # combine metrics: smaller rate diff preferred
#             if best_score is None or diff_rate < best_score:
#                 best = it
#                 best_score = diff_rate
#     return best or candidates[0]

# def reconstruct_item_taxes_safe(dry_run=True, limit=None, batch_size=200, tolerance=TOLERANCE):
#     limit_clause = f"LIMIT {int(limit)}" if limit else ""
#     rows = frappe.db.sql(f"""
#         SELECT name, parent as invoice, gst_tax_type, rate, tax_amount, item_wise_tax_detail
#         FROM `tabSales Taxes and Charges`
#         WHERE parenttype = 'Sales Invoice'
#           AND IFNULL(tax_amount,0) <> 0
#           AND IFNULL(item_wise_tax_detail,'') <> ''
#           AND IFNULL(gst_tax_type,'') != ''
#         {limit_clause}
#     """, as_dict=True)

#     total = len(rows)
#     print("Found %d tax rows to examine" % total)
#     if total == 0:
#         return {"rows": 0}

#     os.makedirs("/tmp", exist_ok=True)
#     csv_path = f"/tmp/reconstruct_item_taxes_safe_{now_datetime().strftime('%Y%m%d%H%M%S')}.csv"
#     import csv
#     with open(csv_path, "w", newline='', encoding='utf-8') as f:
#         fieldnames = [
#             "tax_row", "invoice", "tax_row_amount", "sum_itemwise", "row_status",
#             "item_key", "matched_item_name", "matched_item_code",
#             "tax_row_gst_type", "tax_row_rate", "inferred_taxable_value", "inferred_tax_amount",
#             "field_before", "field_after", "action", "notes"
#         ]
#         writer = csv.DictWriter(f, fieldnames=fieldnames)
#         writer.writeheader()

#         updated = 0
#         processed = 0
#         for r in rows:
#             processed += 1
#             tax_row_name = r["name"]
#             invoice_name = r["invoice"]
#             gst_type = (r.get("gst_tax_type") or "").lower()
#             tax_row_rate = flt(r.get("rate") or 0)
#             tax_row_amount = flt(r.get("tax_amount") or 0)
#             blob = r.get("item_wise_tax_detail") or ""
#             parsed = _parse_item_wise_blob(blob)

#             # compute sum of item-wise tax_amounts (only when present)
#             sum_itemwise = 0.0
#             has_item_amounts = False
#             for info in parsed.values():
#                 if info.get("tax_amount") not in (None, ""):
#                     has_item_amounts = True
#                     sum_itemwise += flt(info.get("tax_amount") or 0)

#             if has_item_amounts:
#                 if abs(tax_row_amount - sum_itemwise) > tolerance:
#                     writer.writerow({
#                         "tax_row": tax_row_name, "invoice": invoice_name,
#                         "tax_row_amount": tax_row_amount, "sum_itemwise": sum_itemwise,
#                         "row_status": "mismatch_sum",
#                         "notes": f"tax_row_amount != sum(item_wise) (diff {tax_row_amount - sum_itemwise})"
#                     })
#                     # skip this tax_row entirely for safety
#                     continue
#             else:
#                 # item_wise has only rates or no amounts -> skip (flag)
#                 writer.writerow({
#                     "tax_row": tax_row_name, "invoice": invoice_name,
#                     "tax_row_amount": tax_row_amount, "sum_itemwise": 0,
#                     "row_status": "no_item_amounts",
#                     "notes": "item_wise contains no tax_amount values; skipping"
#                 })
#                 continue

#             # load invoice items (as dicts)
#             try:
#                 inv_items = frappe.db.sql("""
#                     SELECT name, item_code, item_name, amount, taxable_value, igst_rate, igst_amount,
#                            cgst_rate, cgst_amount, sgst_rate, sgst_amount, cess_rate, cess_amount, rate, net_rate
#                     FROM `tabSales Invoice Item`
#                     WHERE parent=%s
#                 """, invoice_name, as_dict=True)
#             except Exception as e:
#                 writer.writerow({"tax_row": tax_row_name, "invoice": invoice_name, "row_status":"invoice_read_error", "notes":str(e)})
#                 continue

#             # build key -> list of candidate items (handle duplicates)
#             item_index = {}
#             for it in inv_items:
#                 keys = []
#                 if it.get("item_code"):
#                     keys.append(str(it.get("item_code")).strip())
#                 if it.get("item_name"):
#                     keys.append(str(it.get("item_name")).strip())
#                 keys.append(str(it.get("name")).strip())
#                 keys.append(str(int(flt(it.get("idx") or 0))) if it.get("idx") else "")
#                 for k in keys:
#                     if k:
#                         item_index.setdefault(k, []).append(it)

#             # keep track matched item names to avoid double-assign where possible
#             matched_item_names = set()

#             # process each parsed item
#             for item_key, info in parsed.items():
#                 inferred_tax_amount = info.get("tax_amount")
#                 inferred_rate = info.get("tax_rate") or tax_row_rate or None
#                 inferred_taxable_value = None
#                 if inferred_tax_amount is not None and inferred_rate:
#                     try:
#                         inferred_taxable_value = (float(inferred_tax_amount) * 100.0) / float(inferred_rate)
#                         inferred_taxable_value = round(inferred_taxable_value, 6)
#                     except Exception:
#                         inferred_taxable_value = None

#                 # candidate list by key
#                 candidates = item_index.get(str(item_key).strip(), [])[:]
#                 # if none, try substring match in item_code/item_name
#                 if not candidates:
#                     for k, lst in item_index.items():
#                         if str(item_key).strip().lower() in str(k).strip().lower():
#                             candidates.extend(lst)
#                 # deduplicate candidates preserving order
#                 cand_seen = []
#                 final_candidates = []
#                 for c in candidates:
#                     n = c.get("name")
#                     if n not in cand_seen:
#                         final_candidates.append(c)
#                         cand_seen.append(n)
#                 candidates = final_candidates

#                 # if still no candidates, use all invoice items as fallback
#                 if not candidates:
#                     candidates = inv_items[:]

#                 best_item = _find_best_item(candidates, inferred_taxable_value, inferred_rate, tol_amount=0.5)
#                 if not best_item:
#                     writer.writerow({
#                         "tax_row": tax_row_name, "invoice": invoice_name,
#                         "row_status": "no_item_found_for_key", "item_key": item_key
#                     })
#                     continue

#                 # determine target DB fields depending on gst_type
#                 target_rate_field = None
#                 target_amount_field = None
#                 if gst_type == "igst":
#                     target_rate_field = "igst_rate"
#                     target_amount_field = "igst_amount"
#                 elif gst_type == "cgst":
#                     target_rate_field = "cgst_rate"
#                     target_amount_field = "cgst_amount"
#                 elif gst_type == "sgst":
#                     target_rate_field = "sgst_rate"
#                     target_amount_field = "sgst_amount"
#                 elif gst_type == "cess":
#                     target_rate_field = "cess_rate"
#                     target_amount_field = "cess_amount"
#                 else:
#                     # unknown type
#                     writer.writerow({
#                         "tax_row": tax_row_name, "invoice": invoice_name, "item_key": item_key,
#                         "row_status": "unknown_gst_type", "notes": f"{gst_type}"
#                     })
#                     continue

#                 # check existing values
#                 before_val = {
#                     "taxable_value": flt(best_item.get("taxable_value") or 0),
#                     "rate": flt(best_item.get("rate") or 0),
#                     target_rate_field: flt(best_item.get(target_rate_field) or 0),
#                     target_amount_field: flt(best_item.get(target_amount_field) or 0)
#                 }

#                 # decide writes only if target fields are zero/empty
#                 writes = {}
#                 if (not before_val["taxable_value"]) and inferred_taxable_value:
#                     writes["taxable_value"] = inferred_taxable_value
#                 if (not before_val[target_rate_field]) and inferred_rate:
#                     writes[target_rate_field] = inferred_rate
#                 if (not before_val[target_amount_field]) and inferred_tax_amount is not None:
#                     writes[target_amount_field] = inferred_tax_amount

#                 if writes:
#                     # audit before/after
#                     for k, v in writes.items():
#                         writer.writerow({
#                             "tax_row": tax_row_name, "invoice": invoice_name, "tax_row_amount": tax_row_amount,
#                             "sum_itemwise": sum_itemwise, "row_status": "will_write",
#                             "item_key": item_key,
#                             "matched_item_name": best_item.get("item_name"),
#                             "matched_item_code": best_item.get("item_code"),
#                             "tax_row_gst_type": gst_type, "tax_row_rate": tax_row_rate,
#                             "inferred_taxable_value": inferred_taxable_value,
#                             "inferred_tax_amount": inferred_tax_amount,
#                             "field_before": f"{k}:{before_val.get(k)}",
#                             "field_after": f"{k}:{v}",
#                             "action": "would_set" if dry_run else "set",
#                             "notes": ""
#                         })
#                     # perform DB writes if not dry_run
#                     if not dry_run:
#                         for k, v in writes.items():
#                             # set on Sales Invoice Item by primary key (name)
#                             try:
#                                 frappe.db.set_value("Sales Invoice Item", best_item.get("name"), k, v, update_modified=False)
#                             except Exception as e:
#                                 writer.writerow({
#                                     "tax_row": tax_row_name, "invoice": invoice_name, "row_status": "write_error",
#                                     "item_key": item_key, "notes": str(e)
#                                 })
#                         updated += 1
#                 else:
#                     writer.writerow({
#                         "tax_row": tax_row_name, "invoice": invoice_name, "tax_row_amount": tax_row_amount,
#                         "sum_itemwise": sum_itemwise, "row_status": "no_change_needed",
#                         "item_key": item_key,
#                         "matched_item_name": best_item.get("item_name"),
#                         "matched_item_code": best_item.get("item_code"),
#                         "tax_row_gst_type": gst_type, "tax_row_rate": tax_row_rate,
#                         "inferred_taxable_value": inferred_taxable_value,
#                         "inferred_tax_amount": inferred_tax_amount,
#                         "field_before": str(before_val), "field_after": "",
#                         "action": "skipped", "notes": "target fields already present"
#                     })

#                 matched_item_names.add(best_item.get("name"))

#             # commit periodically when applying changes
#             if not dry_run and (updated and updated % batch_size == 0):
#                 frappe.db.commit()

#         if not dry_run:
#             frappe.db.commit()

#     return {"rows_examined": total, "csv": csv_path, "updated": (updated if not dry_run else 0)}

# def execute(dry_run=True, limit=None, batch_size=200, tolerance=TOLERANCE):
#     dry_run = bool(dry_run)
#     limit = int(limit) if limit else None
#     return reconstruct_item_taxes_safe(dry_run=dry_run, limit=limit, batch_size=batch_size, tolerance=tolerance)

# # bench --site development.localhost execute rohit_common.utils.reconstruct_item_taxes_improved.execute --kwargs "{'dry_run': True, 'limit': 200}"

# # bench --site development.localhost execute rohit_common.utils.reconstruct_item_taxes_improved.execute --kwargs "{'dry_run': True, 'limit': 200}"

##########################################

# import frappe, json, os, math
# from frappe.utils import now_datetime, flt

# # Tolerances
# SUM_TOLERANCE = 0.5       # rupees tolerance when comparing tax row <-> sum(itemwise)
# AMOUNT_MATCH_TOLERANCE = 0.5  # rupees tolerance when matching amounts

# def _parse_item_wise_blob(blob):
#     if not blob:
#         return {}
#     if isinstance(blob, dict):
#         data = blob
#     else:
#         try:
#             data = json.loads(blob)
#         except Exception:
#             return {}
#     out = {}
#     for key, val in data.items():
#         if isinstance(val, dict):
#             out[key] = {
#                 "tax_rate": float(val.get("tax_rate") or val.get("rate") or 0) if (val.get("tax_rate") or val.get("rate")) else None,
#                 "tax_amount": float(val.get("tax_amount")) if val.get("tax_amount") not in (None, "") else None,
#                 "net_amount": float(val.get("net_amount")) if val.get("net_amount") not in (None, "") else None,
#             }
#         else:
#             try:
#                 out[key] = {"tax_rate": float(val), "tax_amount": None, "net_amount": None}
#             except Exception:
#                 out[key] = {"tax_rate": None, "tax_amount": None, "net_amount": None}
#     return out

# def _find_best_item(candidates, inferred_taxable_value, inferred_rate, tol_amount=AMOUNT_MATCH_TOLERANCE):
#     """
#     Pick best matching item from candidates:
#       - prefer item whose taxable_value/amount matches inferred_taxable_value within tol_amount
#       - else pick the item with smallest difference in rate (net_rate/rate)
#       - else return first candidate
#     """
#     if not candidates:
#         return None
#     # exact taxable value/amount match
#     best = None
#     best_score = None
#     for it in candidates:
#         tv = flt(it.get("taxable_value") or it.get("amount") or 0)
#         nr = flt(it.get("net_rate") or it.get("rate") or 0)
#         if inferred_taxable_value:
#             diff = abs(tv - inferred_taxable_value)
#             if diff <= tol_amount:
#                 return it
#         if inferred_rate and nr is not None:
#             diff_rate = abs(nr - inferred_rate)
#             if best_score is None or diff_rate < best_score:
#                 best = it
#                 best_score = diff_rate
#     return best or candidates[0]

# def _determine_target_fields(gst_type):
#     gst_type = (gst_type or "").lower()
#     if gst_type == "igst":
#         return "igst_rate", "igst_amount"
#     if gst_type == "cgst":
#         return "cgst_rate", "cgst_amount"
#     if gst_type == "sgst":
#         return "sgst_rate", "sgst_amount"
#     if gst_type == "cess":
#         return "cess_rate", "cess_amount"
#     return None, None

# def reconstruct_item_taxes_safe(dry_run=True, limit=None, batch_size=200, sum_tolerance=SUM_TOLERANCE):
#     """
#     dry_run: True => no DB writes (only CSV audit)
#     limit: number of tax rows to examine (None => all)
#     batch_size: commit interval when applying
#     sum_tolerance: rupee tolerance for sums (tax row vs sum of per-item)
#     """
#     limit_clause = f"LIMIT {int(limit)}" if limit else ""
#     rows = frappe.db.sql(f"""
#         SELECT name, parent as invoice, gst_tax_type, rate, tax_amount, item_wise_tax_detail
#         FROM `tabSales Taxes and Charges`
#         WHERE parenttype = 'Sales Invoice'
#           AND IFNULL(tax_amount,0) <> 0
#           AND IFNULL(item_wise_tax_detail,'') <> ''
#           AND IFNULL(gst_tax_type,'') != ''
#         {limit_clause}
#     """, as_dict=True)

#     total = len(rows)
#     print("Found %d tax rows to examine" % total)
#     if total == 0:
#         return {"rows": 0}

#     os.makedirs("/tmp", exist_ok=True)
#     csv_path = f"/tmp/reconstruct_item_taxes_safe_{now_datetime().strftime('%Y%m%d%H%M%S')}.csv"
#     import csv
#     with open(csv_path, "w", newline='', encoding='utf-8') as f:
#         fieldnames = [
#             "tax_row", "invoice", "tax_row_amount", "sum_itemwise", "row_status",
#             "item_key", "matched_item_name", "matched_item_code",
#             "tax_row_gst_type", "tax_row_rate", "inferred_taxable_value", "inferred_tax_amount",
#             "field_before", "field_after", "action", "notes"
#         ]
#         writer = csv.DictWriter(f, fieldnames=fieldnames)
#         writer.writeheader()

#         updated = 0
#         processed = 0
#         for r in rows:
#             processed += 1
#             tax_row_name = r["name"]
#             invoice_name = r["invoice"]
#             gst_type = (r.get("gst_tax_type") or "").lower()
#             tax_row_rate = flt(r.get("rate") or 0)
#             tax_row_amount = flt(r.get("tax_amount") or 0)
#             blob = r.get("item_wise_tax_detail") or ""
#             parsed = _parse_item_wise_blob(blob)

#             # compute sum of item-wise tax_amounts (only when present)
#             sum_itemwise = 0.0
#             has_item_amounts = False
#             for info in parsed.values():
#                 if info.get("tax_amount") not in (None, ""):
#                     has_item_amounts = True
#                     sum_itemwise += flt(info.get("tax_amount") or 0)

#             # if item_wise has explicit amounts -> use those to set per-item fields (conservative path)
#             if has_item_amounts:
#                 # check sum matches tax row amount
#                 if abs(tax_row_amount - sum_itemwise) > sum_tolerance:
#                     writer.writerow({
#                         "tax_row": tax_row_name, "invoice": invoice_name,
#                         "tax_row_amount": tax_row_amount, "sum_itemwise": sum_itemwise,
#                         "row_status": "mismatch_sum",
#                         "notes": f"tax_row_amount != sum(item_wise) (diff {tax_row_amount - sum_itemwise})"
#                     })
#                     # skip this tax_row entirely for safety
#                     continue

#                 # load invoice items
#                 try:
#                     inv_items = frappe.db.sql("""
#                         SELECT name, item_code, item_name, amount, taxable_value, igst_rate, igst_amount,
#                                cgst_rate, cgst_amount, sgst_rate, sgst_amount, cess_rate, cess_amount, rate, net_rate, idx
#                         FROM `tabSales Invoice Item`
#                         WHERE parent=%s
#                     """, invoice_name, as_dict=True)
#                 except Exception as e:
#                     writer.writerow({"tax_row": tax_row_name, "invoice": invoice_name, "row_status":"invoice_read_error", "notes":str(e)})
#                     continue

#                 # index invoice items (key => list)
#                 item_index = {}
#                 for it in inv_items:
#                     keys = []
#                     if it.get("item_code"):
#                         keys.append(str(it.get("item_code")).strip())
#                     if it.get("item_name"):
#                         keys.append(str(it.get("item_name")).strip())
#                     keys.append(str(it.get("name")).strip())
#                     keys.append(str(int(flt(it.get("idx") or 0))) if it.get("idx") else "")
#                     for k in keys:
#                         if k:
#                             item_index.setdefault(k, []).append(it)

#                 matched_item_names = set()

#                 # use explicit item_wise amounts to set per item
#                 for item_key, info in parsed.items():
#                     inferred_tax_amount = info.get("tax_amount")
#                     inferred_rate = info.get("tax_rate") or tax_row_rate or None
#                     inferred_taxable_value = None
#                     if inferred_tax_amount is not None and inferred_rate:
#                         try:
#                             inferred_taxable_value = (float(inferred_tax_amount) * 100.0) / float(inferred_rate)
#                             inferred_taxable_value = round(inferred_taxable_value, 6)
#                         except Exception:
#                             inferred_taxable_value = None

#                     # find candidate list
#                     candidates = item_index.get(str(item_key).strip(), [])[:]
#                     if not candidates:
#                         for k, lst in item_index.items():
#                             if str(item_key).strip().lower() in str(k).strip().lower():
#                                 candidates.extend(lst)
#                     # dedupe
#                     seen = set(); final_candidates = []
#                     for c in candidates:
#                         n = c.get("name")
#                         if n not in seen:
#                             final_candidates.append(c)
#                             seen.add(n)
#                     candidates = final_candidates

#                     if not candidates:
#                         candidates = inv_items[:]

#                     best_item = _find_best_item(candidates, inferred_taxable_value, inferred_rate)
#                     if not best_item:
#                         writer.writerow({
#                             "tax_row": tax_row_name, "invoice": invoice_name,
#                             "item_key": item_key, "row_status":"no_invoice_item_found"
#                         })
#                         continue

#                     target_rate_field, target_amount_field = _determine_target_fields(gst_type)
#                     if not target_rate_field:
#                         writer.writerow({
#                             "tax_row": tax_row_name, "invoice": invoice_name, "item_key": item_key,
#                             "row_status": "unknown_gst_type", "notes": gst_type
#                         })
#                         continue

#                     before_val = {
#                         "taxable_value": flt(best_item.get("taxable_value") or 0),
#                         "rate": flt(best_item.get("rate") or 0),
#                         target_rate_field: flt(best_item.get(target_rate_field) or 0),
#                         target_amount_field: flt(best_item.get(target_amount_field) or 0)
#                     }

#                     writes = {}
#                     if (not before_val["taxable_value"]) and inferred_taxable_value:
#                         writes["taxable_value"] = inferred_taxable_value
#                     if (not before_val[target_rate_field]) and inferred_rate:
#                         writes[target_rate_field] = inferred_rate
#                     if (not before_val[target_amount_field]) and inferred_tax_amount is not None:
#                         # round to 2 decimals for rupee paise
#                         writes[target_amount_field] = round(float(inferred_tax_amount), 2)

#                     if writes:
#                         for k, v in writes.items():
#                             writer.writerow({
#                                 "tax_row": tax_row_name, "invoice": invoice_name, "tax_row_amount": tax_row_amount,
#                                 "sum_itemwise": sum_itemwise, "row_status": "will_write",
#                                 "item_key": item_key,
#                                 "matched_item_name": best_item.get("item_name"),
#                                 "matched_item_code": best_item.get("item_code"),
#                                 "tax_row_gst_type": gst_type, "tax_row_rate": tax_row_rate,
#                                 "inferred_taxable_value": inferred_taxable_value,
#                                 "inferred_tax_amount": inferred_tax_amount,
#                                 "field_before": f"{k}:{before_val.get(k)}",
#                                 "field_after": f"{k}:{v}",
#                                 "action": "would_set" if dry_run else "set",
#                                 "notes": ""
#                             })
#                         if not dry_run:
#                             for k, v in writes.items():
#                                 try:
#                                     frappe.db.set_value("Sales Invoice Item", best_item.get("name"), k, v, update_modified=False)
#                                 except Exception as e:
#                                     writer.writerow({
#                                         "tax_row": tax_row_name, "invoice": invoice_name, "row_status": "write_error",
#                                         "item_key": item_key, "notes": str(e)
#                                     })
#                             updated += 1
#                     else:
#                         writer.writerow({
#                             "tax_row": tax_row_name, "invoice": invoice_name, "tax_row_amount": tax_row_amount,
#                             "sum_itemwise": sum_itemwise, "row_status": "no_change_needed",
#                             "item_key": item_key,
#                             "matched_item_name": best_item.get("item_name"),
#                             "matched_item_code": best_item.get("item_code"),
#                             "tax_row_gst_type": gst_type, "tax_row_rate": tax_row_rate,
#                             "inferred_taxable_value": inferred_taxable_value,
#                             "inferred_tax_amount": inferred_tax_amount,
#                             "field_before": str(before_val), "field_after": "",
#                             "action": "skipped", "notes": "target fields already present"
#                         })

#                     matched_item_names.add(best_item.get("name"))

#                 # end processed tax row with explicit item amounts
#                 if not dry_run and (updated and updated % batch_size == 0):
#                     frappe.db.commit()
#                 continue  # next tax row

#             # -------------------------------------------------------
#             # item_wise contains no explicit amounts -> attempt inference via taxable_value / amount * rate/100
#             # -------------------------------------------------------
#             # build invoice items (we will try to infer)
#             try:
#                 inv_items = frappe.db.sql("""
#                     SELECT name, item_code, item_name, amount, taxable_value, rate, net_rate,
#                            igst_rate, igst_amount, cgst_rate, cgst_amount, sgst_rate, sgst_amount, cess_rate, cess_amount, idx
#                     FROM `tabSales Invoice Item`
#                     WHERE parent=%s
#                 """, invoice_name, as_dict=True)
#             except Exception as e:
#                 writer.writerow({"tax_row": tax_row_name, "invoice": invoice_name, "row_status":"invoice_read_error", "notes":str(e)})
#                 continue

#             # index items
#             item_index = {}
#             for it in inv_items:
#                 keys = []
#                 if it.get("item_code"):
#                     keys.append(str(it.get("item_code")).strip())
#                 if it.get("item_name"):
#                     keys.append(str(it.get("item_name")).strip())
#                 keys.append(str(it.get("name")).strip())
#                 keys.append(str(int(flt(it.get("idx") or 0))) if it.get("idx") else "")
#                 for k in keys:
#                     if k:
#                         item_index.setdefault(k, []).append(it)

#             # attempt inference per parsed key
#             inferred_rows = []
#             sum_inferred = 0.0
#             for item_key, info in parsed.items():
#                 rate_only = info.get("tax_rate")
#                 if not rate_only or rate_only == 0:
#                     # cannot infer if rate missing
#                     continue

#                 # candidates by key or substring
#                 candidates = item_index.get(str(item_key).strip(), [])[:]
#                 if not candidates:
#                     for k, lst in item_index.items():
#                         if str(item_key).strip().lower() in str(k).strip().lower():
#                             candidates.extend(lst)
#                 # dedupe preserving order
#                 seen = set(); final_candidates = []
#                 for c in candidates:
#                     n = c.get("name")
#                     if n not in seen:
#                         final_candidates.append(c)
#                         seen.add(n)
#                 candidates = final_candidates or inv_items[:]

#                 best_item = _find_best_item(candidates, None, rate_only)
#                 if not best_item:
#                     continue

#                 # prefer taxable_value else amount
#                 tv = flt(best_item.get("taxable_value") or 0)
#                 if not tv:
#                     tv = flt(best_item.get("amount") or 0)
#                 if not tv or tv == 0:
#                     # cannot infer if tv is zero
#                     continue

#                 inferred_tax_amount = round((tv * float(rate_only) / 100.0), 2)
#                 inferred_rows.append((best_item, rate_only, tv, inferred_tax_amount))
#                 sum_inferred += inferred_tax_amount

#             if not inferred_rows:
#                 writer.writerow({
#                     "tax_row": tax_row_name, "invoice": invoice_name,
#                     "tax_row_amount": tax_row_amount, "sum_itemwise": 0,
#                     "row_status": "no_inference_possible",
#                     "notes": "no candidates with taxable_value/amount to infer from"
#                 })
#                 continue

#             # compare sum_inferred with tax_row_amount
#             if abs(sum_inferred - tax_row_amount) > sum_tolerance:
#                 writer.writerow({
#                     "tax_row": tax_row_name, "invoice": invoice_name,
#                     "tax_row_amount": tax_row_amount, "sum_itemwise": sum_inferred,
#                     "row_status": "mismatch_inferred",
#                     "notes": f"sum inferred ({sum_inferred}) != tax_row_amount (diff {tax_row_amount - sum_inferred})"
#                 })
#                 continue

#             # sums match => apply writes (or would_set in dry_run)
#             for best_item, rate_used, tv, inferred_tax_amount in inferred_rows:
#                 target_rate_field, target_amount_field = _determine_target_fields(gst_type)
#                 if not target_rate_field:
#                     writer.writerow({
#                         "tax_row": tax_row_name, "invoice": invoice_name, "item_key": item_key,
#                         "row_status": "unknown_gst_type", "notes": gst_type
#                     })
#                     continue

#                 before_val = {
#                     "taxable_value": flt(best_item.get("taxable_value") or 0),
#                     "rate": flt(best_item.get("rate") or 0),
#                     target_rate_field: flt(best_item.get(target_rate_field) or 0),
#                     target_amount_field: flt(best_item.get(target_amount_field) or 0)
#                 }

#                 writes = {}
#                 if (not before_val["taxable_value"]) and tv:
#                     writes["taxable_value"] = tv
#                 if (not before_val[target_rate_field]) and rate_used:
#                     writes[target_rate_field] = rate_used
#                 if (not before_val[target_amount_field]) and inferred_tax_amount is not None:
#                     writes[target_amount_field] = inferred_tax_amount

#                 if writes:
#                     for k, v in writes.items():
#                         writer.writerow({
#                             "tax_row": tax_row_name, "invoice": invoice_name, "tax_row_amount": tax_row_amount,
#                             "sum_itemwise": sum_inferred, "row_status": "will_write_inferred",
#                             "item_key": item_key,
#                             "matched_item_name": best_item.get("item_name"),
#                             "matched_item_code": best_item.get("item_code"),
#                             "tax_row_gst_type": gst_type, "tax_row_rate": tax_row_rate,
#                             "inferred_taxable_value": tv,
#                             "inferred_tax_amount": inferred_tax_amount,
#                             "field_before": f"{k}:{before_val.get(k)}",
#                             "field_after": f"{k}:{v}",
#                             "action": "would_set" if dry_run else "set",
#                             "notes": "inferred_from_rate_only_blob"
#                         })
#                     if not dry_run:
#                         for k, v in writes.items():
#                             try:
#                                 frappe.db.set_value("Sales Invoice Item", best_item.get("name"), k, v, update_modified=False)
#                             except Exception as e:
#                                 writer.writerow({
#                                     "tax_row": tax_row_name, "invoice": invoice_name, "row_status": "write_error",
#                                     "item_key": item_key, "notes": str(e)
#                                 })
#                         updated += 1
#                 else:
#                     writer.writerow({
#                         "tax_row": tax_row_name, "invoice": invoice_name, "tax_row_amount": tax_row_amount,
#                         "sum_itemwise": sum_inferred, "row_status": "no_change_needed_inferred",
#                         "item_key": item_key,
#                         "matched_item_name": best_item.get("item_name"),
#                         "matched_item_code": best_item.get("item_code"),
#                         "tax_row_gst_type": gst_type, "tax_row_rate": tax_row_rate,
#                         "inferred_taxable_value": tv,
#                         "inferred_tax_amount": inferred_tax_amount,
#                         "field_before": str(before_val), "field_after": "",
#                         "action": "skipped", "notes": "target fields already present"
#                     })

#             # commit periodically when applying
#             if not dry_run and (updated and updated % batch_size == 0):
#                 frappe.db.commit()

#         # end for rows
#         if not dry_run:
#             frappe.db.commit()

#     return {"rows_examined": total, "csv": csv_path, "updated": (updated if not dry_run else 0)}

# def execute(dry_run=True, limit=None, batch_size=200, tolerance=SUM_TOLERANCE):
#     dry_run = bool(dry_run)
#     limit = int(limit) if limit else None
#     return reconstruct_item_taxes_safe(dry_run=dry_run, limit=limit, batch_size=batch_size, sum_tolerance=tolerance)

############################################################

import frappe, json, csv, os
from frappe.utils import now_datetime

def _parse_item_wise(blob):
    if not blob:
        return {}
    if isinstance(blob, dict):
        data = blob
    else:
        try:
            data = json.loads(blob)
        except Exception:
            return {}
    out = {}
    for k,v in data.items():
        if isinstance(v, dict):
            tax_amount = v.get("tax_amount")
            try:
                tax_amount = float(tax_amount) if tax_amount not in (None,"") else None
            except Exception:
                tax_amount = None
            rate = v.get("tax_rate") or v.get("rate")
            try:
                rate = float(rate) if rate not in (None,"") else None
            except Exception:
                rate = None
            net_amount = v.get("net_amount")
            try:
                net_amount = float(net_amount) if net_amount not in (None,"") else None
            except Exception:
                net_amount = None
            out[str(k).strip()] = {"tax_amount": tax_amount, "rate": rate, "net_amount": net_amount}
    return out

def execute(dry_run=True, limit=None, batch_size=100, tol_rupees=0.5):
    dry_run = bool(dry_run)
    limit_clause = f"LIMIT {int(limit)}" if limit else ""
    rows = frappe.db.sql(f"""
        SELECT name, parent as invoice, gst_tax_type, rate, tax_amount, item_wise_tax_detail
        FROM `tabSales Taxes and Charges`
        WHERE parenttype = 'Sales Invoice'
          AND IFNULL(tax_amount,0) <> 0
          AND IFNULL(item_wise_tax_detail,'') <> ''
        {limit_clause}
    """, as_dict=True)

    now = now_datetime().strftime("%Y%m%d%H%M%S")
    os.makedirs("/tmp", exist_ok=True)
    csv_path = f"/tmp/reconstruct_item_taxes_safe_{now}.csv"
    writer = csv.DictWriter(open(csv_path,"w",newline="",encoding="utf-8"),
                            fieldnames=["tax_row","invoice","gst_type","tax_row_rate","tax_row_amount","item_key","parsed_tax_amount","parsed_rate","inferred_taxable","matched_item","matched_item_code","item_taxable_before","igst_before","cgst_before","sgst_before","cess_before","writes","notes"])
    writer.writeheader()

    updated = 0
    for r in rows:
        tax_row = r["name"]
        inv_name = r["invoice"]
        gst_type = (r.get("gst_tax_type") or "").lower()
        try:
            inv = frappe.get_doc("Sales Invoice", inv_name)
        except Exception as e:
            writer.writerow({"tax_row": tax_row, "invoice": inv_name, "notes": f"invoice_read_error:{e}"})
            continue

        # build quick lookup of invoice items by item_code,item_name,idx,name
        item_map = {}
        for it in inv.get("items") or []:
            keys = []
            if it.get("item_code"):
                keys.append(str(it.get("item_code")).strip())
            if it.get("item_name"):
                keys.append(str(it.get("item_name")).strip())
            keys.append(str(it.get("idx") or ""))
            keys.append(str(it.get("name")))
            for k in keys:
                if k:
                    item_map.setdefault(k, it)

        parsed = _parse_item_wise(r.get("item_wise_tax_detail"))
        if not any(v.get("tax_amount") for v in parsed.values()):
            writer.writerow({"tax_row": tax_row, "invoice": inv_name, "notes":"no_item_amounts"})
            continue

        for key, info in parsed.items():
            p_tax_amount = info.get("tax_amount")
            p_rate = info.get("rate") or float(r.get("rate") or 0) or None
            inferred_taxable = None
            if p_tax_amount not in (None,) and p_rate:
                try:
                    inferred_taxable = (p_tax_amount * 100.0) / p_rate
                except Exception:
                    inferred_taxable = None

            # find item: exact key, substring match, or first unmatched
            matched_item = item_map.get(str(key).strip())
            if not matched_item:
                for k,it in item_map.items():
                    if str(key).strip().lower() in str(k).strip().lower():
                        matched_item = it
                        break
            if not matched_item and inferred_taxable is not None:
                # try match by taxable/amount within tolerance
                for it in inv.get("items") or []:
                    tv = float(it.taxable_value or it.amount or 0)
                    if abs(tv - inferred_taxable) <= tol_rupees:
                        matched_item = it
                        break
            if not matched_item:
                # fallback to first item with zero taxable_value
                for it in inv.get("items") or []:
                    if float(it.taxable_value or 0) == 0:
                        matched_item = it
                        break
            if not matched_item:
                writer.writerow({"tax_row": tax_row, "invoice": inv_name, "item_key": key, "notes":"no_invoice_item_found"})
                continue

            # read current per-item fields
            igst_b = float(matched_item.igst_amount or 0)
            cgst_b = float(matched_item.cgst_amount or 0)
            sgst_b = float(matched_item.sgst_amount or 0)
            cess_b = float(matched_item.cess_amount or 0)
            taxable_b = float(matched_item.taxable_value or 0)

            writes = {}
            if taxable_b == 0 and inferred_taxable is not None:
                # only set taxable if we have a good inferred_taxable within tolerance of item amount or 0.5
                writes["taxable_value"] = inferred_taxable

            if gst_type == "igst":
                if igst_b == 0 and p_tax_amount not in (None,):
                    writes["igst_amount"] = p_tax_amount
                    if p_rate:
                        writes["igst_rate"] = p_rate
            elif gst_type == "cgst":
                if cgst_b == 0 and p_tax_amount not in (None,):
                    writes["cgst_amount"] = p_tax_amount
                    if p_rate:
                        writes["cgst_rate"] = p_rate
            elif gst_type == "sgst":
                if sgst_b == 0 and p_tax_amount not in (None,):
                    writes["sgst_amount"] = p_tax_amount
                    if p_rate:
                        writes["sgst_rate"] = p_rate
            elif gst_type == "cess":
                if cess_b == 0 and p_tax_amount not in (None,):
                    writes["cess_amount"] = p_tax_amount
                    if p_rate:
                        writes["cess_rate"] = p_rate

            writer.writerow({
                "tax_row": tax_row, "invoice": inv_name, "gst_type": gst_type, "tax_row_rate": r.get("rate"), "tax_row_amount": r.get("tax_amount"),
                "item_key": key, "parsed_tax_amount": p_tax_amount, "parsed_rate": p_rate,
                "inferred_taxable": inferred_taxable, "matched_item": matched_item.get("name"),
                "matched_item_code": matched_item.get("item_code"), "item_taxable_before": taxable_b,
                "igst_before": igst_b, "cgst_before": cgst_b, "sgst_before": sgst_b, "cess_before": cess_b,
                "writes": ",".join(f"{k}={v}" for k,v in writes.items()), "notes":"will_write" if writes else "no_write_needed"
            })

            if not dry_run and writes:
                for k,v in writes.items():
                    # set on child row
                    matched_item.set(k, v)
                # mark that we'll save parent after processing all parsed keys for this tax row
                # (we'll save once per invoice per outer-loop iteration)
        # save invoice if we modified any items for it
        if not dry_run:
            try:
                inv.save(ignore_permissions=True)
                updated += 1
                if updated % batch_size == 0:
                    frappe.db.commit()
            except Exception as e:
                writer.writerow({"tax_row": tax_row, "invoice": inv_name, "notes":"invoice_save_error:"+str(e)})

    if not dry_run:
        frappe.db.commit()
    return {"csv": csv_path, "examined": len(rows), "updated": updated if not dry_run else 0}