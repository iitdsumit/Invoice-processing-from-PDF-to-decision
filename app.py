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
    {"po": "PO-4821", "vendor": "Northstar Office Supply", "amount": 12840.00, "currency": "USD", "received": 0.00, "open": 12840.00, "date": "2026-10-28"},
    {"po": "PO-4822", "vendor": "Northstar Office Supply", "amount": 18600.00, "currency": "USD", "received": 9300.00, "open": 9300.00, "date": "2026-11-04"},
    {"po": "PO-5110", "vendor": "Acme Industrial Parts", "amount": 7600.00, "currency": "USD", "received": 7600.00, "open": 0.00, "date": "2026-10-17"},
    {"po": "PO-5277", "vendor": "Brightline Cloud Services", "amount": 24000.00, "currency": "USD", "received": 24000.00, "open": 0.00, "date": "2026-11-01"},
    {"po": "PO-6034", "vendor": "Meridian Labs", "amount": 5200.00, "currency": "USD", "received": 5200.00, "open": 0.00, "date": "2026-10-12"},
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

    invoice_no = first(r"(?:invoice\s*(?:number|no\.?|#|id))\s*[:#-]?\s*([A-Z0-9][A-Z0-9/_-]{2,})")
    po_no = first(r"(?:purchase\s*order|P\.?O\.?)\s*(?:number|no\.?|#)?\s*[:#-]?\s*(PO[- ]?\d{3,})")
    vendor = None
    known_names = sorted({po["vendor"] for po in PURCHASE_ORDERS}, key=len, reverse=True)
    for candidate in known_names:
        if candidate.casefold() in flat.casefold():
            vendor = candidate
            break
    if not vendor and lines:
        vendor = lines[0][:100]
    date_value = first(r"(?:invoice\s*date|date\s*of\s*invoice|date)\s*[:#-]?\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})")
    total_value = first(r"(?:grand\s*total|amount\s*due|total\s*due|invoice\s*total|total)\s*[:\-]?\s*(?:USD|\$)?\s*([\(\-]?\$?\s*\d[\d,]*(?:\.\d{2})?\)?)")
    subtotal_value = first(r"(?:subtotal|sub-total)\s*[:\-]?\s*(?:USD|\$)?\s*([\d,]+(?:\.\d{2})?)")
    tax_value = first(r"(?:sales\s*tax|tax|VAT|GST)\s*(?:\([^)]*\))?\s*[:\-]?\s*(?:USD|\$)?\s*([\d,]+(?:\.\d{2})?)")
    currency = "USD" if re.search(r"\$|\bUSD\b", flat, re.I) else (re.search(r"\b(EUR|GBP|INR|CAD|AUD)\b", flat, re.I).group(1).upper() if re.search(r"\b(EUR|GBP|INR|CAD|AUD)\b", flat, re.I) else None)
    return {"invoice_number": invoice_no, "po_number": po_no, "vendor": vendor, "date": date_value, "total": float(money(total_value)) if money(total_value) is not None else None, "subtotal": float(money(subtotal_value)) if money(subtotal_value) is not None else None, "tax": float(money(tax_value)) if money(tax_value) is not None else None, "currency": currency, "lines": lines}


