# -*- coding: utf-8 -*-
# Copyright (c) 2020, Aakvatech and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import base64
from frappe.model.document import Document
import frappe
from frappe.utils import flt
import hashlib
import json
import requests
from csf_tz.custom_api import print_out
from csf_tz.vehicle_authority import get_vehicle_docname_by_plate, get_vehicle_like_records
import re
from time import sleep
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


TPF_SECRET = "irtismutDkjQBbZKEUn8hw7WqKdxld01E6HIY"


class VehicleFineRecord(Document):
    def validate(self):
        """
        Resolve the ERPNext Vehicle document linked to this fine's plate number.

        Searches all known plate-like fields (license_plate, number_plate, etc.)
        across the Vehicle doctype. If found, sets vehicle_doc; otherwise clears it.
        """
        try:
            if self.vehicle:
                vehicle_name = get_vehicle_docname_by_plate(self.vehicle)
                self.vehicle_doc = vehicle_name or None
        except Exception:
            frappe.log_error(
                title="Error in VehicleFineRecord.validate",
                message=frappe.get_traceback(),
            )


def normalize_number_plate(number_plate):
    """
    Strip non-alphanumeric characters and uppercase the result.
    Returns None if the input is empty or normalises to an empty string.
    Truncates to 7 characters (Tanzanian plate length).
    """
    if not number_plate:
        return None
    normalized = re.sub(r"[^A-Za-z0-9]", "", str(number_plate)).upper()
    normalized = normalized[:7] if len(normalized) >= 7 else normalized
    return normalized or None


def is_valid_number_plate(number_plate):
    """
    Validate the plate follows the Tanzanian format: 1-3 letters, 3 digits, 1-3 letters.
    Example: T123ABC, TZ999A
    """
    if not number_plate or len(number_plate) != 7:
        return False
    return bool(re.match(r"^[A-Z]{1,3}[0-9]{3}[A-Z]{1,3}$", number_plate))


def decode_tpf_response(result):
    if not result.get("payload"):
        return result

    payload = str(result.get("payload")).strip()
    try:
        maybe_payload = base64.b64decode(payload, validate=True).decode()
        if re.match(r"^[A-Za-z0-9+/]+=*$", maybe_payload.strip()):
            payload = maybe_payload.strip()
    except Exception:
        pass

    key = TPF_SECRET[:32].ljust(32, "\0").encode()
    iv = hashlib.sha256(TPF_SECRET.encode()).hexdigest()[:16].encode()
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(base64.b64decode(payload)) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    plain = unpadder.update(padded) + unpadder.finalize()
    return json.loads(plain.decode("utf-8"))


def check_fine_all_vehicles(batch_size=20):
    """
    Discover every vehicle-like record across all installed doctypes
    (ERPNext Vehicle, Fleet MS Truck, Fleet MS Trailers, and any future
    doctype that has a plate-like field), normalise the registration number,
    and enqueue a background fine-check for each unique, valid plate.

    Duplicate plates (same plate on multiple doctypes) are only checked once.
    The mark-as-PAID logic runs inside get_fine — no separate job is needed.
    """
    seen_plates = set()
    enqueued = 0

    for vehicle in get_vehicle_like_records():
        plate = normalize_number_plate(vehicle.plate_number)
        if not plate or not is_valid_number_plate(plate):
            continue
        if plate in seen_plates:
            continue
        seen_plates.add(plate)

        frappe.enqueue(
            "csf_tz.csf_tz.doctype.vehicle_fine_record.vehicle_fine_record.get_fine",
            number_plate=plate,
            queue="long",
        )
        enqueued += 1

    frappe.logger().info(
        f"Enqueued fine checks for {enqueued} unique vehicle-like records"
    )
    return {"message": f"Enqueued fine checks for {enqueued} unique vehicle-like records"}


