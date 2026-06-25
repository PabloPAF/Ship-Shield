# Project Context: SmartInvoiceAI + Physical Telemetry Validation Engine
> Graduation project — Maritime Logistics Invoice Fraud Detection

---

## 1. Problem Statement

**Vendor Email Compromise (VEC)** is the leading financially devastating cybercrime in maritime logistics. Attackers compromise a vendor's email account, monitor billing patterns, then inject a fraudulent invoice into an active payment thread — keeping arithmetic accurate, layout identical, and shipment details real, but swapping the bank routing number (IBAN).

**Why existing tools fail:** Baseline AI invoice parsers validate *document integrity* — math, layout, duplicate detection. They return a clean result because the document *is* structurally clean. They do not ask: *did this physical event actually happen?*

The second attack vector is **header-level VEC**: the attacker sends an email with a spoofed display name but a different reply-to domain, so any reply (including payment confirmations) goes to the attacker's inbox — the document itself never needs to be touched.

---

## 2. Solution

**SmartInvoiceAI + Physical Telemetry Validation Engine** — a context-aware fraud detection layer built on top of an open-source invoice parser.

The system operates in two complementary layers:

**Layer 1 — Email Ingestion (VEC Header Detection)**
Before a document is even extracted, the email carrying it is analysed:
- Reply-to domain vs sender domain mismatch (primary VEC indicator)
- Sender domain is a consumer email provider (impersonation signal)
- Urgency / payment-redirect keywords in subject line
- Result: risk flag attached to the whole submission before document processing begins

**Layer 2 — Physical Telemetry Validation**
After the invoice is extracted, logistics identifiers are cross-referenced against real-world AIS telemetry:
- Did the vessel actually dock at the claimed port? (MMSI / MarineTraffic AIS)
- Is the invoice dated near the actual dock date? (Case A — post/ante-dating)
- Is the cargo physically possible on this vessel type? (Case B — type incompatibility)
- Has this voyage ID already been invoiced? (Case B — ghost cargo / duplicate sale)
- Does the claimed tonnage exceed vessel capacity? (Case C — DWT overstatement)
- Was the invoice submitted suspiciously late after departure? (Case D — fake agency invoice)
- Does the IBAN match the registered carrier account? (VEC financial payload)

> Core insight: if the ship didn't dock, the payment doesn't clear.

---

## 3. Target Market

- **Primary:** Maritime logistics SMBs — freight forwarders, NVOCCs, ship agents, regional carriers
- **Geography:** EU launch (NIS2 / CRA regulatory tailwinds), global expansion
- **Size:** 500,000+ SMEs in shipping/logistics worldwide; ~80,000 in the EU
- **Pain:** Median fraud loss per incident: $75k–$500k. 75% of logistics CFOs reported at least one fraud attempt in 2023.

---

## 4. Architecture

Two entry points share the same validation core:

### Entry Point A — Streamlit dashboard (`enhanced_ui.py`)
```
[Invoice email received]
         ↓
[Tab 5 — Email Ingestion]
  - Parse .eml headers
  - Check reply-to domain vs sender domain (VEC flag)
  - Check sender on free email provider (impersonation)
  - Check urgency keywords in subject
  - Extract PDF/image attachments
  - VEC risk: HIGH / MEDIUM / LOW / CLEAN
         ↓
[Tab 1 — Invoice Extraction]
  - Preprocess image (contrast, resize)
  - LLaMA-4 Scout via Groq API (vision extraction)
  - Isolation Forest anomaly detection
  - Pydantic validation (InvoiceData, LineItem)
         ↓
[Extracted JSON payload]
  { mmsi, imo, discharge_port, invoice_date, submission_date,
    iban, cargo_type, voyage_id, cargo_quantity_mt }
         ↓
[Tab 4 — Telemetry Validation]
  → shared validation core (see below)
```

### Entry Point B — BOL Scanner web app (`bol_scanner_app.py`)
```
[User uploads Bill of Lading image]
         ↓
[POST /scan — FastAPI endpoint]
  - Preprocess image (contrast, resize)
  - LLaMA-4 Scout BOL extraction prompt
    → bol_number, vessel_name, mmsi, imo, voyage_number,
      port_of_loading, port_of_discharge, bol_date,
      cargo_description, cargo_quantity_mt, shipper, consignee
  - Cargo keyword → type mapping (grain/coal/crude_oil/lng/ore…)
         ↓
[Shared validation core — telemetry_context_validation()]
  - Port check: live MarineTraffic /portcalls API (fallback: mock registry)
  - Case A: bol_date vs dock_date (±2 days)
  - Case B: vessel_type vs cargo_type incompatibility
  - Case B: voyage_id duplicate detection
  - Case C: cargo_quantity_mt vs deadweight_tonnes
  - Case D: submission_date - dock_date > 14 days
  - IBAN: invoice vs registered_iban
         ↓
[Verdict: CLEAR / REVIEW / BLOCKED]
  - CLEAR: all checks passed
  - REVIEW: API unavailable or missing IBAN on file — manual check required
  - BLOCKED: hard mismatch detected + per-field breakdown + risk score
         ↓
[GET / — rendered in browser]
  - Verdict banner (colour-coded) + risk %
  - Extracted BOL fields table
  - Per-check breakdown
```

