# Copyright (c) 2026, Aakvatech and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

from time import sleep

import frappe
import requests
from frappe.model.document import Document

from csf_tz.vehicle_authority import get_vehicle_like_records
from csf_tz.csf_tz.doctype.vehicle_fine_record.vehicle_fine_record import (
	is_valid_number_plate,
	normalize_number_plate,
)

LATRA_GQL_URL = "https://rrims.latra.go.tz:8086/graphql"
LATRA_LOGIN_URL = "https://rrims.latra.go.tz:8086/user/login"
LATRA_BASIC_AUTH = "Basic bGFsaXM6MTIzNDU2Nzg="
TOKEN_EXPIRED = "TOKEN_EXPIRED"

GQL_QUERY = """
query findMyCurrentLicensesPageable($pageableParam: PageableParamInput){
  findMyCurrentLicensesPageable(pageableParam: $pageableParam){
    content{
      licenseNumber
      licenseStatus
      validFrom
      validTo
      serviceType {
        name
        licenseType { licenseTypeName }
      }
      licenseInfoDetail {
        currentLicenseApplication {
          branch { name district { districtName } }
        }
      }
      vehicle{ vehicleRegistrationNumber }
    }
  }
}
"""

OFFENCE_GQL_QUERY = """
query allMyClientOffencesPageable($pageableParam: PageableParamInput) {
  allMyClientOffencesPageable(pageableParam: $pageableParam) {
    content {
      uid
      vehicleRegistrationNumber
      offenceReferenceNumber
      offenderName
      offenceDate
      paymentStatus
      amount
      clientOffenceType
      warningDescription
      comment
      offence { uid name code compoundedAmount }
      offenceLocation { uid name }
    }
    totalElements
  }
}
"""


class LatraLicenses(Document):
	pass


@frappe.whitelist()
def update_latra_records():
	"""
	Scheduler entry point. Discovers all vehicle-like records, deduplicates
	the plates, and dispatches a background job for each unique plate.
	"""
	seen_plates = set()
	enqueued = 0
	skipped = 0
	invalid_plates = []

	vehicles = list(get_vehicle_like_records())
	frappe.logger().info(f"[LATRA] Found {len(vehicles)} total vehicle records")

	for vehicle in vehicles:
		plate = normalize_number_plate(vehicle.plate_number)

		if not plate:
			frappe.logger().warning(f"[LATRA] Could not normalize plate: {vehicle.plate_number}")
			invalid_plates.append(vehicle.plate_number)
			skipped += 1
			continue

		if not is_valid_number_plate(plate):
			frappe.logger().warning(f"[LATRA] Invalid plate format: {plate}")
			invalid_plates.append(plate)
			skipped += 1
			continue

		if plate in seen_plates:
			skipped += 1
			continue

		seen_plates.add(plate)

		frappe.enqueue(
			"csf_tz.csf_tz.doctype.latra_licenses.latra_licenses.fetch_and_update_latra_license",
			plate_number=plate,
			queue="long",
		)
		enqueued += 1

	frappe.logger().info(
		f"[LATRA] Enqueued {enqueued} licenses, skipped {skipped}, "
		f"invalid plates: {len(invalid_plates)} - {invalid_plates[:10]}"
	)
	try:
		offence_result = update_latra_offences()
	except Exception:
		offence_result = None
		frappe.log_error(
			title="LATRA: Offence Sync Failed",
			message=frappe.get_traceback(),
		)

	return {
		"message": f"Enqueued license updates for {enqueued} vehicles",
		"skipped": skipped,
		"invalid_plates": invalid_plates[:20],
		"offences": offence_result,
	}


