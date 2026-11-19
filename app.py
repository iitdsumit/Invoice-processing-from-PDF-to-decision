from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pypdf import PdfReader

BASE = Path(__file__).parent
app = FastAPI(title="Ledgerline | Invoice decision desk")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

# A deliberately small, transparent procurement ledger makes the demo useful out of the box.
PURCHASE_ORDERS = [
    {"po": "PO-4821", "vendor": "Northstar Office Supply", "amount": 12840.00, "currency": "USD", "billed": 0.00, "paid": 0.00, "date": "2026-10-28", "approved": True},
    {"po": "PO-4822", "vendor": "Northstar Office Supply", "amount": 18600.00, "currency": "USD", "billed": 9300.00, "paid": 9300.00, "date": "2026-11-04", "approved": True},
    {"po": "PO-5110", "vendor": "Acme Industrial Parts", "amount": 7600.00, "currency": "USD", "billed": 7600.00, "paid": 7600.00, "date": "2026-10-17", "approved": True},
    {"po": "PO-5277", "vendor": "Brightline Cloud Services", "amount": 24000.00, "currency": "USD", "billed": 24000.00, "paid": 24000.00, "date": "2026-11-01", "approved": True},
    {"po": "PO-6034", "vendor": "Meridian Labs", "amount": 5200.00, "currency": "USD", "billed": 5200.00, "paid": 5200.00, "date": "2026-10-12", "approved": True},
]
# Sample historical invoice/payment records support explicit duplicate and settled-charge checks.
PAYMENT_HISTORY = [
    {"vendor": "Northstar Office Supply", "invoice_number": "NS-2026-1001", "po_number": "PO-4822", "amount": 9300.00, "status": "paid"},
    {"vendor": "Acme Industrial Parts", "invoice_number": "AC-9001", "po_number": "PO-5110", "amount": 7600.00, "status": "paid"},
    {"vendor": "Brightline Cloud Services", "invoice_number": "BL-8700", "po_number": "PO-5277", "amount": 24000.00, "status": "paid"},
    {"vendor": "Meridian Labs", "invoice_number": "ML-221", "po_number": "PO-6034", "amount": 5200.00, "status": "paid"},
]
SEEN: dict[str, dict[str, Any]] = {}
TOLERANCE = Decimal("25.00")


def money(value: Any) -> Decimal | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^\d.,()\-]", "", str(value)).strip()
    if not cleaned:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "") if cleaned.rfind(".") > cleaned.rfind(",") else cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        tail = cleaned.rsplit(",", 1)[1]
        cleaned = cleaned.replace(",", ".") if len(tail) == 2 else cleaned.replace(",", "")
    try:
        amount = Decimal(cleaned)
        return -amount if negative else amount
    except InvalidOperation:
        return None


def extract(text: str) -> dict[str, Any]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    flat = "\n".join(lines)

    def first(pattern: str, flags: int = re.I) -> str | None:
        match = re.search(pattern, flat, flags)
        return match.group(1).strip(" #:\t") if match else None

    invoice_no = first(r"(?:^|\n)\s*(?:invoice\s*(?:number|no\.?|#|id))\s*[:#-]?\s*([A-Z0-9][A-Z0-9/_-]{2,})")
    po_no = first(r"(?:^|\n)\s*(?:purchase\s*order|P\.?O\.?)\s*(?:number|no\.?|#)?\s*[:#-]?\s*(PO[- ]?\d{3,})")
    vendor = None
    known_names = sorted({po["vendor"] for po in PURCHASE_ORDERS}, key=len, reverse=True)
    for candidate in known_names:
        if candidate.casefold() in flat.casefold():
            vendor = candidate
            break
    if not vendor and lines:
        vendor = lines[0][:100]
    date_value = first(r"(?:^|\n)\s*(?:invoice\s*date|date\s*of\s*invoice|date)\s*[:#-]?\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})")
    total_value = first(r"(?:^|\n)\s*(?:grand\s*total|amount\s*due|total\s*due|invoice\s*total|total)\s*[:\-]?\s*(?:USD|\$)?\s*([\(\-]?\$?\s*\d[\d,]*(?:\.\d{2})?\)?)")
    subtotal_value = first(r"(?:^|\n)\s*(?:subtotal|sub-total)\s*[:\-]?\s*(?:USD|\$)?\s*([\d,]+(?:\.\d{2})?)")
    tax_value = first(r"(?:^|\n)\s*(?:sales\s*tax|tax|VAT|GST)\s*(?:\([^)]*\))?\s*[:\-]?\s*(?:USD|\$)?\s*([\d,]+(?:\.\d{2})?)")
    currency = "USD" if re.search(r"\$|\bUSD\b", flat, re.I) else (re.search(r"\b(EUR|GBP|INR|CAD|AUD)\b", flat, re.I).group(1).upper() if re.search(r"\b(EUR|GBP|INR|CAD|AUD)\b", flat, re.I) else None)
    adjustment_match = re.search(r"(discount|shipping|freight|handling|fees?)\s*[:\-]?\s*(?:USD|\$)?\s*([\(\-]?\$?\s*\d[\d,]*(?:\.\d{2})?\)?)", flat, re.I)
    adjustments_value = money(adjustment_match.group(2)) if adjustment_match else Decimal("0")
    if adjustment_match and adjustment_match.group(1).casefold() == "discount" and adjustments_value is not None:
        adjustments_value = -abs(adjustments_value)
    # Capture common explicit item extensions: "Widget 2 x $10.00 $20.00" or "Widget $20.00".
    line_amounts = []
    for line in lines:
        if re.search(r"\b(subtotal|tax|vat|gst|total|amount due|invoice|discount|shipping|freight)\b", line, re.I):
            continue
        # Avoid treating invoice IDs, PO IDs, and dates as monetary values.
        amounts = re.findall(r"(?:\$|\bUSD\s*)\(?-?\d[\d,]*(?:\.\d{2})?\)?|\(?-?\d[\d,]+\.\d{2}\)?", line, re.I)
        if amounts:
            parsed = money(amounts[-1])
            if parsed is not None:
                line_amounts.append(float(parsed))
    return {"invoice_number": invoice_no, "po_number": po_no, "vendor": vendor, "date": date_value, "total": float(money(total_value)) if money(total_value) is not None else None, "subtotal": float(money(subtotal_value)) if money(subtotal_value) is not None else None, "tax": float(money(tax_value)) if money(tax_value) is not None else None, "adjustments": float(adjustments_value) if adjustments_value is not None else 0.0, "currency": currency, "line_amounts": line_amounts, "lines": lines}


