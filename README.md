# ShipShield — Physical Telemetry Validation Engine

A maritime logistics invoice fraud detection system that combines AI-powered document extraction with a physical reality check layer. While standard tools validate *document structure*, ShipShield validates *whether the physical event actually happened* — cross-referencing invoice logistics identifiers against live AIS vessel tracking data.

ShipShield is built on top of the open-source SmartInvoiceAI invoice parser (credited under License), extended with a maritime telemetry validation layer and an Accounts Payable mailbox.

[![Try Live Demo](https://img.shields.io/badge/🚀_Try_Live_Demo-Click_Here-FF4B4B?style=for-the-badge&logo=streamlit&logoColor=white)](https://smartinvoiceai.streamlit.app/)

---

## What It Does

Maritime logistics invoices are a prime target for **Vendor Email Compromise (VEC)** fraud. An attacker compromises a vendor's email, monitors active payment threads, and injects a fraudulent invoice — structurally identical to the real one, with only the IBAN (bank account number) swapped. Document-level tools return no anomalies because the document *is* clean.

This system catches the fraud at two layers:

**Layer 1 — Email Header Analysis**
Before the invoice is even opened, the email carrying it is parsed for VEC indicators:
- Reply-to domain differs from sender domain (primary attack pattern)
- Sender using a consumer email provider (Gmail, Yahoo) while claiming to be a shipping company
- Urgency / payment-redirect language in the subject line

**Layer 2 — Physical Telemetry Validation**
After extraction, the invoice's logistics claims are verified against AIS vessel data:
- Did the vessel actually dock at the claimed port? (verified via MarineTraffic AIS)
- Is the invoice date consistent with the dock date? (Skuld Case A — post/ante-dating)
- Is the cargo physically compatible with this vessel type? (Skuld Case B — tanker can't carry grain)
- Has this voyage already been invoiced? (Skuld Case B — ghost cargo / duplicate sale)
- Does the claimed cargo tonnage fit within vessel capacity? (Skuld Case C — DWT overstatement)
- Was the invoice submitted suspiciously late? (Skuld Case D — fake agency invoice)
- Does the IBAN match the registered carrier bank account? (VEC financial payload)

> "If the ship didn't dock, the payment doesn't clear."

---

## Features

- **B/L scanner web app** — standalone FastAPI app: drag-drop a Bill of Lading image, get an instant authenticity verdict
- **5-tab Streamlit dashboard** — extraction, chatbot, fraud detection, telemetry validation, email ingestion
- **LLaMA-4 Scout extraction** — vision-capable LLM via Groq API; multilingual (8+ languages); B/L-specific prompt extracts vessel, ports, cargo, tonnage
- **Isolation Forest anomaly detection** — unsupervised ML on invoice amount patterns
- **Skuld Cases A–D** — four maritime fraud patterns from P&I Club case files
- **VEC email detection** — `.eml` upload, IMAP live-mailbox fetch, or demo scenario — header forensics and attachment extraction
- **AISStream live telemetry** — real-time vessel position check via WebSocket (free tier); falls back to mock registry
- **MarineTraffic AIS integration** — historical port call verification; graceful fallback to mock registry
- **Three-way verdict** — CLEAR / REVIEW (API unavailable) / BLOCKED with per-field breakdown and risk score
- **10 built-in demo scenarios** in the Telemetry tab covering every fraud case
- **Batch invoice processing** — multi-upload with per-image progress tracking
- **Data export** — CSV and JSON with one click
- **Chatbot** — natural language queries over extracted invoice data

---

## Tech Stack

| Layer | Technology |
|---|---|
| Streamlit dashboard | Streamlit |
| B/L scanner web app | FastAPI + Uvicorn + vanilla HTML/CSS/JS |
| LLM extraction | LLaMA-4 Scout via Groq API |
| Anomaly detection | Isolation Forest (scikit-learn) |
| Data validation | Pydantic v2 |
| Email parsing | Python `email` stdlib (RFC 2822 / MIME) |
| IMAP mailbox | Python `imaplib` stdlib (SSL, unread fetch, mark-read) |
| HTTP client | `requests` |
| Live AIS data (real-time) | AISStream WebSocket — `wss://stream.aisstream.io/v0/stream` (free) |
| Live AIS data (historical) | MarineTraffic REST API — `GET /portcalls/{api_key}` |
| Mock AIS data | `maritime_registry.json` (3 vessels, MMSI-keyed) |
| Language | Python 3.10+ |
| Configuration | `.streamlit/secrets.toml` |

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)
![Streamlit](https://img.shields.io/badge/Streamlit-FF4B4B?logo=streamlit)
![LLaMA-4](https://img.shields.io/badge/LLaMA--4_Scout-FF6F00?logo=meta)
![Groq](https://img.shields.io/badge/Groq-00A98F)
![Pydantic](https://img.shields.io/badge/Pydantic-E92063?logo=pydantic)

---

## How It Works

### 1. Email Ingestion (Tab 5)

Upload a `.eml` file, connect directly to a live mailbox via IMAP, or select a demo scenario. The system parses email headers without opening any attachment:

```
From: "Atlantic Shipping BV" <billing@atlantlc-shipping.com>
Reply-To: urgent.payments@gmail.com
Subject: URGENT: Updated Banking Details — Invoice INV-2026-441 ACTION REQUIRED
```

Flags raised:
- 🚨 **HIGH** — Reply-to domain (`gmail.com`) differs from sender domain (`atlantlc-shipping.com`)
- ⚠️ **MEDIUM** — Reply-to is a consumer email provider
- 🔶 **LOW** — Subject contains urgency keywords: `"urgent"`, `"action required"`

Image/PDF attachments are extracted and can be passed directly into the extraction pipeline.

---

### 2. Invoice Extraction (Tab 1)

Images or PDF pages are preprocessed (contrast enhancement, resize) then sent to LLaMA-4 Scout for structured extraction:

```python
# Extracted InvoiceData (Pydantic model)
{
  "invoice_number": "INV-2026-441",
  "invoice_date": "2026-06-20",
  "vendor_name": "Atlantic Shipping BV",
  "total_amount": 42500.0,
  "currency": "EUR",
  ...
}
```

Isolation Forest runs across the batch to score each invoice against the distribution of amounts, taxes, and subtotals.

---

### 3. Telemetry Validation (Tab 4)

The extracted invoice's logistics identifiers are submitted to the validation engine:

```python
payload = {
    "mmsi": "244170218",            # 9-digit vessel identifier (primary key)
    "discharge_port": "Port of Rotterdam",
    "invoice_date": "2026-06-20",
    "submission_date": "2026-06-23",
    "iban": "GB29NWBK60161331926819",   # attacker's IBAN
    "cargo_type": "grain",
    "voyage_id": "VOY-2026-999",
    "cargo_quantity_mt": 15000,
}
result = telemetry_context_validation(
    payload,
    marinetraffic_api_key=mt_key,   # paid, historical — priority source
    aisstream_api_key=ais_key,       # free, real-time — used when no MT key
)
```

Result:
```
BLOCKED — IBAN on invoice does not match registered carrier IBAN. Possible account hijack.
Risk score: 1.0

Checks:
  ✅ discharge_port — Port confirmed: 'Port of Rotterdam'
  ✅ cargo_type — Compatible with container vessel
  ✅ voyage_id — Not previously invoiced
  ❌ iban — IBAN mismatch. Registered: NL91ABNA0417164300
```

**MarineTraffic mode:** with a `MARINETRAFFIC_API_KEY`, port verification hits the `/portcalls` endpoint with a ±14-day window around the invoice date — best for historical invoice validation.

**AISStream mode:** with an `AISSTREAM_API_KEY` (free at aisstream.io), port verification subscribes to a live WebSocket feed and waits up to the configured listen window (default 30 s) for the vessel to broadcast inside the port bounding box — best for current port calls.

Both live modes fall back silently to the local registry on network or API error — no false positives from connectivity issues.

**Mock mode:** no API key required; all checks use `maritime_registry.json`.

---

### 4. B/L Scanner Web App

A focused, standalone web interface for scanning a single Bill of Lading image — no login, no setup beyond running the server.

```
Upload B/L image (JPEG / PNG / WebP)
        ↓
LLaMA-4 Scout extracts: vessel name, MMSI, IMO, voyage number,
  port of loading, port of discharge, B/L date,
  cargo description, tonnage, shipper, consignee
        ↓
Cargo description → type mapping
  e.g. "bulk wheat" → grain  |  "crude petroleum" → crude_oil
        ↓
telemetry_context_validation() — same engine as the Streamlit app
        ↓
Verdict rendered in browser:
  ✅ CLEAR     — physical event confirmed
  ⚠️ REVIEW    — API unavailable or vessel not identified
  🚨 BLOCKED   — fraud indicator detected + field breakdown
```

**Run the B/L scanner:**
```bash
uvicorn bl_scanner_app:app --reload --port 8502
# open http://localhost:8502
```

---

### 5. Accounts Payable Mailbox

An Outlook-style inbox served at `/mailbox` by the same FastAPI app. It loads a demo
inbox of shipping-invoice emails (`mailbox_inbox/*.eml` + `inbox.json`) — a mix of
legitimate invoices and fraud cases. Open any email and click **🛡 Cross-check facts**:

```
POST /mailbox/check  { "id": "<email id>" }
        ↓
Layer 1 — email_ingestor.ingest_eml()      → VEC header forensics (reply-to mismatch,
                                              free-provider sender, urgency keywords)
Layer 2 — telemetry_context_validation()   → port / IBAN / cargo / DWT / voyage / date
        ↓
Combined verdict rendered in a side panel:
  ✅ CLEAR   ⚠️ REVIEW   🚫 BLOCKED  + per-check breakdown and risk score
```

A HIGH email-header flag escalates the verdict to BLOCKED. The button calls the **same
engines** used by the B/L scanner and the Streamlit dashboard — no logic is duplicated.

```bash
uvicorn bl_scanner_app:app --reload --port 8502
# open http://localhost:8502/mailbox
```

---

### 6. Fraud Patterns Detected

Based on Skuld P&I Club maritime fraud case files:

| Case | Pattern | Signal |
|---|---|---|
| A | Forged Bill of Lading | Invoice date vs AIS dock date > ±2 days |
| B | Ghost cargo / duplicate sale | Voyage ID already invoiced; or cargo type incompatible with vessel type |
| C | DWT overstatement | Cargo quantity exceeds vessel deadweight tonnage |
| D | Fake agency invoice | Invoice submitted >14 days after vessel departure |
| VEC | IBAN hijack | Invoice IBAN ≠ registered carrier IBAN |

---

## Demo Scenarios

The Telemetry Validation tab has 10 built-in scenarios. Key examples:

| Scenario | MMSI | Details | Verdict |
|---|---|---|---|
| CLEAR | 244170218 | Rotterdam · correct IBAN | CLEAR |
| Port mismatch | 244170218 | Claims Port of Antwerp | BLOCKED |
| Unknown vessel | 999999999 | Not in registry | BLOCKED |
| IBAN hijack | 244170218 | Attacker's IBAN substituted | BLOCKED |
| Duplicate voyage (Case B) | 244170218 | VOY-2026-441 already invoiced | BLOCKED |
| Tanker + grain cargo (Case B) | 224143870 | Tanker invoiced for bulk grain | BLOCKED |
| DWT exceeded (Case C) | 211456200 | Claims 50,000 MT, vessel max 45,000 | BLOCKED |
| Late submission (Case D) | 211456200 | Invoice 20 days after dock date | BLOCKED |

---

## Project Structure

```
ShipShield/
├── enhanced_ui.py          # 6-tab Streamlit dashboard
├── bl_scanner_app.py      # FastAPI app — B/L scanner + Accounts Payable mailbox (all layers)
├── templates/
│   ├── bl_index.html      # B/L scanner frontend — single-file, no build step
│   └── mailbox.html        # Outlook-style AP mailbox (HTML-escaped renderer)
├── mailbox_inbox/          # Demo .eml inbox + inbox.json manifest (14 scenarios)
├── telemetry_validator.py  # Layer 2 — physical telemetry (AIS port call / cargo / DWT / dates)
├── email_ingestor.py       # Layer 1 — VEC email header analysis + attachment extraction
├── email_forensics.py      # Layer 1 — look-alike domain, homoglyph & zero-width detection
├── document_hygiene.py     # Layer 0 — active/hidden content scan of attachments
├── vendor_ledger.py        # Layer 3 — hashed IBAN ledger + bank-account change-detection
├── bank_enrich.py          # Layer 3 — IBAN/bank enrichment + Verification of Payee
├── sanctions_screen.py     # Layer 4 — sanctions / dark-fleet screening
├── entity_verify.py        # Layer 4 — VAT (VIES) + commercial-register verification
├── vessel_risk.py          # Layer 2+ — Equasis / Port State Control enrichment
├── seed_vendor_ledger.py   # rebuild vendor_ledger.json (hash-only)
├── seed_bank_accounts.py   # rebuild account_registry.json (hash-only VoP directory)
├── maritime_registry.json  # Mock AIS registry (vessels keyed by MMSI)
├── sanctions_list.json     # Mock consolidated sanctions data
├── vessel_risk.json        # Mock Equasis/PSC data
├── company_registry.json   # Mock VAT/commercial-register data
├── vendor_ledger.json      # Hash-only known-account ledger (safe to commit)
├── account_registry.json   # Hash-only Verification-of-Payee directory
├── utils.py · analytics.py · app.py
├── test_mailbox.py         # Pytest suite for the mailbox + all layers
├── legal/                  # GDPR Terms of Use & consent (DE authoritative + EN)
├── requirements.txt
├── .streamlit/secrets.toml # API keys + ledger pepper (gitignored)
├── .Dataset/  ·  Results/
```

### Validation layers

The mailbox cross-check runs the document through five layers; any single hard
failure (or a HIGH email-header flag) blocks the payment, and unverifiable signals
resolve to REVIEW — never a false clear.

| Layer | Question | Module(s) |
|---|---|---|
| 0 — Document hygiene | Is the attachment weaponised (macros, embedded JS, auto-run)? | `document_hygiene.py` |
| 1 — Email / VEC | Is the sender spoofed, look-alike, homoglyph, urgent? | `email_ingestor.py`, `email_forensics.py` |
| 2 — Physical telemetry | Did the vessel actually dock / carry / fit the claim? | `telemetry_validator.py`, `vessel_risk.py` |
| 3 — Bank account | Has the account changed, and does it belong to the vendor? | `vendor_ledger.py`, `bank_enrich.py` |
| 4 — Counterparty | Is the vendor real, valid and not sanctioned? | `sanctions_screen.py`, `entity_verify.py` |

### Indicator weights (IOC scoring)

Each indicator carries a graduated weight, not a blanket maximum — a late invoice
is not as damning as a sanctions hit. The overall **risk score is the highest
weight among the indicators that fired**. The verdict is **BLOCKED** if any
blocking indicator fires (or a HIGH email-header flag), **REVIEW** if only
review-grade signals fire, otherwise **CLEAR**.

| Indicator | Layer | Weight | Verdict |
|---|---|---:|---|
| Sanctions / dark-fleet match | 4 | 1.00 | BLOCK |
| Unknown / unidentifiable vessel | 2 | 1.00 | BLOCK |
| Verification-of-Payee mismatch (account holder ≠ vendor) | 3 | 0.95 | BLOCK |
| IBAN ≠ registered carrier account | 2/3 | 0.90 | BLOCK |
| New bank account for a known vendor (change-detection) | 3 | 0.90 | BLOCK |
| Active/hidden content in attachment | 0 | 0.90 | BLOCK |
| Duplicate voyage (ghost cargo) | 2 | 0.85 | BLOCK |
| Port of discharge mismatch | 2 | 0.80 | BLOCK |
| Counterparty dissolved / insolvent | 4 | 0.80 | BLOCK |
| VEC email header — HIGH (reply-to mismatch, look-alike, homoglyph) | 1 | 0.80 | BLOCK |
| VAT ID invalid | 4 | 0.75 | BLOCK |
| Cargo / vessel-type incompatible | 2 | 0.75 | BLOCK |
| DWT capacity exceeded | 2 | 0.70 | BLOCK |
| Invoice/dock date drift (post/ante-dating) | 2 | 0.55 | BLOCK |
| Late agency invoice (> 14 days) | 2 | 0.50 | BLOCK |
| Vessel risk — PSC detentions / flag of convenience | 2+ | 0.30 | REVIEW |
| Unverifiable — API down, no baseline, new incorporation, VoP n/a | any | 0.30 | REVIEW |
| Email header — MEDIUM/LOW (free provider, urgency, zero-width) | 1 | 0.30 | REVIEW* |

\* Email MEDIUM/LOW flags only move a CLEAR verdict to REVIEW; they do not override a higher signal.

---

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/PabloPAF/Ship-Shield
   cd Ship-Shield
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Configure API keys in `.streamlit/secrets.toml`:
   ```toml
   GROQ_API_KEY = "your_groq_api_key"
   MARINETRAFFIC_API_KEY = "your_mt_api_key"   # optional — historical AIS port calls
   AISSTREAM_API_KEY = "your_aisstream_key"     # optional — real-time AIS (free at aisstream.io)

   # Enrichment APIs — all optional; each falls back to a bundled offline mock
   SANCTIONS_API_KEY = "…"                      # Layer 4 — OpenSanctions / OFAC / EU
   EQUASIS_API_KEY   = "…"                      # Layer 2+ — Equasis / Port State Control
   VIES_API_KEY      = "…"                      # Layer 4 — VAT / commercial register
   VOP_API_KEY       = "…"                      # Layer 3 — Verification of Payee (open banking)

   # REQUIRED for production — secret pepper for the hashed IBAN ledgers.
   # Without it a clearly-labelled dev pepper is used (demo only). After setting
   # it, re-run: python seed_vendor_ledger.py && python seed_bank_accounts.py
   SHIPSHIELD_LEDGER_PEPPER = "a-long-random-secret"
   ```

4. Run the apps:

   **Streamlit dashboard** (full 5-tab suite):
   ```bash
   streamlit run enhanced_ui.py
   ```

   **B/L scanner + Accounts Payable mailbox** (FastAPI):
   ```bash
   uvicorn bl_scanner_app:app --reload --port 8502
   # B/L scanner:  http://localhost:8502/
   # AP mailbox:   http://localhost:8502/mailbox
   ```

Both apps read from the same `secrets.toml`. Without a MarineTraffic key, all telemetry checks use the local `maritime_registry.json` — all demo scenarios work fully offline.

---

## UI

| Tab | Screenshot |
|---|---|
| **Main Interface** | ![image](https://github.com/user-attachments/assets/2e8c9582-ff0c-4790-817f-95298c7d43f2) |
| **Fraud Detection** | ![Screenshot 2025-06-03 231430](https://github.com/user-attachments/assets/c9531a28-631a-46b8-9574-ae2e7d02c00f) |
| **Chat Assistant** | ![image](https://github.com/user-attachments/assets/11de9800-101e-4099-bf3c-b81815efbc83) |

---

## Performance

| Metric | Value |
|---|---|
| Extraction accuracy | 92.7% |
| Fraud detection precision | 89.3% |
| Anomaly detection recall | 85.6% |
| Average processing time | 3.2 s/invoice |
| Multilingual support | 8 languages |

---

## Fraud Detection Rules (Tab 3)

In addition to telemetry validation, Tab 3 applies document-level rules:

1. **Duplicate invoice numbers** — same number across different vendors
2. **Round amounts** — excessive rounding (e.g. $10,000.00 exactly)
3. **After-hours invoices** — dated outside normal business hours
4. **Rapid succession** — multiple invoices from same vendor within a short window
5. **Amount discrepancies** — large gap between subtotal and total

---

## Roadmap

- [x] Port of discharge verification via MarineTraffic AIS
- [x] IBAN cross-check vs registered carrier
- [x] Skuld Case A — invoice date vs dock date
- [x] Skuld Case B — vessel type / cargo incompatibility + voyage duplicate
- [x] Skuld Case C — DWT capacity overstatement
- [x] Skuld Case D — late agency invoice submission
- [x] VEC email header analysis (reply-to mismatch, impersonation, urgency keywords)
- [x] Three-way verdict: CLEAR / REVIEW / BLOCKED
- [x] B/L scanner web app — FastAPI + drag-drop frontend, LLaMA B/L extraction, cargo type mapping
- [x] AISStream real-time WebSocket integration — live vessel position check with listen window
- [x] IMAP mailbox integration — fetch unread invoice emails directly from Gmail, Outlook, Yahoo, or any IMAP provider
- [ ] Auto-extract MMSI, voyage ID, and port from invoice image via LLaMA (Streamlit tab auto-fill)
- [ ] Live IBAN verification via Open Banking / SWIFT gpi
- [ ] REST API plugin for Odoo / ERPNext / QuickBooks
- [ ] Multi-source AIS: AISHub + port authority records

---

## License

Distributed under the MIT License. ShipShield extends the open-source
**SmartInvoiceAI** invoice parser by Janani N; the original copyright notice is
preserved in `LICENSE`.

## Credits

- ShipShield — maritime telemetry validation layer + Accounts Payable mailbox
- Base invoice parser: [SmartInvoiceAI](https://github.com/JaanuNan/SmartInvoiceAI) by Janani N
