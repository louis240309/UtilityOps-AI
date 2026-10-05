"""Small HTTP client for optional n8n bill-submission automation."""

import json
import logging
import os
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


def build_bill_submitted_payload(bill_id, store, bill_data):
    """Create the versioned JSON event sent to n8n (no document bytes)."""
    file_path = bill_data.get("file_path")
    return {
        "event": "bill.submitted",
        "schema_version": 1,
        "request_id": str(uuid.uuid4()),
        "callback_url": os.environ.get("UTILITYOPS_CALLBACK_URL", "").strip() or None,
        "bill": {
            "id": bill_id,
            "store": {
                "id": store["id"],
                "code": store["store_code"],
                "name": store["store_name"],
            },
            "utility_type": bill_data.get("utility_type"),
            "billing_period": bill_data.get("billing_period"),
            "bill_number": bill_data.get("bill_number"),
            "units": bill_data.get("units"),
            "unit_rate": bill_data.get("unit_rate"),
            "amount": bill_data.get("amount"),
            "bill_date": bill_data.get("bill_date"),
            "status": "RECEIVED",
            "document": {
                "attached": bool(file_path),
                "filename": os.path.basename(file_path) if file_path else None,
            },
        },
    }


def send_bill_submitted(payload):
    """POST an event to n8n; return a sanitized result and never raise."""
    webhook_url = os.environ.get("N8N_WEBHOOK_URL", "").strip()
    shared_secret = os.environ.get("N8N_WEBHOOK_SECRET") or os.environ.get("N8N_SHARED_SECRET", "")
    callback_url = os.environ.get("UTILITYOPS_CALLBACK_URL", "").strip()

    if not webhook_url or not shared_secret or not callback_url:
        return {
            "ok": False,
            "status": "not_configured",
            "message": "n8n is not configured.",
        }

    parsed_url = urlparse(webhook_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        logger.warning("n8n dispatch skipped: invalid webhook URL configuration")
        return {
            "ok": False,
            "status": "unavailable",
            "message": "n8n automation is unavailable.",
        }

    try:
        timeout = float(os.environ.get("N8N_TIMEOUT_SECONDS", "4"))
        timeout = min(max(timeout, 1.0), 8.0)
        body = json.dumps(payload, allow_nan=False).encode("utf-8")
        req = Request(
            webhook_url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {shared_secret}",
            },
            method="POST",
        )
        with urlopen(req, timeout=timeout) as response:
            if not 200 <= response.status < 300:
                logger.warning("n8n dispatch returned HTTP %s", response.status)
                return {
                    "ok": False,
                    "status": "unavailable",
                    "message": "n8n automation is unavailable.",
                }
        return {"ok": True, "status": "queued", "message": "n8n accepted the bill."}
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        # Do not log the request URL, body, or authentication header.
        logger.warning("n8n dispatch failed (%s)", type(exc).__name__)
        return {
            "ok": False,
            "status": "unavailable",
            "message": "n8n automation is unavailable.",
        }
