from __future__ import unicode_literals


def ensure_cost_details_for_process_suppliers(doc, method=None):
	"""Ensure each process in supplier child table has one cost detail row."""
	meta = doc.meta
	if not meta.get_field("br_process_suppliers") or not meta.get_field("br_cost_details"):
		return

	processes = {
		(row.get("br_process") or "").strip()
		for row in (doc.get("br_process_suppliers") or [])
		if (row.get("br_process") or "").strip()
	}
	if not processes:
		return

	existing = {
		(row.get("br_process") or "").strip()
		for row in (doc.get("br_cost_details") or [])
		if (row.get("br_process") or "").strip()
	}

	for process in sorted(processes - existing):
		doc.append("br_cost_details", {"br_process": process})
