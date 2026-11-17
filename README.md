# Ledgerline — Invoice decision desk

A small invoice review workbench built from the prompt. Upload a text-based PDF or paste invoice text. The app extracts common invoice fields, checks required data and total arithmetic, matches a sample PO ledger, applies a $25 tolerance, checks duplicates during the current server session, and gives a reasoned decision with a control trace. Extracted fields can be corrected and re-evaluated.

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

## Deploy on Render

The `render.yaml` in this folder describes a Docker web service. Make `zamp` the root of its own Git repository, push it to GitHub, connect that repository to Render, and create a Blueprint instance. Render builds `Dockerfile` and serves the app on its assigned port. The health check is `/api/health`.

## Deliberate limits

- The included PO ledger is sample data in `app.py`; connect a real procurement source before using real invoices.
- Duplicate detection and the ledger are in memory for this demo. Duplicate history resets when the process restarts and is not shared between instances.
- Text-based PDF extraction uses `pypdf`. Image-only/scanned PDFs need OCR first; the UI reports when there is no selectable text.
- Amounts are treated as USD for the supplied sample POs. A missing or different currency is surfaced for reviewer confirmation rather than silently converted.
- The app is a review aid: exceptions route to AP review. It does not initiate payment.