---

## 5. Tech Stack

| Layer | Component |
|---|---|
| Base invoice parser | SmartInvoiceAI (open source, extended) |
| LLM extraction | LLaMA-4 Scout via Groq API |
| Anomaly detection | Isolation Forest (scikit-learn) |
| Data models | Pydantic v2 (`InvoiceData`, `LineItem`) |
| Email parsing | Python `email` stdlib (RFC 2822 / MIME) |
| VEC detection | Custom `email_ingestor.py` |
| Telemetry validation | Custom `telemetry_validator.py` |
| AIS data (mock) | `maritime_registry.json` — 3 vessels keyed by MMSI |
| AIS data (live) | MarineTraffic REST API — `GET /portcalls/{api_key}` |
| Streamlit dashboard | `enhanced_ui.py` — 5-tab app (invoice, chatbot, fraud, telemetry, email) |
| BOL scanner web app | FastAPI + vanilla HTML/CSS/JS — `bol_scanner_app.py` + `templates/bol_index.html` |
| Web framework | FastAPI + Uvicorn |
| File upload | `python-multipart` |
| Configuration | `.streamlit/secrets.toml` (Groq key, MarineTraffic key) |
| Language | Python 3.10+ |

---

## 6. Files

### `email_ingestor.py`
Parses `.eml` files and flags VEC indicators before document extraction.

- `ingest_eml(bytes) → dict` — main entry point
- `_check_vec_headers(msg)` — four checks: REPLY_TO_MISMATCH (HIGH), FREE_PROVIDER_SENDER / FREE_PROVIDER_REPLY_TO (MEDIUM), IMPERSONATION (MEDIUM), URGENCY_KEYWORDS (LOW)
- `_extract_attachments(msg)` — walks MIME tree for PDF/image payloads
- `make_demo_eml(scenario)` — generates in-memory `.eml` for UI demos ("clean" | "vec_attack")

Return shape:
```python
{
    "sender": str, "subject": str, "reply_to": str, "date": str,
    "vec_risk": "HIGH" | "MEDIUM" | "LOW" | "CLEAN",
    "vec_flags": [{"severity": str, "code": str, "detail": str}],
    "attachments": [{"filename": str, "content_type": str, "bytes": bytes, "is_image": bool}],
    "error": str | None,
}
```

---

### `telemetry_validator.py`
Validates invoice logistics identifiers against AIS data.

Registry lookup key: **MMSI** (9-digit Maritime Mobile Service Identity). Falls back to IMO if MMSI is absent.

Fraud cases detected:

| Check | Skuld Case | Risk Score |
|---|---|---|
| Vessel not in registry | — | 1.0 — BLOCKED |
| Port of discharge mismatch | — | 0.95 — BLOCKED |
| Invoice date vs dock date > ±2 days | Case A | 0.85 — BLOCKED |
| Vessel type / cargo type incompatibility | Case B | 0.9 — BLOCKED |
| Voyage ID already invoiced (duplicate) | Case B | 1.0 — BLOCKED |
| Cargo quantity exceeds vessel DWT | Case C | 0.95 — BLOCKED |
| Submission > 14 days after dock date | Case D | 0.8 — BLOCKED |
| IBAN mismatch vs registered carrier | VEC | 1.0 — BLOCKED |
| API unavailable / no IBAN on file | — | 0.3 — REVIEW |
| All checks pass | — | 0.0 — CLEAR |

Live mode (API key present): port verified via MarineTraffic `/portcalls`; silently falls back to local registry on timeout/error to avoid false positives.

Return shape:
```python
{
    "is_tampered": bool,
    "verdict": "CLEAR" | "REVIEW" | "BLOCKED",
    "risk_score": float,         # 0.0–1.0
    "overall_reason": str,
    "checks": [{"field": str, "status": "PASS"|"WARN"|"FAIL", "detail": str}],
    "vessel_name": str | None,
    "carrier": str | None,
    "dock_date": str | None,
    "source": "mock_registry" | "marinetraffic_api",
}
```