@frappe.whitelist()
def update_latra_offences():
	plates = {
		plate
		for plate in (normalize_number_plate(v.plate_number) for v in get_vehicle_like_records())
		if plate and is_valid_number_plate(plate)
	}
	token = _get_token()
	if not token:
		frappe.log_error(title="LATRA: No Token", message="Set access_token in Latra Settings.")
		return

	data = _call_offences_graphql(token)
	if data == TOKEN_EXPIRED:
		new_token = _refresh_token(token)
		data = _call_offences_graphql(new_token) if new_token else None
	if not data:
		frappe.log_error(title="LATRA: Offence GraphQL Failed", message="Could not fetch LATRA offences.")
		return

	saved = skipped = 0
	for row in ((data.get("allMyClientOffencesPageable") or {}).get("content") or []):
		plate = normalize_number_plate(row.get("vehicleRegistrationNumber"))
		if not plate or plate not in plates:
			skipped += 1
			continue

		offence = row.get("offence") or {}
		location = row.get("offenceLocation") or {}
		values = {
			"mv_reg_number": plate,
			"offender_name": row.get("offenderName") or "",
			"offence_type": row.get("clientOffenceType") or "",
			"status": row.get("paymentStatus") or "",
			"offence": offence.get("name") or row.get("warningDescription") or "",
			"offence_date": _parse_date(row.get("offenceDate")),
			"location": location.get("name") or "",
			"reference_number": row.get("offenceReferenceNumber") or row.get("uid") or "",
			"amount": row.get("amount") or offence.get("compoundedAmount") or 0,
		}
		existing = frappe.db.get_value(
			"Latra Offence",
			{
				"mv_reg_number": values["mv_reg_number"],
				"reference_number": values["reference_number"],
				"offence_date": values["offence_date"],
				"offence": values["offence"],
			},
			"name",
		)
		if existing:
			frappe.db.set_value("Latra Offence", existing, values)
		else:
			frappe.get_doc({"doctype": "Latra Offence", **values}).insert(ignore_permissions=True)
		saved += 1

	return {"saved": saved, "skipped": skipped}


def fetch_and_update_latra_license(plate_number):
	"""
	Background job. Fetches the LATRA GraphQL response for the given plate
	and upserts the results into Latra Licenses.
	"""
	frappe.logger().info(f"[LATRA] Starting fetch for plate: {plate_number}")

	token = _get_token()
	if not token:
		frappe.log_error(
			title="LATRA: No Token",
			message="Set access_token in Latra Settings before running LATRA sync.",
		)
		return

	data = _call_graphql(token, plate_number)
	if data == TOKEN_EXPIRED:
		frappe.logger().info(f"[LATRA] Token expired for {plate_number}, refreshing...")
		new_token = _refresh_token(token)
		if not new_token:
			frappe.log_error(
				title="LATRA: Token Refresh Failed",
				message=f"Could not refresh token for {plate_number}",
			)
			return
		data = _call_graphql(new_token, plate_number)
		if data in (None, TOKEN_EXPIRED):
			frappe.log_error(
				title="LATRA: GraphQL Call Failed After Token Refresh",
				message=f"Still failed for {plate_number}",
			)
			return
	elif data is None:
		frappe.log_error(
			title="LATRA: GraphQL Call Failed",
			message=f"Got None response for {plate_number}",
		)
		return

	licenses = (
		(data.get("findMyCurrentLicensesPageable") or {}).get("content") or []
	)

	frappe.logger().info(f"[LATRA] Got {len(licenses)} licenses for {plate_number}")

	if not licenses:
		frappe.logger().warning(f"[LATRA] No licenses found in LATRA for {plate_number}")
		return

	matching_licenses = []
	for lic in licenses:
		vehicle_reg = normalize_number_plate(
			(lic.get("vehicle") or {}).get("vehicleRegistrationNumber")
		)
		license_status = (lic.get("licenseStatus") or "").strip().upper()
		if vehicle_reg == plate_number and license_status == "ACTIVE":
			matching_licenses.append(lic)

	if not matching_licenses:
		frappe.logger().warning(f"[LATRA] No ACTIVE license found for {plate_number}")
		return

	matching_licenses.sort(key=lambda d: str(d.get("validTo") or ""), reverse=True)

	records_saved = 0
	for lic in matching_licenses[:1]:
		try:
			license_number = lic.get("licenseNumber")
			if not license_number:
				frappe.logger().warning(f"[LATRA] No license number for {plate_number}")
				continue

			values = {
				"vehicle": plate_number,
				"license_number": license_number,
				"license_status": lic.get("licenseStatus") or "",
				"license_type": _get_license_type(lic),
				"service_type": (lic.get("serviceType") or {}).get("name") or "",
				"place_issued": _get_place_issued(lic),
				"issue_date": _parse_date(lic.get("validFrom")),
				"expire_date": _parse_date(lic.get("validTo")),
			}

			if frappe.db.exists("Latra Licenses", license_number):
				frappe.db.set_value("Latra Licenses", license_number, values)
				frappe.logger().info(
					f"[LATRA] Updated license {license_number} for {plate_number}"
				)
				records_saved += 1
			else:
				try:
					doc = frappe.get_doc({"doctype": "Latra Licenses", **values})
					doc.insert(ignore_permissions=True)
					frappe.logger().info(
						f"[LATRA] Inserted license {license_number} for {plate_number}"
					)
					records_saved += 1
				except frappe.exceptions.DuplicateEntryError:
					frappe.logger().warning(
						f"[LATRA] Duplicate entry for {license_number}"
					)
				except Exception:
					frappe.log_error(
						title=f"LATRA: Error saving license {license_number} for {plate_number}",
						message=frappe.get_traceback(),
					)

		except Exception:
			frappe.log_error(
				title=f"LATRA: Error processing license for {plate_number}",
				message=frappe.get_traceback(),
			)

	frappe.logger().info(
		f"[LATRA] Completed for {plate_number}: {records_saved} records saved"
	)


