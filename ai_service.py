import os
import re
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Supported Utility Types
VALID_UTILITY_TYPES = {'Electricity', 'Water', 'Gas', 'Diesel'}


def get_empty_extraction():
    """Return canonical empty structured extraction dict."""
    return {
        "store_code": None,
        "store_name": None,
        "utility_type": None,
        "billing_period": None,
        "bill_number": None,
        "units": None,
        "unit_rate": None,
        "amount": None,
        "bill_date": None
    }


def validate_file_content(file_stream, filename):
    """
    Validate file extension and file header magic bytes without heavy dependencies.
    Supported types: JPG, JPEG, PNG, PDF.
    """
    if not filename or '.' not in filename:
        return False, "File name must include a valid extension."

    ext = filename.rsplit('.', 1)[1].lower()
    if ext not in {'jpg', 'jpeg', 'png', 'pdf'}:
        return False, f"Unsupported file extension '.{ext}'. Supported formats: JPG, JPEG, PNG, PDF."

    # Read header bytes
    header = file_stream.read(16)
    file_stream.seek(0)  # Rewind file stream

    if len(header) < 4:
        return False, "Uploaded file is empty or corrupted."

    if ext in {'jpg', 'jpeg'}:
        if not header.startswith(b'\xff\xd8\xff'):
            return False, "File content does not match a valid JPEG image."
    elif ext == 'png':
        if not header.startswith(b'\x89PNG\r\n\x1a\n'):
            return False, "File content does not match a valid PNG image."
    elif ext == 'pdf':
        if not header.startswith(b'%PDF-'):
            return False, "File content does not match a valid PDF document."

    return True, None