@frappe.whitelist()
def get_fine(number_plate):
    """
    Query the TPF API for pending fines on the given number plate.

    Behaviour:
    - If the API returns pending transactions:
        * Create a new Vehicle Fine Record for each reference not yet in ERPNext.
        * Mark any existing PENDING records whose reference is no longer in the
          API response as PAID (they have been settled).
    - If the API returns no pending transactions:
        * Mark all PENDING records for this plate as PAID.

    Returns a list of fine reference strings that are currently pending
    according to the TPF API, or [] on any error.
    """
    number_plate = normalize_number_plate(number_plate)

    if not number_plate:
        frappe.log_error(
            title="get_fine: missing number plate",
            message="get_fine was called with an empty or None number plate",
        )
        return []

    if not is_valid_number_plate(number_plate):
        frappe.log_error(
            title="get_fine: invalid number plate",
            message=f"Skipping invalid plate: {number_plate}",
        )
        return []

    url = "https://tms.tpf.go.tz/api/OffenceCheck"
    headers = {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Origin": "https://tms.tpf.go.tz",
        "Referer": "https://tms.tpf.go.tz/",
        "Connection": "keep-alive",
    }
    payload = {"vehicle": number_plate}

    max_retries = 3
    response = None  

    for attempt in range(max_retries):
        try:
            if attempt > 0:
                sleep(5 * attempt)

            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            break 

        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt < max_retries - 1:
                continue
            frappe.logger().warning(
                f"[VehicleFine] Connection timeout for {number_plate} "
                f"after {max_retries} retries"
            )
            return []

        except requests.exceptions.HTTPError:
            status = response.status_code if response is not None else 0
            if status in (408, 429) or status >= 500:
                if attempt < max_retries - 1:
                    continue
                frappe.logger().warning(
                    f"[VehicleFine] HTTP {status} for {number_plate} "
                    f"after {max_retries} retries"
                )
                return []
            else:
                frappe.log_error(
                    title="TPF API Error",
                    message=(
                        f"HTTP {status} for {number_plate}: "
                        f"{response.text[:500] if response is not None else ''}"
                    ),
                )
                return []

        except requests.exceptions.RequestException as e:
            frappe.log_error(title="TPF API Error", message=str(e))
            return []

    if response is None:
        return []

    try:
        result = response.json()
        result = decode_tpf_response(result)
    except Exception:
        frappe.log_error(
            title="TPF API: Invalid JSON",
            message=(
                f"Non-JSON response for {number_plate}: "
                f"{response.text[:500]}"
            ),
        )
        return []

    data = result.get("pending_transactions", [])
    fine_list = []

    if data:
        fine_list = [fine.get("reference") for fine in data if fine.get("reference")]
        if not fine_list:
            return fine_list

        stale_filters = {
            "vehicle": number_plate,
            "status": ["!=", "PAID"],
            "reference": ["not in", fine_list],
        }
        for record in frappe.get_all(
            "Vehicle Fine Record", filters=stale_filters, pluck="name"
        ):
            frappe.db.set_value("Vehicle Fine Record", record, "status", "PAID")

        existing_refs = frappe.get_all(
            "Vehicle Fine Record",
            filters={"vehicle": number_plate, "reference": ["in", fine_list]},
            pluck="reference",
        )
        for fine in data:
            fine_ref = fine.get("reference")
            if not fine_ref or fine_ref in existing_refs:
                continue
            charge = fine.get("charge") or fine.get("amount")
            penalty = fine.get("penalty")
            try:
                frappe.get_doc(
                    {
                        "doctype": "Vehicle Fine Record",
                        "vehicle": number_plate,
                        "reference": fine_ref,
                        "status": fine.get("status") or "PENDING",
                        "licence": fine.get("licence"),
                        "location": fine.get("location"),
                        "officer": fine.get("officer"),
                        "charge": charge,
                        "penalty": penalty,
                        "total": fine.get("total") or (flt(charge) + flt(penalty)),
                        "offence": fine.get("offence"),
                        "issued_date": fine.get("issued_date") or fine.get("date"),
                    }
                ).insert(ignore_permissions=True)
            except frappe.exceptions.DuplicateEntryError:
                pass  
            except Exception:
                frappe.log_error(
                    title=f"Error creating fine record for {number_plate}",
                    message=frappe.get_traceback(),
                )

    else:
        for record in frappe.get_all(
            "Vehicle Fine Record",
            filters={"vehicle": number_plate, "status": ["!=", "PAID"]},
            pluck="name",
        ):
            frappe.db.set_value("Vehicle Fine Record", record, "status", "PAID")

    frappe.db.commit()
    return fine_list