def _get_token():
	try:
		cached_token = frappe.cache().get_value(_token_cache_key(), expires=True)
		if cached_token:
			return str(cached_token).strip()

		token = (frappe.db.get_single_value("Latra Settings", "access_token") or "").strip()
		if token:
			frappe.cache().set_value(_token_cache_key(), token, expires_in_sec=60 * 60 * 8)
			frappe.logger().debug("[LATRA] Using existing token")
		else:
			frappe.logger().warning("[LATRA] No token found in settings")
		return token
	except Exception as e:
		frappe.log_error(
			title="LATRA: Error reading token",
			message=str(e),
		)
		return None


def _refresh_token(old_token=None):
	"""
	Log in to the LATRA API using stored credentials and save the new token.
	Returns the new token string, or None on failure.
	"""
	lock = frappe.cache().lock(
		f"{frappe.local.site}:latra_token_refresh",
		timeout=120,
		blocking_timeout=60,
	)
	with lock:
		current_token = _get_token()
		if old_token and current_token and current_token != old_token:
			return current_token
		return _refresh_token_locked()


def _refresh_token_locked():
	try:
		settings = frappe.get_single("Latra Settings")
		email = (settings.get("username") or "").strip()
		try:
			password = (settings.get_password("password") or "").strip()
		except Exception:
			password = (settings.get("password") or "").strip()
	except Exception:
		frappe.log_error(
			title="LATRA: Settings missing",
			message=frappe.get_traceback(),
		)
		return None

	if not email or not password:
		frappe.log_error(
			title="LATRA: Credentials missing",
			message="Username or password not set in Latra Settings",
		)
		return None

	try:
		frappe.logger().info("[LATRA] Attempting token refresh...")
		response = requests.post(
			LATRA_LOGIN_URL,
			data={"username": email, "password": password},
			headers={
				"Authorization": LATRA_BASIC_AUTH,
				"Content-Type": "application/x-www-form-urlencoded",
				"Accept": "application/json",
			},
			timeout=30,
		)
		if response.status_code >= 400:
			frappe.log_error(
				title="LATRA: Token refresh HTTP failed",
				message=f"HTTP {response.status_code}: {response.text[:1000]}",
			)
			return None
		ld = response.json() if response.text else {}
		d = ld.get("data") if isinstance(ld.get("data"), dict) else {}
		new_token = (
			ld.get("token")
			or ld.get("accessToken")
			or ld.get("access_token")
			or d.get("token")
			or d.get("accessToken")
			or d.get("access_token")
			or ""
		).strip()
	except Exception:
		frappe.log_error(
			title="LATRA: Token refresh failed",
			message=frappe.get_traceback(),
		)
		return None

	if new_token:
		frappe.db.set_single_value("Latra Settings", "access_token", new_token)
		frappe.cache().set_value(_token_cache_key(), new_token, expires_in_sec=60 * 60 * 8)
		frappe.logger().info("[LATRA] Token refreshed successfully")
	else:
		frappe.log_error(
			title="LATRA: No token in login response",
			message=f"Response: {ld}",
		)

	return new_token or None


def _token_cache_key():
	return f"{frappe.local.site}:latra_access_token"