---

### `maritime_registry.json`
Mock AIS registry keyed by MMSI. Three vessels with full Phase 2 enrichment.

| MMSI | Vessel | Type | DWT | Port | Registered IBAN |
|---|---|---|---|---|---|
| 244170218 | Atlantic Explorer | container | 18,000 MT | Port of Rotterdam | NL91ABNA0417164300 |
| 211456200 | Baltic Carrier | bulk_carrier | 45,000 MT | Port of Hamburg | DE89370400440532013000 |
| 224143870 | Mediterranean Star | tanker | 25,000 MT | Port of Barcelona | ES9121000418450200051332 |

Each entry also carries `invoiced_voyages` (list) for Case B duplicate detection.

---

### `enhanced_ui.py`
Five-tab Streamlit dashboard.

| Tab | Purpose |
|---|---|
| 📄 Invoice Extraction | Batch upload, LLaMA extraction, confidence scoring, data editor, CSV/JSON export |
| 🤖 Chatbot | Predefined quick questions + free-text chat with extracted invoice context |
| 🚨 Fraud Detection | Rules-based checks: duplicate numbers, high amounts, suspicious tax ratios |
| 🛰️ Telemetry Validation | AIS validation form — 10 built-in demo scenarios + manual input. Live/mock banner |
| 📧 Email Ingestion | `.eml` upload or demo scenario — header VEC analysis, attachment preview, extraction |

Tab 4 demo scenarios include all four Skuld cases plus IBAN hijack, port mismatch, unknown vessel, late submission, DWT exceeded, tanker/grain cargo mismatch.

---

### `utils.py`
Data models and shared helpers.

- `InvoiceData` — Pydantic model: invoice_number, invoice_date, due_date, vendor_name, customer_name, line_items, subtotal, tax, total_amount, currency
- `LineItem` — Pydantic model: description, quantity, unit_price, total_price
- `GroqClient` — wraps Groq API: `extract_invoice_data()`, `run_chatbot_query()`
- Image helpers: `process_image_upload()`, `process_image_url()`, `preprocess_image()`, `display_image_preview()`
- `export_to_csv(List[InvoiceData])` — exports batch to CSV

---

### `bol_scanner_app.py` + `templates/bol_index.html`
Standalone FastAPI web app for scanning a Bill of Lading image for authenticity. Runs independently of the Streamlit app — no shared session state.

Routes:
- `GET /` — serves `bol_index.html`
- `GET /health` — returns Groq/MarineTraffic key status and AIS mode
- `POST /scan` — accepts JPEG/PNG upload, returns `{ bol, telemetry, mode }` JSON

Backend (`bol_scanner_app.py`):
- `_extract_bol(image_bytes, api_key)` — BOL-specific LLaMA-4 Scout prompt; extracts bol_number, vessel_name, mmsi, imo, voyage_number, port_of_loading, port_of_discharge, bol_date, cargo_description, cargo_quantity_mt, shipper, consignee, notify_party
- `_map_cargo_type(description)` — keyword mapping from free-text cargo to telemetry validator taxonomy (grain, coal, crude_oil, lng, ore, cement, fertiliser…)
- `_read_secret(key)` — reads from `os.environ` then `.streamlit/secrets.toml` (compatible with existing key setup)

Frontend (`templates/bol_index.html`):
- Single-file, zero build step, no external dependencies
- Drag-and-drop upload zone with image preview
- Three-step progress indicator (extracting → validating → complete)
- Verdict banner: colour-coded CLEAR / REVIEW / BLOCKED + risk %
- Two-column result: extracted BOL fields table + per-check breakdown

Run:
```bash
uvicorn bol_scanner_app:app --reload --port 8502
```

---

### `app.py`
Single-invoice baseline app (original SmartInvoiceAI). Known bug: line 183 calls `export_to_csv()` with a single `InvoiceData` instead of a list. Not fixed to preserve original codebase — use `enhanced_ui.py` instead.

---

## 7. Demo Story

### Email-first flow (VEC attack):
| Step | Action | Result |
|---|---|---|
| 1 | Load VEC attack demo in Email Ingestion tab | 🚨 HIGH risk — reply-to hijack + urgency keywords flagged |
| 2 | Upload same invoice document via Invoice Extraction | LLaMA extracts clean invoice (no document anomalies) |
| 3 | Submit to Telemetry Validation with attacker's IBAN | BLOCKED — IBAN does not match registered carrier account |

