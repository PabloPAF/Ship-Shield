"""
ShipShield FastAPI web app — BOL Authenticity Scanner + Accounts Payable mailbox.

  /          BOL scanner: upload a Bill of Lading image, extract logistics
             identifiers via LLaMA-4 Scout (Groq), run physical telemetry validation.
  /mailbox   Outlook-style AP inbox of shipping-invoice emails. The "Cross-check
             facts" button runs the same engines (email_ingestor + telemetry_validator).

Run:
    uvicorn bol_scanner_app:app --reload --port 8502
    # BOL scanner:  http://localhost:8502/
    # AP mailbox:   http://localhost:8502/mailbox
"""

import base64
import json
import os
import tomllib
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from groq import Groq
from PIL import Image, ImageEnhance

from telemetry_validator import telemetry_context_validation
from email_ingestor import ingest_eml
from vendor_ledger import check_vendor_iban
from document_hygiene import scan_attachments
from sanctions_screen import screen_counterparty
from vessel_risk import assess_vessel
from entity_verify import verify_entity
import email_forensics

# ── Constants ──────────────────────────────────────────────────────────────

TEMPLATE = Path(__file__).parent / "templates" / "bol_index.html"
MAILBOX_TEMPLATE = Path(__file__).parent / "templates" / "mailbox.html"
INBOX_DIR = Path(__file__).parent / "mailbox_inbox"
LOGO_DIR = Path(__file__).parent / "logo"
SECRETS_PATH = Path(__file__).parent / ".streamlit" / "secrets.toml"

SUPPORTED_MIME = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/tiff"}

BOL_EXTRACTION_PROMPT = """You are a maritime document expert. Extract all logistics identifiers from this Bill of Lading image.

Return ONLY a valid JSON object with these exact fields (use null for any field not found):
{
  "bol_number": "string — Bill of Lading number / reference",
  "vessel_name": "string — name of the carrying vessel",
  "mmsi": "string — 9-digit MMSI if printed on document, otherwise null",
  "imo": "string — IMO number (7 digits, may be labelled 'IMO No.'), otherwise null",
  "voyage_number": "string — voyage number or reference",
  "port_of_loading": "string — full port name where cargo was loaded",
  "port_of_discharge": "string — full port name where cargo is to be discharged",
  "bol_date": "string — date in YYYY-MM-DD format",
  "cargo_description": "string — description of goods / commodity",
  "cargo_quantity_mt": "number — cargo weight in metric tonnes, or null",
  "shipper": "string — name of the shipper / exporter",
  "consignee": "string — name of the consignee / importer",
  "notify_party": "string or null"
}"""

_CARGO_KEYWORDS = {
    "grain": ["grain", "wheat", "corn", "maize", "soy", "soybean", "rice", "barley", "oats"],
    "coal": ["coal", "coke"],
    "ore": ["iron ore", "ore", "bauxite", "scrap metal", "scrap"],
    "crude_oil": ["crude oil", "crude", "petroleum", "fuel oil", "diesel"],
    "lng": ["lng", "liquefied natural gas", "natural gas"],
    "lpg": ["lpg", "liquefied petroleum gas", "propane", "butane"],
    "cement": ["cement", "clinker"],
    "fertiliser": ["fertiliser", "fertilizer", "urea", "potash", "phosphate"],
}


# ── Helpers ────────────────────────────────────────────────────────────────

def _read_secret(key: str) -> str | None:
    val = os.environ.get(key)
    if val:
        return val
    try:
        with open(SECRETS_PATH, "rb") as f:
            secrets = tomllib.load(f)
        return secrets.get(key)
    except FileNotFoundError:
        return None


def _preprocess(image_bytes: bytes) -> bytes:
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    img = ImageEnhance.Contrast(img).enhance(1.8)
    img.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
    out = BytesIO()
    img.save(out, format="JPEG", quality=92)
    return out.getvalue()


def _map_cargo_type(description: str) -> str:
    desc = description.lower()
    for cargo_type, keywords in _CARGO_KEYWORDS.items():
        if any(kw in desc for kw in keywords):
            return cargo_type
    return description


def _extract_bol(image_bytes: bytes, api_key: str) -> dict:
    processed = _preprocess(image_bytes)
    b64 = base64.standard_b64encode(processed).decode()
    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model="meta-llama/llama-4-scout-17b-16e-instruct",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": BOL_EXTRACTION_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }],
        temperature=0.2,
        max_completion_tokens=1024,
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


# ── App ────────────────────────────────────────────────────────────────────