def _call_offences_graphql(token):
	payload = {
		"operationName": "allMyClientOffencesPageable",
		"variables": {
			"pageableParam": {
				"first": 0,
				"size": 500,
				"sortBy": "id",
				"sortDirection": "DESC",
				"searchFields": [],
			}
		},
		"query": OFFENCE_GQL_QUERY,
	}
	response = requests.post(
		LATRA_GQL_URL,
		json=payload,
		headers={
			"Authorization": f"Bearer {token}",
			"Accept": "application/json",
			"Content-Type": "application/json",
		},
		timeout=60,
	)
	if response.status_code == 401:
		return TOKEN_EXPIRED
	response.raise_for_status()
	result = response.json()
	if result.get("errors"):
		frappe.log_error(
			title="LATRA Offence GraphQL Error",
			message=frappe.as_json(result.get("errors"))[:2000],
		)
		return None
	return result.get("data")


def _call_graphql(token, plate_number):
	"""
	POST to the LATRA GraphQL endpoint. Returns the parsed `data` dict on
	success, TOKEN_EXPIRED on a 401, or None on any other failure.
	"""
	payload = {
		"operationName": "findMyCurrentLicensesPageable",
		"variables": {
			"pageableParam": {
				"first": 0,
				"size": 200,
				"sortBy": "id",
				"sortDirection": "DESC",
				"searchFields": [],
			}
		},
		"query": GQL_QUERY,
	}
	headers = {
		"Authorization": f"Bearer {token}",
		"Accept": "application/json",
		"Content-Type": "application/json",
	}

	max_retries = 3
	response = None

	for attempt in range(max_retries):
		try:
			if attempt > 0:
				frappe.logger().info(f"[LATRA] Retry {attempt}/{max_retries-1} for {plate_number}")
				sleep(5 * attempt)

			response = requests.post(
				LATRA_GQL_URL, json=payload, headers=headers, timeout=30
			)

			if response.status_code == 401:
				frappe.logger().warning(f"[LATRA] 401 Unauthorized for {plate_number}")
				return TOKEN_EXPIRED

			response.raise_for_status()
			frappe.logger().debug(f"[LATRA] GraphQL success for {plate_number}")
			break

		except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
			if attempt < max_retries - 1:
				frappe.logger().warning(
					f"[LATRA] Connection error (attempt {attempt+1}/{max_retries}): {str(e)}"
				)
				continue
			frappe.logger().error(
				f"[LATRA] Connection timeout for {plate_number} after {max_retries} retries: {str(e)}"
			)
			return None

		except requests.exceptions.HTTPError:
			status = response.status_code if response is not None else 0
			if status in (408, 429) or status >= 500:
				if attempt < max_retries - 1:
					frappe.logger().warning(
						f"[LATRA] HTTP {status} (attempt {attempt+1}/{max_retries}), will retry"
					)
					continue
				frappe.logger().error(
					f"[LATRA] HTTP {status} for {plate_number} after {max_retries} retries"
				)
				return None
			else:
				response_text = response.text[:500] if response else ""
				frappe.log_error(
					title="LATRA API Error",
					message=f"HTTP {status} for {plate_number}: {response_text}",
				)
				return None

		except requests.exceptions.RequestException as e:
			frappe.log_error(title="LATRA API Error", message=str(e))
			return None

	if response is None:
		frappe.log_error(
			title="LATRA: No Response",
			message=f"No response for {plate_number}",
		)
		return None

	try:
		result = response.json()
		data = result.get("data")

		errors = result.get("errors")
		if errors:
			frappe.log_error(
				title="LATRA GraphQL Error",
				message=f"GraphQL errors for {plate_number}: {errors}",
			)
			return None

		return data
	except Exception:
		frappe.log_error(
			title="LATRA API: Invalid JSON",
			message=f"Non-JSON response for {plate_number}: {response.text[:500] if response else 'No response'}",
		)
		return None


def _parse_date(value):
	if not value:
		return None
	try:
		return str(value)[:10] if "T" in str(value) else str(value)
	except Exception:
		return None


def _get_place_issued(license_row):
	branch = (
		((license_row.get("licenseInfoDetail") or {}).get("currentLicenseApplication") or {})
		.get("branch")
		or {}
	)
	district = branch.get("district") or {}
	branch_name = branch.get("name") or ""
	district_name = district.get("districtName") or ""
	return f"{branch_name} ({district_name})" if branch_name and district_name else branch_name


def _get_license_type(license_row):
	license_type = (
		((license_row.get("serviceType") or {}).get("licenseType") or {}).get("licenseTypeName")
	) or ""
	return {
		"GOODSCARRYINGVEHICLE": "GCV",
		"PUBLICSERVICEVEHICLE": "PSV",
		"PRIVATEHIRE": "Private Hire",
	}.get(license_type, license_type)