### Telemetry-only flow (forged port):
| Step | Action | Result |
|---|---|---|
| 1 | Submit invoice with correct vessel, wrong port, correct IBAN | Document checks pass — baseline returns no anomalies |
| 2 | Telemetry Validation runs | BLOCKED — vessel not at claimed port on invoice date |

**Key talking point:** The document is identical across scenarios. The only difference is whether we check operational reality.

---

## 8. Integration — How the Pipeline Connects

Tab 4 and Tab 5 share session state. When an image attachment is extracted via Tab 5, it pre-fills the invoice date in the Telemetry Validation form.

Tab 4 telemetry call:
```python
payload = {
    "mmsi": "244170218",
    "discharge_port": "Port of Rotterdam",
    "invoice_date": "2026-06-20",
    "submission_date": "2026-06-23",
    "iban": "NL91ABNA0417164300",
    "cargo_type": "grain",
    "voyage_id": "VOY-2026-999",
    "cargo_quantity_mt": 15000,
}
result = telemetry_context_validation(payload, marinetraffic_api_key=api_key)
# result["verdict"] → "CLEAR" | "REVIEW" | "BLOCKED"
```

API key resolution order: `st.secrets["MARINETRAFFIC_API_KEY"]` → `os.getenv("MARINETRAFFIC_API_KEY")` → mock mode.

---

## 9. Production Upgrade Path

| Current (demo) | Production |
|---|---|
| `maritime_registry.json` (3 vessels, local) | MarineTraffic / AISHub real-time AIS feed |
| Manual field entry in Telemetry tab | Auto-extract MMSI, port, voyage ID from invoice via LLaMA prompt |
| Single registry IBAN | Live IBAN verification via banking API (SWIFT gpi / Open Banking) |
| Streamlit app | REST API — plugin for Odoo / ERPNext / QuickBooks |
| `.eml` file upload | IMAP listener on accounts payable inbox — automatic on message arrival |

---

## 10. Competitive Positioning

| Solution | Validates math | Validates physical context | SMB-native | Shipping-specific | VEC email detection |
|---|---|---|---|---|---|
| QuickBooks / Xero | Partial | ✗ | ✓ | ✗ | ✗ |
| Medius Fraud Detection | ✓ | Partial | ✗ | ✗ | ✗ |
| Abnormal Security | ✓ | Email only | ✗ | ✗ | ✓ |
| SmartInvoiceAI baseline | ✓ | ✗ | ✓ | ✗ | ✗ |
| **This product** | ✓ | **✓ AIS telemetry** | ✓ | ✓ | **✓** |

---

## 11. Regulatory Tailwinds

- **NIS2 Directive (EU):** Mandates digital supply chain security for essential entities — creates compliance demand for tools like this
- **EU Cyber Resilience Act (CRA):** Direct liability on supply chain software components — positions this as a compliance aid, not just a fraud tool

---

## 12. Pitch One-Liner

> "We stop shipping invoice fraud by validating physical reality — if the ship didn't dock, the payment doesn't clear."

---

## 13. Implementation Status

| Component | Status |
|---|---|
| `maritime_registry.json` — 3 vessels, MMSI keys, DWT, voyage history | Done |
| `telemetry_validator.py` — port, Case A–D, IBAN checks; live + mock mode | Done |
| MarineTraffic API integration — `/portcalls`, timeout fallback | Done |
| Three-way verdict — CLEAR / REVIEW / BLOCKED | Done |
| `email_ingestor.py` — VEC header detection, attachment extraction, demo factory | Done |
| `enhanced_ui.py` — 5-tab dashboard, 10 telemetry demos, email tab | Done |
| `bol_scanner_app.py` — FastAPI BOL scanner, LLaMA extraction, cargo mapping | Done |
| `templates/bol_index.html` — dark-theme drag-drop web UI, live results | Done |
| All modules — syntax valid, cross-module integration tests passing | Done |

---

## 14. Key Sources

- [SmartInvoiceAI GitHub](https://github.com/JaanuNan/SmartInvoiceAI) — base project
- Skuld P&I Club Fraud Cases A–D — maritime invoice fraud taxonomy
- Deloitte "2023 Global Fraud Report"
- ACFE "2024 Report to the Nations"
- LexisNexis Risk Solutions: "2024 Invoice Fraud in Shipping and Supply Chain"
- Lloyd's List Intelligence (March 2024)
- IBM "Cyber Security and Fraud in Maritime Logistics" (2023)
- [MarineTraffic API](https://www.marinetraffic.com/en/ais-api-services)