class AIService:
    """
    AI Extraction service with support for:
      1. DEMO MODE (100% offline, deterministic, zero external API keys required)
      2. LIVE MODE (external AI provider integration with safe error handling)
    """

    def __init__(self):
        self.mode = os.environ.get('AI_MODE', 'demo').lower()
        self.provider = os.environ.get('AI_PROVIDER', 'gemini').lower()
        self.api_key = os.environ.get('AI_API_KEY', '')
        self.model = os.environ.get('AI_MODEL', 'gemini-1.5-flash')

    def extract_bill_data(self, file_path=None, manual_inputs=None):
        """
        Extract structured bill data from an uploaded file or demo context.
        Returns a dict:
          {
            "success": bool,
            "mode": "demo" | "live",
            "raw_output": dict,
            "normalized": dict,
            "validation_notes": list,
            "is_safe_for_auto": bool,
            "error": str | None
          }
        """
        manual_inputs = manual_inputs or {}

        if self.mode == 'live':
            return self._extract_live(file_path, manual_inputs)
        else:
            return self._extract_demo(file_path, manual_inputs)

    def _extract_demo(self, file_path, manual_inputs):
        """
        Deterministic, offline extraction simulating an AI bill parser.
        Inspects filename and file contents for deterministic patterns.
        """
        result = get_empty_extraction()
        filename = os.path.basename(file_path).lower() if file_path else ''
        file_bytes = b''

        # Read sample bytes if file exists
        if file_path and os.path.isfile(file_path):
            try:
                with open(file_path, 'rb') as f:
                    file_bytes = f.read(4096).lower()
            except Exception as e:
                logger.warning(f"Demo extractor could not read {file_path}: {e}")

        # Failure / corrupt simulation
        if 'corrupt' in filename or 'fail' in filename or 'error' in filename or b'corrupt' in file_bytes:
            return {
                "success": False,
                "mode": "demo",
                "raw_output": result,
                "normalized": result,
                "validation_notes": ["AI demo service simulated an extraction failure for unreadable statement."],
                "is_safe_for_auto": False,
                "error": "Document quality insufficient for automated reading."
            }

        # Partial bill simulation (e.g. missing units / invoice number)
        if 'partial' in filename or b'partial' in file_bytes:
            result["store_code"] = "K137"
            result["store_name"] = "K137 - Westside Express"
            result["utility_type"] = "Water"
            result["amount"] = 840.50
            result["billing_period"] = manual_inputs.get('billing_period') or "September 2026"
            # units, unit_rate, bill_number left as None
            normalized, notes, is_safe = self.validate_and_normalize(result, manual_inputs)
            return {
                "success": True,
                "mode": "demo",
                "raw_output": result,
                "normalized": normalized,
                "validation_notes": notes,
                "is_safe_for_auto": is_safe,
                "error": None
            }

        # Deterministic simulation matching store and utility keywords
        if 'k144' in filename or b'k144' in file_bytes or manual_inputs.get('store_code') == 'K144':
            result["store_code"] = "K144"
            result["store_name"] = "K144 - Downtown Central Store"
            result["utility_type"] = "Electricity"
            result["billing_period"] = manual_inputs.get('billing_period') or "September 2026"
            result["bill_number"] = "BILL-K144-202609"
            result["units"] = 10040.0
            result["unit_rate"] = 0.15
            result["amount"] = 1506.00
            result["bill_date"] = "2026-09-30"

        elif 'k175' in filename or b'k175' in file_bytes or manual_inputs.get('store_code') == 'K175':
            result["store_code"] = "K175"
            result["store_name"] = "K175 - Metro Supermarket"
            result["utility_type"] = manual_inputs.get('utility_type') or "Electricity"
            result["billing_period"] = manual_inputs.get('billing_period') or "September 2026"
            result["bill_number"] = "INV-K175-202609"
            result["units"] = 8500.5
            result["unit_rate"] = 0.145
            result["amount"] = 1232.57
            result["bill_date"] = "2026-09-30"

        elif 'water' in filename or b'water' in file_bytes or manual_inputs.get('utility_type') == 'Water':
            result["utility_type"] = "Water"
            result["billing_period"] = manual_inputs.get('billing_period') or "September 2026"
            result["bill_number"] = "WTR-2026-09"
            result["units"] = 450.0
            result["unit_rate"] = 2.80
            result["amount"] = 1260.00
            result["bill_date"] = "2026-09-28"

        elif 'gas' in filename or b'gas' in file_bytes or manual_inputs.get('utility_type') == 'Gas':
            result["utility_type"] = "Gas"
            result["billing_period"] = manual_inputs.get('billing_period') or "September 2026"
            result["bill_number"] = "GAS-2026-09"
            result["units"] = 920.0
            result["unit_rate"] = 1.15
            result["amount"] = 1058.00
            result["bill_date"] = "2026-09-29"

        elif 'diesel' in filename or b'diesel' in file_bytes or manual_inputs.get('utility_type') == 'Diesel':
            result["utility_type"] = "Diesel"
            result["billing_period"] = manual_inputs.get('billing_period') or "September 2026"
            result["bill_number"] = "DSL-2026-09"
            result["units"] = 600.0
            result["unit_rate"] = 3.20
            result["amount"] = 1920.00
            result["bill_date"] = "2026-09-27"

        else:
            # General demo extraction fallback
            result["utility_type"] = manual_inputs.get('utility_type') or "Electricity"
            result["billing_period"] = manual_inputs.get('billing_period')
            # Extract reasonable values if provided or leave null
            if manual_inputs.get('units'):
                result["units"] = manual_inputs.get('units')
            if manual_inputs.get('amount'):
                result["amount"] = manual_inputs.get('amount')
            if manual_inputs.get('bill_number'):
                result["bill_number"] = manual_inputs.get('bill_number')

        normalized, notes, is_safe = self.validate_and_normalize(result, manual_inputs)
        return {
            "success": True,
            "mode": "demo",
            "raw_output": result,
            "normalized": normalized,
            "validation_notes": notes,
            "is_safe_for_auto": is_safe,
            "error": None
        }

    def _extract_live(self, file_path, manual_inputs):
        """
        Live extraction calling external provider API safely.
        Never crashes Flask and never exposes API keys.
        """
        empty = get_empty_extraction()

        if not self.api_key:
            return {
                "success": False,
                "mode": "live",
                "raw_output": empty,
                "normalized": empty,
                "validation_notes": ["AI_API_KEY environment variable is not configured for LIVE mode."],
                "is_safe_for_auto": False,
                "error": "External AI service credentials not configured. Bill marked for manual verification."
            }

        try:
            # We perform request using standard urllib to avoid extra dependencies
            import urllib.request
            import urllib.error

            endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
            
            prompt = (
                "You are a utility bill parser. Extract structured values in strict JSON with keys: "
                "store_code, store_name, utility_type, billing_period, bill_number, units, unit_rate, amount, bill_date. "
                "Return null for any field that cannot be identified with high confidence."
            )

            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": prompt}
                        ]
                    }
                ],
                "generationConfig": {
                    "responseMimeType": "application/json"
                }
            }

            req = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'},
                method='POST'
            )

            with urllib.request.urlopen(req, timeout=15) as resp:
                resp_data = json.loads(resp.read().decode('utf-8'))
                text_content = resp_data['candidates'][0]['content']['parts'][0]['text']
                raw_json = json.loads(text_content)
                normalized, notes, is_safe = self.validate_and_normalize(raw_json, manual_inputs)
                return {
                    "success": True,
                    "mode": "live",
                    "raw_output": raw_json,
                    "normalized": normalized,
                    "validation_notes": notes,
                    "is_safe_for_auto": is_safe,
                    "error": None
                }

        except Exception as e:
            # Safe catch: do not expose API keys or internal stack trace
            logger.error("Live AI extraction encountered an error: %s", type(e).__name__)
            return {
                "success": False,
                "mode": "live",
                "raw_output": empty,
                "normalized": empty,
                "validation_notes": [f"External extraction failed: {type(e).__name__}. Marked for manual verification."],
                "is_safe_for_auto": False,
                "error": "External AI processing was unavailable. Upload preserved and marked for supervisor verification."
            }

    @staticmethod
    def validate_and_normalize(data, manual_inputs=None):
        """
        Validate and sanitize extracted values against strict rules:
        - utility_type must be in {'Electricity', 'Water', 'Gas', 'Diesel'}
        - numeric fields (units, unit_rate, amount) must be non-negative numbers
        - bill_date must be a valid date
        - billing_period must be preserved or valid
        Returns: (normalized_dict, notes_list, is_safe_for_auto)
        """
        manual_inputs = manual_inputs or {}
        normalized = get_empty_extraction()
        notes = []

        if not isinstance(data, dict):
            notes.append("Malformed AI output structure; expected JSON object.")
            return normalized, notes, False

        # 1. Utility Type Validation
        raw_utility = data.get("utility_type") or manual_inputs.get("utility_type")
        if raw_utility:
            match = next((u for u in VALID_UTILITY_TYPES if u.lower() == str(raw_utility).strip().lower()), None)
            if match:
                normalized["utility_type"] = match
                notes.append(f"Utility classified as {match}")
            else:
                notes.append(f"Unrecognized utility type '{raw_utility}' rejected.")
        else:
            notes.append("Utility type not detected.")

        # 2. Store Code & Store Name
        store_code = data.get("store_code") or manual_inputs.get("store_code")
        if store_code:
            clean_code = str(store_code).strip().upper()
            normalized["store_code"] = clean_code
            notes.append(f"Store code identified as {clean_code}")
        
        store_name = data.get("store_name") or manual_inputs.get("store_name")
        if store_name:
            normalized["store_name"] = str(store_name).strip()

        # 3. Billing Period
        # Preserve manually entered period if available, otherwise check extracted
        billing_period = manual_inputs.get("billing_period") or data.get("billing_period")
        if billing_period and str(billing_period).strip():
            clean_period = str(billing_period).strip()
            # Basic format sanity check (e.g. at least 3 chars)
            if len(clean_period) >= 3:
                normalized["billing_period"] = clean_period
                notes.append(f"Billing period confirmed: {clean_period}")
            else:
                notes.append("Billing period string invalid.")
        else:
            notes.append("Billing period not detected — supervisor confirmation required.")

        # 4. Bill Number
        bill_num = data.get("bill_number") or manual_inputs.get("bill_number")
        if bill_num and str(bill_num).strip():
            normalized["bill_number"] = str(bill_num).strip()
            notes.append(f"Invoice number: {normalized['bill_number']}")
        else:
            notes.append("Invoice / bill number not detected.")

        # 5. Units (Numeric coercion and check)
        raw_units = data.get("units")
        if raw_units is None or raw_units == '':
            raw_units = manual_inputs.get("units")

        if raw_units is not None and str(raw_units).strip() != '':
            try:
                # Clean any commas or spaces
                cleaned = str(raw_units).replace(',', '').replace(' ', '').strip()
                val = float(cleaned)
                if val >= 0:
                    normalized["units"] = val
                    notes.append(f"Units consumed: {val:,.2f}")
                else:
                    notes.append(f"Negative consumption units '{raw_units}' rejected.")
            except (ValueError, TypeError):
                notes.append(f"Non-numeric units '{raw_units}' rejected.")
        else:
            notes.append("Consumption units not detected.")

        # 6. Unit Rate (Numeric coercion and check)
        raw_rate = data.get("unit_rate")
        if raw_rate is None or raw_rate == '':
            raw_rate = manual_inputs.get("unit_rate")

        if raw_rate is not None and str(raw_rate).strip() != '':
            try:
                cleaned = str(raw_rate).replace('$', '').replace('₹', '').replace(',', '').strip()
                val = float(cleaned)
                if val >= 0:
                    normalized["unit_rate"] = val
                    notes.append(f"Unit tariff rate: ${val:,.4f}")
                else:
                    notes.append(f"Negative unit rate '{raw_rate}' rejected.")
            except (ValueError, TypeError):
                notes.append(f"Non-numeric unit rate '{raw_rate}' rejected.")
        else:
            notes.append("Unit rate not detected.")

        # 7. Amount (Numeric coercion and check)
        raw_amt = data.get("amount")
        if raw_amt is None or raw_amt == '':
            raw_amt = manual_inputs.get("amount")

        if raw_amt is not None and str(raw_amt).strip() != '':
            try:
                cleaned = str(raw_amt).replace('$', '').replace('₹', '').replace(',', '').strip()
                val = float(cleaned)
                if val >= 0:
                    normalized["amount"] = val
                    notes.append(f"Gross amount: ${val:,.2f}")
                else:
                    notes.append(f"Negative amount '{raw_amt}' rejected.")
            except (ValueError, TypeError):
                notes.append(f"Non-numeric statement amount '{raw_amt}' rejected.")
        else:
            notes.append("Statement amount not detected.")

        # 8. Bill Date Validation
        raw_date = data.get("bill_date") or manual_inputs.get("bill_date")
        if raw_date and str(raw_date).strip():
            clean_date_str = str(raw_date).strip()
            parsed_date = None
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%B %d, %Y", "%b %d, %Y"):
                try:
                    parsed_date = datetime.strptime(clean_date_str, fmt).strftime("%Y-%m-%d")
                    break
                except ValueError:
                    continue
            if parsed_date:
                normalized["bill_date"] = parsed_date
                notes.append(f"Bill statement date: {parsed_date}")
            else:
                notes.append(f"Unparseable date '{clean_date_str}' rejected.")
        else:
            notes.append("Bill date not detected.")

        # Determine safety for automatic processing
        # Strict validation: all essential fields must be present and valid
        has_essential_fields = (
            normalized["utility_type"] is not None and
            normalized["billing_period"] is not None and
            normalized["amount"] is not None and
            normalized["units"] is not None
        )

        is_safe_for_auto = has_essential_fields

        return normalized, notes, is_safe_for_auto


# Global service singleton
ai_service = AIService()