app = FastAPI(title="ShipShield — BOL Scanner & AP Mailbox")

# Serve the Ship-Shield logo assets (used by the mailbox button + panel header).
app.mount("/logo", StaticFiles(directory=LOGO_DIR), name="logo")


@app.get("/", response_class=HTMLResponse)
async def index():
    return TEMPLATE.read_text(encoding="utf-8")


@app.get("/health")
async def health():
    groq_key = _read_secret("GROQ_API_KEY")
    mt_key = _read_secret("MARINETRAFFIC_API_KEY")
    ais_key = _read_secret("AISSTREAM_API_KEY")
    mode = "marinetraffic" if mt_key else ("aisstream" if ais_key else "mock")
    return {
        "status": "ok",
        "groq_configured": bool(groq_key),
        "marinetraffic_configured": bool(mt_key),
        "aisstream_configured": bool(ais_key),
        "mode": mode,
    }


@app.post("/scan")
async def scan_bol(file: UploadFile = File(...)):
    content_type = (file.content_type or "").lower().split(";")[0].strip()
    if content_type not in SUPPORTED_MIME:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{content_type}'. Upload a JPEG, PNG, or WebP image.",
        )

    groq_key = _read_secret("GROQ_API_KEY")
    if not groq_key:
        raise HTTPException(status_code=503, detail="GROQ_API_KEY is not configured on the server.")

    image_bytes = await file.read()

    # Step 1 — extract BOL fields
    try:
        bol = _extract_bol(image_bytes, groq_key)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Extraction failed: {e}")

    # Step 2 — telemetry validation
    mt_key = _read_secret("MARINETRAFFIC_API_KEY")
    ais_key = _read_secret("AISSTREAM_API_KEY")
    cargo_raw = bol.get("cargo_description") or ""
    payload = {
        "mmsi": bol.get("mmsi") or "",
        "imo": bol.get("imo") or "",
        "discharge_port": bol.get("port_of_discharge") or "",
        "invoice_date": bol.get("bol_date") or "",
        "cargo_type": _map_cargo_type(cargo_raw),
        "voyage_id": bol.get("voyage_number") or "",
        "cargo_quantity_mt": bol.get("cargo_quantity_mt"),
    }

    telemetry = telemetry_context_validation(
        payload,
        marinetraffic_api_key=mt_key,
        aisstream_api_key=ais_key,
    )

    return JSONResponse({
        "bol": bol,
        "telemetry": telemetry,
        "mode": "live" if mt_key else "mock",
    })


# ── Mailbox (Accounts Payable inbox) ─────────────────────────────────────────
# A demo Outlook-style inbox of shipping-invoice emails. The "Cross-check facts"
# button posts to /mailbox/check, which runs the SAME real engines used elsewhere:
# email_ingestor.ingest_eml() for VEC header forensics + telemetry_context_validation()
# for the physical reality check.

def _load_inbox() -> list:
    with open(INBOX_DIR / "inbox.json", encoding="utf-8") as f:
        return json.load(f)


class CheckRequest(BaseModel):
    id: str


@app.get("/mailbox", response_class=HTMLResponse)
async def mailbox():
    return MAILBOX_TEMPLATE.read_text(encoding="utf-8")


@app.get("/mailbox/emails")
async def mailbox_emails():
    """Return the inbox manifest used to render the message list and invoices."""
    return JSONResponse(_load_inbox())


