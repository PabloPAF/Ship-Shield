"""
BOL Authenticity Scanner — standalone FastAPI web app.

Accepts a Bill of Lading image, extracts logistics identifiers via
LLaMA-4 Scout (Groq), then runs physical telemetry validation against
the maritime AIS registry.

Run:
    uvicorn bol_scanner_app:app --reload --port 8501
"""

import base64
import json
import os
import tomllib
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from groq import Groq
from PIL import Image, ImageEnhance

from telemetry_validator import telemetry_context_validation

# ── Constants ──────────────────────────────────────────────────────────────

TEMPLATE = Path(__file__).parent / "templates" / "bol_index.html"
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

app = FastAPI(title="BOL Authenticity Scanner")


@app.get("/", response_class=HTMLResponse)
async def index():
    return TEMPLATE.read_text(encoding="utf-8")


@app.get("/health")
async def health():
    groq_key = _read_secret("GROQ_API_KEY")
    mt_key = _read_secret("MARINETRAFFIC_API_KEY")
    return {
        "status": "ok",
        "groq_configured": bool(groq_key),
        "marinetraffic_configured": bool(mt_key),
        "mode": "live" if mt_key else "mock",
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

    telemetry = telemetry_context_validation(payload, marinetraffic_api_key=mt_key)

    return JSONResponse({
        "bol": bol,
        "telemetry": telemetry,
        "mode": "live" if mt_key else "mock",
    })
