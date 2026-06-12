import frappe


PLATE_FIELD_CANDIDATES = (
	"license_plate",
	"plate_number",
	"number_plate",
	"registration_number",
	"vehicle_number",
	"truck_number",
	"trailer_number",
	"name",
)


def get_vehicle_plate(doc_or_values, meta=None):
	"""Resolve a registration number from any vehicle-like document."""
	if meta is None and getattr(doc_or_values, "doctype", None):
		meta = doc_or_values.meta

	for fieldname in PLATE_FIELD_CANDIDATES:
		if fieldname != "name" and meta and not meta.has_field(fieldname):
			continue

		value = doc_or_values.get(fieldname) if fieldname != "name" else doc_or_values.get("name")
		if value and str(value).strip():
			return str(value).strip()


def has_vehicle_plate_field(meta):
	return any(
		meta.has_field(fieldname)
		for fieldname in PLATE_FIELD_CANDIDATES
		if fieldname != "name"
	)


def get_vehicle_like_doctypes():
	for doctype in frappe.get_all(
		"DocType",
		filters={"istable": 0, "issingle": 0},
		pluck="name",
	):
		try:
			meta = frappe.get_meta(doctype)
		except Exception:
			continue

		if has_vehicle_plate_field(meta):
			yield doctype, meta


def get_vehicle_like_records():
	for doctype, meta in get_vehicle_like_doctypes():
		fields = ["name"]
		fields.extend(
			fieldname
			for fieldname in PLATE_FIELD_CANDIDATES
			if fieldname != "name" and meta.has_field(fieldname)
		)

		for row in frappe.get_all(doctype, fields=fields, limit_page_length=0):
			plate_number = get_vehicle_plate(row, meta=meta)
			if plate_number:
				yield frappe._dict(
					{
						"doctype": doctype,
						"name": row.name,
						"plate_number": plate_number,
					}
				)


def get_vehicle_docname_by_plate(plate_number):
	if not plate_number or not frappe.db.exists("DocType", "Vehicle"):
		return

	meta = frappe.get_meta("Vehicle")
	for fieldname in PLATE_FIELD_CANDIDATES:
		if fieldname == "name":
			vehicle_name = frappe.db.exists("Vehicle", plate_number)
		elif meta.has_field(fieldname):
			vehicle_name = frappe.db.get_value("Vehicle", {fieldname: plate_number}, "name")
		else:
			vehicle_name = None

		if vehicle_name:
			return vehicle_name