def evaluate(data: dict[str, Any], digest: str | None = None, override: dict[str, Any] | None = None) -> dict[str, Any]:
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
    if merged.get("subtotal") is not None and merged.get("tax") is not None and merged.get("total") is not None:
        difference = abs(Decimal(str(merged["subtotal"])) + Decimal(str(merged["tax"])) - Decimal(str(merged["total"])))
        arithmetic_ok = difference <= Decimal("0.02")
        trace.append({"name": "Amount arithmetic", "status": "pass" if arithmetic_ok else "warn", "detail": f"Subtotal + tax differs from total by ${difference:.2f}." if not arithmetic_ok else "Subtotal + tax agrees with total within $0.02."})
    else:
        arithmetic_ok = True
        trace.append({"name": "Amount arithmetic", "status": "info", "detail": "Not enough line-level totals to independently verify arithmetic."})
    po_num = re.sub(r"[ -]", "", str(merged.get("po_number") or "")).upper()
    po = next((row for row in PURCHASE_ORDERS if re.sub(r"[ -]", "", row["po"]).upper() == po_num), None)
    if not po and merged.get("vendor"):
        vendor_pos = [row for row in PURCHASE_ORDERS if row["vendor"].casefold() == str(merged["vendor"]).casefold()]
        if len(vendor_pos) == 1:
            po = vendor_pos[0]
    trace.append({"name": "Purchase order match", "status": "pass" if po else "warn", "detail": f"Matched {po['po']} for {po['vendor']}." if po else "No unique PO match. Add a PO reference or route to AP review."})
    duplicate = (digest in SEEN) if digest else False
    if not duplicate and merged.get("invoice_number") and merged.get("vendor"):
        duplicate = any(x.get("invoice_number", "").casefold() == str(merged["invoice_number"]).casefold() and x.get("vendor", "").casefold() == str(merged["vendor"]).casefold() for x in SEEN.values())
    trace.append({"name": "Duplicate check", "status": "fail" if duplicate else "pass", "detail": "Same file or vendor + invoice number was already processed." if duplicate else "No matching prior invoice found in this session."})
    total = money(merged.get("total"))
    amount_ok = False
    if po and total is not None:
        remaining = Decimal(str(po["open"]))
        delta = abs(total - remaining)
        amount_ok = delta <= TOLERANCE
        # A partial invoice can be matched to an open PO balance; large variances need a human.
        detail = f"Invoice ${total:,.2f}; PO open balance ${remaining:,.2f}; variance ${delta:,.2f} (±$25 tolerance)."
        if remaining == 0 and total <= Decimal(str(po["amount"])) and total > 0:
            detail = f"PO is fully received; invoice ${total:,.2f} is within the original PO cap of ${po['amount']:,.2f}. Confirm receipt and prior billing."
            amount_ok = False
        trace.append({"name": "PO amount & tolerance", "status": "pass" if amount_ok else "warn", "detail": detail})
    else:
        trace.append({"name": "PO amount & tolerance", "status": "info", "detail": "Amount comparison unavailable until both invoice and PO are matched."})
    if duplicate:
        decision, reason = "duplicate", "Possible duplicate detected. Do not pay until AP checks the earlier submission."
    elif missing or not date_ok or not arithmetic_ok or not po or not amount_ok:
        decision, reason = "review", "One or more controls need a person to resolve before this invoice can be approved."
    else:
        decision, reason = "approve", f"Required fields are present, checks passed, and the invoice is within the matched PO tolerance."
    return {"decision": decision, "reason": reason, "extracted": {k: merged.get(k) for k in ("invoice_number", "vendor", "date", "total", "subtotal", "tax", "currency", "po_number")}, "purchase_order": po, "trace": trace, "tolerance": float(TOLERANCE)}


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return (BASE / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/purchase-orders")
def purchase_orders() -> list[dict[str, Any]]:
    return PURCHASE_ORDERS


@app.get("/api/demo")
def demos() -> list[dict[str, Any]]:
    return [
        {"name": "Clean match", "hint": "Northstar · PO-4821 · $12,840", "text": "NORTHSTAR OFFICE SUPPLY\nINVOICE NUMBER: NS-2026-1102\nINVOICE DATE: 2026-11-02\nPURCHASE ORDER: PO-4821\nSubtotal: $12,000.00\nSales Tax: $840.00\nTotal Due: $12,840.00 USD"},
        {"name": "Split shipment", "hint": "Northstar · PO-4822 · $9,300 open", "text": "NORTHSTAR OFFICE SUPPLY\nINVOICE NUMBER: NS-2026-1138\nINVOICE DATE: 2026-11-12\nPURCHASE ORDER: PO-4822\nSubtotal: $8,690.00\nTax: $610.00\nInvoice Total: $9,300.00 USD"},
        {"name": "Over tolerance", "hint": "Acme · PO-5110 · $380 over", "text": "ACME INDUSTRIAL PARTS\nINVOICE NO: AC-9417\nINVOICE DATE: 2026-11-10\nP.O. NUMBER: PO-5110\nSubtotal: $7,980.00\nTax: $0.00\nGrand Total: $7,980.00 USD"},
        {"name": "Arithmetic mismatch", "hint": "Brightline · tax does not add up", "text": "BRIGHTLINE CLOUD SERVICES\nINVOICE NUMBER: BL-8803\nINVOICE DATE: 2026-11-12\nPO: PO-5277\nSubtotal: $10,000.00\nTax: $800.00\nTotal Due: $10,950.00 USD"},
        {"name": "Duplicate invoice", "hint": "Submit twice to see duplicate guard", "text": "MERIDIAN LABS\nINVOICE NUMBER: ML-221\nINVOICE DATE: 2026-11-10\nPURCHASE ORDER: PO-6034\nSubtotal: $4,800.00\nTax: $400.00\nTotal Due: $5,200.00 USD"},
    ]


@app.post("/api/analyze")
async def analyze(invoice: UploadFile | None = File(default=None), text: str = Form(default=""), overrides: str = Form(default="{}")) -> JSONResponse:
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
    result = evaluate(extract(text), digest, overrides_obj)
    if result["decision"] != "duplicate":
        record = result["extracted"]
        key = digest or hashlib.sha256((str(record.get("vendor")) + str(record.get("invoice_number"))).encode()).hexdigest()
        SEEN[key] = record
    return JSONResponse(result)