@app.post("/mailbox/check")
async def mailbox_check(req: CheckRequest):
    """Cross-check one email: Layer 1 (VEC headers) + Layer 2 (physical telemetry)."""
    inbox = _load_inbox()
    entry = next((e for e in inbox if e["id"] == req.id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Email id '{req.id}' not found in inbox.")

    # Layer 1 — parse the actual .eml with the real email_ingestor
    eml_path = INBOX_DIR / entry["file"]
    try:
        ingested = ingest_eml(eml_path.read_bytes())
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Email parsing failed: {e}")
    vec_flags = list(ingested.get("vec_flags", []))
    # Augment Layer 1 with look-alike domain, homoglyph and zero-width detection.
    vec_flags += email_forensics.analyze(
        ingested.get("sender", ""), ingested.get("reply_to", ""), ingested.get("subject", "")
    )
    if any(f["severity"] == "HIGH" for f in vec_flags):
        vec_risk = "HIGH"
    elif any(f["severity"] == "MEDIUM" for f in vec_flags):
        vec_risk = "MEDIUM"
    elif any(f["severity"] == "LOW" for f in vec_flags):
        vec_risk = "LOW"
    else:
        vec_risk = "CLEAN"
    vec = {"risk": vec_risk, "flags": vec_flags}

    # Layer 0 — document hygiene: scan attachments for active/hidden content
    # BEFORE trusting the document. A FAIL means quarantine.
    hygiene = scan_attachments(ingested.get("attachments", []))

    # Layer 2 — physical telemetry validation with the real engine
    mt_key = _read_secret("MARINETRAFFIC_API_KEY")
    ais_key = _read_secret("AISSTREAM_API_KEY")
    try:
        telemetry = telemetry_context_validation(
            entry["payload"], marinetraffic_api_key=mt_key, aisstream_api_key=ais_key
        )
    except TypeError:
        # tolerate engine builds without the aisstream parameter
        telemetry = telemetry_context_validation(entry["payload"], marinetraffic_api_key=mt_key)

    # Layer 3 — vendor bank-account history (hashed ledger; change-detection).
    # Returns None for documents without an IBAN (e.g. a Bill of Lading).
    bank = check_vendor_iban(entry.get("invoice", {}).get("vendor", ""),
                             entry["payload"].get("iban", ""))

    # Layer 4 — counterparty screening (sanctions / dark fleet).
    iban_country = entry["payload"].get("iban", "")[:2].upper()
    counterparty = []
    sanctions = screen_counterparty(
        vendor=entry.get("invoice", {}).get("vendor", ""),
        carrier=telemetry.get("carrier", "") or "",
        vessel_name=telemetry.get("vessel_name", "") or "",
        imo=entry["payload"].get("imo", ""),
        mmsi=entry["payload"].get("mmsi", ""),
        iban_country=iban_country,
        sanctions_api_key=_read_secret("SANCTIONS_API_KEY"),
    )
    counterparty.append(sanctions)

    # Layer 4 — counterparty existence (VAT / commercial register).
    entity = verify_entity(
        entry.get("invoice", {}).get("vendor", ""),
        invoice_date=entry["payload"].get("invoice_date", ""),
        vies_api_key=_read_secret("VIES_API_KEY"),
    )
    if entity:
        counterparty.append(entity)

    # Layer 2 (addendum) — vessel-risk enrichment (Equasis / Port State Control).
    vessel_risk = assess_vessel(
        imo=entry["payload"].get("imo", ""),
        mmsi=entry["payload"].get("mmsi", ""),
        vessel_name=telemetry.get("vessel_name", "") or "",
        equasis_api_key=_read_secret("EQUASIS_API_KEY"),
    )

    # Combined verdict — a HIGH email-header flag or a bank-account change escalates to BLOCKED
    verdict = telemetry.get("verdict", "REVIEW")
    risk = float(telemetry.get("risk_score", 0.0))
    if vec["risk"] == "HIGH":
        verdict = "BLOCKED"
        risk = max(risk, 1.0)
    elif vec["risk"] in ("MEDIUM", "LOW") and verdict == "CLEAR":
        verdict = "REVIEW"
        risk = max(risk, 0.3)
    if bank:
        if bank["status"] == "FAIL":
            verdict = "BLOCKED"
            risk = max(risk, float(bank.get("risk", 1.0)))
        elif bank["status"] == "WARN" and verdict == "CLEAR":
            verdict = "REVIEW"
            risk = max(risk, 0.3)
    if hygiene:
        if hygiene["status"] == "FAIL":
            verdict = "BLOCKED"
            risk = max(risk, 1.0)
        elif hygiene["status"] == "WARN" and verdict == "CLEAR":
            verdict = "REVIEW"
            risk = max(risk, 0.3)
    for c in counterparty:
        if c["status"] == "FAIL":
            verdict = "BLOCKED"
            risk = max(risk, float(c.get("risk", 1.0)))
        elif c["status"] == "WARN" and verdict == "CLEAR":
            verdict = "REVIEW"
            risk = max(risk, 0.3)
    if vessel_risk:
        if vessel_risk["status"] == "WARN" and verdict == "CLEAR":
            verdict = "REVIEW"
            risk = max(risk, 0.3)

    return JSONResponse({
        "id": entry["id"],
        "verdict": verdict,
        "hygiene": hygiene,
        "bank": bank,
        "vessel_risk": vessel_risk,
        "counterparty": counterparty,
        "risk_score": round(risk, 2),
        "vec": vec,
        "telemetry": telemetry,
        "mode": "live" if mt_key else "mock",
    })
