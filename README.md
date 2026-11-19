# Ledgerline: invoice decision desk

Ledgerline is a small invoice review workbench. Upload a text-based PDF or paste invoice text to see extracted fields, validation results, PO and payment history, and a reasoned decision. AP reviewers can correct extracted fields and rerun the checks.

## Run locally

Python 3.12 or newer is recommended.

```powershell
cd zamp
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app:app --reload
```

Open <http://127.0.0.1:8000>.

## What the demo checks

- Required invoice fields, date, vendor approval, currency, and unique PO matching.
- Line item to subtotal and subtotal, tax, adjustments to total arithmetic when the source includes enough values.
- Exact file and vendor plus invoice number duplicates, including the sample historical payment records.
- Invoice total against the PO cap, historical billed and paid amounts, other submissions in the current session, and remaining unbilled balance, with a $25 tolerance.
- A visible control trace with reasons for approval, AP review, or duplicate/payment-history flags.

The app does not initiate payment. Exceptions and incomplete checks require AP review.

## Deploy on Render

The `render.yaml` describes a Docker web service. Make `zamp` the root of its own Git repository, push it to GitHub, connect that repository to Render, and create a Blueprint instance. Render builds `Dockerfile` and serves the app on its assigned port. The health check is `/api/health`.

## Demo limits

- The PO and payment ledger is sample data in `app.py`; connect a real procurement and accounts payable system before using real invoices.
- Prior PO balances and duplicate history are held in memory. New submissions reserve PO balance during the current session; records do not persist across restarts or coordinate across multiple instances.
- PDF extraction uses `pypdf`. Image-only/scanned PDFs need OCR first; the app reports when there is no selectable text.
- Line item arithmetic can only be checked when amounts are clearly present in the invoice text. Ambiguous layouts need reviewer confirmation.
- Amounts are compared in USD for the supplied POs. Missing or different currency routes to review; the app does not convert currencies.