def evaluate(data: dict[str, Any], digest: str | None = None, override: dict[str, Any] | None = None, ignore_key: str | None = None) -> dict[str, Any]:
    merged = {**data, **(override or {})}
    trace: list[dict[str, str]] = []
    missing = [key for key in ("invoice_number", "vendor", "date", "total") if not merged.get(key)]
    trace.append({"name": "Required fields", "status": "fail" if missing else "pass", "detail": "Missing: " + ", ".join(missing) if missing else "Invoice number, vendor, date and total are present."})
    date_text = str(merged.get("date") or "")
    try:
        parsed_date = date.fromisoformat(date_text.replace("/", "-")) if re.match(r"^\d{4}[-/]", date_text) else None
        if parsed_date is None and date_text:
            for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%d-%m-%Y"):
                try:
                    parsed_date = date.fromisoformat(__import__("datetime").datetime.strptime(date_text, fmt).date().isoformat())
                    break
                except ValueError:
                    pass
        date_ok = parsed_date is not None and parsed_date <= date.today()
    except ValueError:
        parsed_date, date_ok = None, False
    trace.append({"name": "Date check", "status": "pass" if date_ok else "warn", "detail": f"Invoice date {date_text} is valid." if date_ok else "Date is missing, unreadable, or in the future; confirm before posting."})
    total = money(merged.get("total")); subtotal = money(merged.get("subtotal")); tax = money(merged.get("tax")); adjustments = money(merged.get("adjustments")) or Decimal("0")
    arithmetic_checks = []
    if subtotal is not None and tax is not None and total is not None:
        difference = abs(subtotal + tax + adjustments - total)
        arithmetic_checks.append((difference, "Subtotal + tax + adjustments"))
    line_amounts = merged.get("line_amounts") or []
    if subtotal is not None and line_amounts:
        line_sum = sum((money(x) or Decimal("0")) for x in line_amounts)
        arithmetic_checks.append((abs(line_sum - subtotal), "Line items vs subtotal"))
    if arithmetic_checks:
        difference, check_name = max(arithmetic_checks, key=lambda item: item[0])
        arithmetic_ok = all(item[0] <= Decimal("0.02") for item in arithmetic_checks)
        detail = " ; ".join(f"{name} difference ${delta:.2f}" for delta, name in arithmetic_checks)
        trace.append({"name": "Amount arithmetic", "status": "pass" if arithmetic_ok else "fail", "detail": detail + ("; all checks are within $0.02." if arithmetic_ok else "; correct the invoice values before approval.")})
    else:
        arithmetic_ok = True
        trace.append({"name": "Amount arithmetic", "status": "info", "detail": "No complete set of line items, subtotal, tax, and total was available for an independent arithmetic check."})
    po_num = re.sub(r"[ -]", "", str(merged.get("po_number") or "")).upper()
    po = next((row for row in PURCHASE_ORDERS if re.sub(r"[ -]", "", row["po"]).upper() == po_num), None)
    if not po and merged.get("vendor"):
        vendor_pos = [row for row in PURCHASE_ORDERS if row["vendor"].casefold() == str(merged["vendor"]).casefold()]
        if len(vendor_pos) == 1:
            po = vendor_pos[0]
    vendor_known = bool(merged.get("vendor") and any(row["vendor"].casefold() == str(merged["vendor"]).casefold() and row["approved"] for row in PURCHASE_ORDERS))
    trace.append({"name": "Vendor & purchase order", "status": "pass" if po and vendor_known else "warn", "detail": f"Approved vendor {po['vendor']} matched to {po['po']}." if po and vendor_known else ("Vendor is not in the approved vendor ledger." if not vendor_known else "No unique PO match. Add a PO reference or route to AP review." )})
    invoice_no = str(merged.get("invoice_number") or "").strip().casefold(); vendor_name = str(merged.get("vendor") or "").strip().casefold()
    repeated_history = next((row for row in PAYMENT_HISTORY if row["invoice_number"].casefold() == invoice_no and row["vendor"].casefold() == vendor_name), None)
    duplicate = ((digest in SEEN and digest != ignore_key) if digest else False) or bool(repeated_history)
    if not duplicate and invoice_no and vendor_name:
        duplicate = any(key != ignore_key and str(x.get("invoice_number") or "").casefold() == invoice_no and str(x.get("vendor") or "").casefold() == vendor_name for key, x in SEEN.items())
    trace.append({"name": "Duplicate & payment history", "status": "fail" if duplicate else "pass", "detail": (f"Invoice number matches a historical {repeated_history['status']} invoice ({repeated_history['invoice_number']}, ${repeated_history['amount']:,.2f})." if repeated_history else "Same file or vendor + invoice number was already submitted in this session." if duplicate else "No matching historical payment or prior submission found." )})
    amount_ok = False
    po_balance = None
    if po and total is not None:
        amount = Decimal(str(po["amount"])); historical_billed = Decimal(str(po["billed"])); session_reserved = sum((money(x.get("total")) or Decimal("0") for key, x in SEEN.items() if key != ignore_key and str(x.get("po_number") or "").casefold() == str(po["po"]).casefold()), Decimal("0"))
        po_balance = max(Decimal("0"), amount - historical_billed - session_reserved)
        within_contract = total <= amount + TOLERANCE
        within_balance = total <= po_balance + TOLERANCE
        amount_ok = within_contract and within_balance and po_balance > 0
        detail = f"Invoice ${total:,.2f}; PO cap ${amount:,.2f}; historical billed ${historical_billed:,.2f} (paid ${po['paid']:,.2f}); other session submissions reserved ${session_reserved:,.2f}; remaining ${po_balance:,.2f}; tolerance ±${TOLERANCE:.2f}."
        if total > amount + TOLERANCE:
            detail += " Invoice exceeds the approved PO/contract cap."
        elif total > po_balance + TOLERANCE:
            detail += " Invoice exceeds the remaining unbilled balance."
        elif po_balance == 0:
            detail += " No unbilled PO balance remains."
        trace.append({"name": "PO cap & remaining balance", "status": "pass" if amount_ok else "fail", "detail": detail})
    else:
        trace.append({"name": "PO cap & remaining balance", "status": "info", "detail": "Amount comparison unavailable until invoice amount and a unique PO are matched."})
    currency_ok = bool(po and merged.get("currency") and str(merged.get("currency")).upper() == po["currency"])
    trace.append({"name": "Currency check", "status": "pass" if currency_ok else "warn", "detail": f"Invoice currency matches PO currency ({po['currency']})." if currency_ok else f"Could not confirm invoice currency against the PO ({po['currency'] if po else 'PO unavailable'})."})
    if duplicate:
        decision, reason = "duplicate", "Possible duplicate detected. Do not pay until AP checks the earlier submission."
    elif missing or not date_ok or not arithmetic_ok or not po or not vendor_known or not amount_ok or not currency_ok:
        decision, reason = "review", "One or more controls need a person to resolve before this invoice can be approved."
    else:
        decision, reason = "approve", "Required fields and arithmetic checks passed; approved vendor, currency, and remaining PO balance are confirmed. Ready for AP approval."
    po_view = {**po, "session_reserved": float(session_reserved) if po and total is not None else 0.0, "remaining": float(po_balance) if po_balance is not None else max(0, po["amount"] - po["billed"])} if po else None
    return {"decision": decision, "reason": reason, "extracted": {k: merged.get(k) for k in ("invoice_number", "vendor", "date", "total", "subtotal", "tax", "adjustments", "currency", "po_number", "line_amounts")}, "purchase_order": po_view, "trace": trace, "tolerance": float(TOLERANCE), "payment_history": PAYMENT_HISTORY}


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return (BASE / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/purchase-orders")
def purchase_orders() -> list[dict[str, Any]]:
    result = []
    for row in PURCHASE_ORDERS:
        reserved = sum(
            (
                money(item.get("total")) or Decimal("0")
                for item in SEEN.values()
                if str(item.get("po_number") or "").casefold() == row["po"].casefold()
            ),
            Decimal("0"),
        )
        remaining = max(
            Decimal("0"),
            Decimal(str(row["amount"])) - Decimal(str(row["billed"])) - reserved,
        )
        result.append({**row, "session_reserved": float(reserved), "remaining": float(remaining)})
    return result


@app.get("/api/demo")
def demos() -> list[dict[str, Any]]:
    return [
        {"name": "Clean match", "hint": "Northstar · PO-4821 · $12,840", "text": "NORTHSTAR OFFICE SUPPLY\nINVOICE NUMBER: NS-2026-1102\nINVOICE DATE: 2026-11-02\nPURCHASE ORDER: PO-4821\nSubtotal: $12,000.00\nSales Tax: $840.00\nTotal Due: $12,840.00 USD"},
        {"name": "Split shipment", "hint": "Northstar · PO-4822 · $9,300 open", "text": "NORTHSTAR OFFICE SUPPLY\nINVOICE NUMBER: NS-2026-1138\nINVOICE DATE: 2026-11-12\nPURCHASE ORDER: PO-4822\nSubtotal: $8,690.00\nTax: $610.00\nInvoice Total: $9,300.00 USD"},
        {"name": "Over tolerance", "hint": "Acme · PO-5110 · $380 over", "text": "ACME INDUSTRIAL PARTS\nINVOICE NO: AC-9417\nINVOICE DATE: 2026-11-10\nP.O. NUMBER: PO-5110\nSubtotal: $7,980.00\nTax: $0.00\nGrand Total: $7,980.00 USD"},
        {"name": "Arithmetic mismatch", "hint": "Brightline · tax does not add up", "text": "BRIGHTLINE CLOUD SERVICES\nINVOICE NUMBER: BL-8803\nINVOICE DATE: 2026-11-12\nPO: PO-5277\nSubtotal: $10,000.00\nTax: $800.00\nTotal Due: $10,950.00 USD"},
        {"name": "Previously paid", "hint": "Meridian · invoice already settled", "text": "MERIDIAN LABS\nINVOICE NUMBER: ML-221\nINVOICE DATE: 2026-11-10\nPURCHASE ORDER: PO-6034\nSubtotal: $4,800.00\nTax: $400.00\nTotal Due: $5,200.00 USD"},
        {"name": "Over PO cap", "hint": "Acme · request exceeds approved amount", "text": "ACME INDUSTRIAL PARTS\nINVOICE NO: AC-9418\nINVOICE DATE: 2026-11-10\nP.O. NUMBER: PO-5110\nSubtotal: $7,980.00\nTax: $0.00\nGrand Total: $7,980.00 USD"},
    ]


@app.post("/api/analyze")
async def analyze(invoice: UploadFile | None = File(default=None), text: str = Form(default=""), overrides: str = Form(default="{}"), revision: bool = Form(default=False)) -> JSONResponse:
    if invoice:
        if not invoice.filename or not invoice.filename.lower().endswith(".pdf"):
            raise HTTPException(415, "Upload a PDF invoice.")
        content = await invoice.read()
        if len(content) > 15 * 1024 * 1024:
            raise HTTPException(413, "PDF must be smaller than 15 MB.")
        digest = hashlib.sha256(content).hexdigest()
        try:
            reader = PdfReader(io.BytesIO(content), strict=False)
            if reader.is_encrypted:
                raise HTTPException(422, "This PDF is password protected. Export an unlocked copy and try again.")
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(422, "Could not read this PDF. Try exporting it again or paste its text below.") from exc
    else:
        digest = None
    if not text.strip():
        raise HTTPException(422, "No selectable text was found. This may be a scanned invoice; paste its text in the review panel or run OCR first.")
    try:
        overrides_obj = json.loads(overrides or "{}")
        if not isinstance(overrides_obj, dict):
            overrides_obj = {}
    except json.JSONDecodeError:
        overrides_obj = {}
    extracted = extract(text)
    original_key = digest or hashlib.sha256((str(extracted.get("vendor")) + str(extracted.get("invoice_number"))).encode()).hexdigest()
    ignore_key = original_key if revision and original_key in SEEN else None
    result = evaluate(extracted, digest, overrides_obj, ignore_key)
    result["submission_key"] = ignore_key or original_key
    if result["decision"] != "duplicate":
        record = result["extracted"]
        key = ignore_key or digest or hashlib.sha256((str(record.get("vendor")) + str(record.get("invoice_number"))).encode()).hexdigest()
        SEEN[key] = record
    return JSONResponse(result)
