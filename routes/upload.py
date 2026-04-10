import csv
import io
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from dateutil.parser import parse as date_parse
from flask import Blueprint, current_app, g, jsonify, request

from services.pdf_parser import parse_pdf_bytes, parse_plaintext_transactions
from services.storage import Expense
from services.vendor_normalizer import normalize_vendor


upload_bp = Blueprint("upload", __name__)
ALLOWED_EXTENSIONS = {".csv", ".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

_MAX_MANUAL_AMOUNT = float(os.getenv("MAX_MANUAL_AMOUNT", "1e8"))
_MAX_VENDOR_LEN = int(os.getenv("MAX_VENDOR_LEN", "500"))
_MAX_CATEGORY_LEN = 80


def _parse_amount(raw: Any) -> float:
    if raw is None:
        raise ValueError("Missing amount")
    s = str(raw).strip()
    if not s:
        raise ValueError("Empty amount")
    s = s.replace(",", "")
    s = re.sub(r"(?i)₹|rs\.?|inr", "", s)
    for sym in ["$", "€", "£"]:
        s = s.replace(sym, "")
    s = s.strip()
    return float(s)


def _parse_date(raw: Any) -> str:
    if raw is None:
        raise ValueError("Missing date")
    s = str(raw).strip()
    if not s:
        raise ValueError("Empty date")
    dt = date_parse(s, dayfirst=True, fuzzy=True)
    return dt.strftime("%Y-%m-%d")


def _remove_duplicates(raw_expenses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unique = set()
    cleaned = []
    for e in raw_expenses:
        key = (e["amount"], e["date"], e.get("vendor"))
        if key not in unique:
            unique.add(key)
            cleaned.append(e)
    return cleaned


def _detect_and_parse_csv(file_bytes: bytes) -> List[Dict[str, Any]]:
    decoded = file_bytes.decode("utf-8-sig", errors="replace")
    if not decoded.strip():
        return []

    sample = decoded[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t", "|"])
    except Exception:
        dialect = csv.get_dialect("excel")

    reader = csv.DictReader(io.StringIO(decoded), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("CSV header not found.")

    header_map: Dict[str, str] = {}
    for h in reader.fieldnames:
        key = str(h).strip().lower()
        if key in {"amount", "total", "expense", "value"}:
            header_map[h] = "amount"
        elif key in {"date", "transaction_date", "day"}:
            header_map[h] = "date"
        elif key in {"category", "cat"}:
            header_map[h] = "category"
        elif key in {"vendor", "merchant", "description"}:
            header_map[h] = "vendor"

    if not {"amount", "date"}.issubset(set(header_map.values())):
        raise ValueError("CSV must include amount and date columns.")

    expenses = []
    for row in reader:
        if not any(v and str(v).strip() for v in row.values()):
            continue

        def get_field(field):
            for raw_h, canon in header_map.items():
                if canon == field:
                    return row.get(raw_h)
            return None

        try:
            amount = _parse_amount(get_field("amount"))
            date = _parse_date(get_field("date"))
        except Exception:
            continue

        expenses.append(
            {
                "amount": amount,
                "date": date,
                "category": get_field("category"),
                "vendor": get_field("vendor") or "Unknown Vendor",
            }
        )

    return expenses


def _categorize_and_build_expenses(user_id: str, raw_expenses: List[Dict[str, Any]], source: str, upload_url=None):
    categorizer = current_app.extensions["categorizer"]
    repo = current_app.extensions["repo"]
    historical = repo.list_expenses(user_id)
    built = []

    for e in raw_expenses:
        cat_in = e.get("category")
        if cat_in and str(cat_in).strip():
            category = str(cat_in).strip()
            if category.lower() == "uncategorized":
                category = "Others"
        else:
            vn_key = normalize_vendor(str(e.get("vendor") or "")).strip().lower()
            fb = repo.get_feedback_category(user_id, vn_key) if vn_key else None
            if fb and str(fb).strip().lower() != "uncategorized":
                category = str(fb).strip()
            else:
                category = categorizer.categorize(
                    amount=e["amount"],
                    category=None,
                    vendor=e.get("vendor"),
                    historical_expenses=historical,
                )

        built.append(
            Expense(
                user_id=user_id,
                amount=e["amount"],
                category=category,
                expense_date=e["date"],
                vendor=e.get("vendor"),
                source=source,
                upload_url=upload_url,
            )
        )

    return built


def _validate_upload(file_name, file_bytes, expected_ext):
    ext = Path(file_name or "").suffix.lower()
    allowed = ALLOWED_EXTENSIONS if expected_ext in ALLOWED_EXTENSIONS else ALLOWED_EXTENSIONS | IMAGE_EXTENSIONS
    if ext not in allowed:
        return "Unsupported file type"
    if ext != expected_ext:
        return f"Expected {expected_ext}"
    return None


def _run_tesseract(file_bytes: bytes) -> str:
    from PIL import Image
    import pytesseract

    cmd = str(current_app.config.get("TESSERACT_CMD") or "").strip()
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd

    img = Image.open(io.BytesIO(file_bytes))
    text = pytesseract.image_to_string(img) or ""
    return text


@upload_bp.route("/csv", methods=["POST"])
def upload_csv():
    user_id = g.user_id
    repo = current_app.extensions["repo"]
    file_storage = current_app.extensions.get("file_storage")

    if "file" not in request.files:
        return jsonify({"error": "Missing file"}), 400

    f = request.files["file"]
    file_bytes = f.read()
    max_b = int(current_app.config.get("MAX_UPLOAD_SIZE_BYTES", 10 * 1024 * 1024))
    if len(file_bytes) > max_b:
        return jsonify({"error": "File too large"}), 400

    error = _validate_upload(f.filename, file_bytes, ".csv")
    if error:
        return jsonify({"error": error}), 400

    try:
        raw_expenses = _detect_and_parse_csv(file_bytes)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    if not raw_expenses:
        return jsonify({"error": "No valid data"}), 400

    repo.clear_all(user_id)
    raw_expenses = _remove_duplicates(raw_expenses)

    upload_url = None
    if file_storage:
        try:
            upload_url = file_storage.save(file_bytes, f.filename)
        except Exception as e:
            print("File upload error:", e)

    expenses = _categorize_and_build_expenses(user_id, raw_expenses, "csv", upload_url)
    inserted = repo.add_expenses(user_id, expenses)
    return jsonify({"status": "ok", "inserted": inserted})


@upload_bp.route("/pdf", methods=["POST"])
def upload_pdf():
    user_id = g.user_id
    repo = current_app.extensions["repo"]
    file_storage = current_app.extensions.get("file_storage")

    if "file" not in request.files:
        return jsonify({"error": "Missing file"}), 400

    f = request.files["file"]
    file_bytes = f.read()
    max_b = int(current_app.config.get("MAX_UPLOAD_SIZE_BYTES", 10 * 1024 * 1024))
    if len(file_bytes) > max_b:
        return jsonify({"error": "File too large"}), 400

    error = _validate_upload(f.filename, file_bytes, ".pdf")
    if error:
        return jsonify({"error": error}), 400

    try:
        raw_expenses = parse_pdf_bytes(file_bytes)
    except Exception:
        return jsonify({"error": "PDF parse failed"}), 400

    if not raw_expenses:
        return jsonify({"error": "No data extracted"}), 400

    repo.clear_all(user_id)
    raw_expenses = _remove_duplicates(raw_expenses)

    upload_url = None
    if file_storage:
        try:
            upload_url = file_storage.save(file_bytes, f.filename)
        except Exception as e:
            print("File upload error:", e)

    expenses = _categorize_and_build_expenses(user_id, raw_expenses, "pdf", upload_url)
    inserted = repo.add_expenses(user_id, expenses)
    return jsonify({"status": "ok", "inserted": inserted})


@upload_bp.route("/image", methods=["POST"])
def upload_image():
    """Receipt image → Tesseract OCR → plaintext transaction parse."""
    user_id = g.user_id
    repo = current_app.extensions["repo"]
    file_storage = current_app.extensions.get("file_storage")

    if "file" not in request.files:
        return jsonify({"error": "Missing file"}), 400

    f = request.files["file"]
    file_bytes = f.read()
    ext = Path(f.filename or "").suffix.lower()
    if ext not in IMAGE_EXTENSIONS:
        return jsonify({"error": "Use PNG/JPG/WebP"}), 400

    try:
        text = _run_tesseract(file_bytes)
    except Exception as e:
        return jsonify({"error": f"OCR failed: {e!s}"}), 400

    raw_expenses = parse_plaintext_transactions(text)
    if not raw_expenses:
        return jsonify({"error": "No transactions detected in image"}), 400

    repo.clear_all(user_id)
    raw_expenses = _remove_duplicates(raw_expenses)

    upload_url = None
    if file_storage:
        try:
            upload_url = file_storage.save(file_bytes, f.filename or "receipt.png")
        except Exception:
            pass

    expenses = _categorize_and_build_expenses(user_id, raw_expenses, "ocr", upload_url)
    inserted = repo.add_expenses(user_id, expenses)
    return jsonify({"status": "ok", "inserted": inserted, "ocrPreview": text[:500]})


@upload_bp.route("/manual", methods=["POST"])
def upload_manual():
    user_id = g.user_id
    repo = current_app.extensions["repo"]
    payload = request.get_json(silent=True) or {}

    try:
        amt = payload.get("amount")
        if amt is None:
            raise ValueError("amount required")
        amount_val = _parse_amount(amt)
        if amount_val <= 0:
            raise ValueError("amount must be positive")
        if amount_val > _MAX_MANUAL_AMOUNT:
            raise ValueError("amount exceeds allowed maximum")

        vendor_raw = payload.get("vendor")
        if vendor_raw is not None and len(str(vendor_raw)) > _MAX_VENDOR_LEN:
            raise ValueError("vendor text too long")

        cat_raw = payload.get("category")
        if cat_raw is not None and len(str(cat_raw).strip()) > _MAX_CATEGORY_LEN:
            raise ValueError("category text too long")

        raw = [
            {
                "amount": amount_val,
                "date": _parse_date(payload.get("date")),
                "category": cat_raw,
                "vendor": vendor_raw,
            }
        ]
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    expenses = _categorize_and_build_expenses(user_id, raw, "manual")
    repo.add_expenses(user_id, expenses)
    return jsonify({"status": "ok"})
