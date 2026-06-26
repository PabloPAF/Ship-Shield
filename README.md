# ShipShield — Physical Telemetry Validation Engine

> Maritime invoice fraud detection that checks not just *what* a document says, but *whether the shipment actually happened*.


---

## The Problem

Maritime shipping is a prime target for **Vendor Email Compromise (VEC)** and **Bill of Lading (B/L) fraud**. Attackers forge invoices and B/Ls, inject them into live payment threads, and redirect funds — often undetected because standard tools only validate document structure, not physical reality. Over **65% of B/L frauds are discovered after funds are already lost**, with direct losses ranging from **$300M–$500M** annually and broader supply-chain damage exceeding **$8B**.

## The Solution

ShipShield cross-references invoice and B/L data against **live AIS vessel tracking** to confirm the shipment physically occurred. It catches what document-only tools miss: ghost voyages, duplicate sales, DWT overstatements, and post/ante-dated cargo — plus email header anomalies that signal VEC attacks.

![Mail-demo](image-1.png)

![AIS-DEMO](image-2.png)

![BOL-DEMO](image-3.png)

## Architecture — `/mailbox/check` cross-check flow

```mermaid
flowchart TD
  A(["POST /mailbox/check · email_id"]) --> B["Load .eml · mailbox_inbox/&lt;id&gt;.eml"]
  B --> L0["Layer 0 — Document Hygiene · document_hygiene.scan_attachments()<br/>scan PDFs for JS / macros / VBA · CLEAN · WARN · FAIL"]
  L0 --> L1a["Layer 1a — VEC Header Analysis · email_ingestor.ingest_eml()<br/>reply-to mismatch · free-provider sender · urgency keywords"]
  L0 --> L1b["Layer 1b — Forensic Analysis · email_forensics.analyze()<br/>typosquat domains · homoglyphs · zero-width · HIGH · MED · LOW · CLEAN"]
  L1a --> P["Extract Invoice Payload<br/>vendor · iban · amount · mmsi · imo · discharge_port · voyage_id · cargo_type · cargo_quantity_mt · invoice_date"]
  L1b --> P
  P --> L2a["Layer 2a — Telemetry Validation · telemetry_context_validation()<br/>A: date vs AIS dock · B: cargo vs vessel · C: duplicate voyage · D: weight vs DWT<br/>CLEAR · REVIEW · BLOCKED"]
  P --> L2b["Layer 2b — Vessel Risk · vessel_risk.assess_vessel()<br/>PSC detentions · flag-of-convenience · class society"]
  P --> L2c["Layer 2c — Amount Anomaly · amount_anomaly.assess_amount()<br/>per-vendor z-score outlier (pandas baseline)"]
  P --> L3a["Layer 3a — Vendor Ledger · vendor_ledger.check_vendor_iban()<br/>HMAC-SHA256 hashed IBAN history · new / changed account"]
  P --> L3b["Layer 3b — Verification of Payee · bank_enrich.enrich_bank()<br/>does the account belong to the named vendor?"]
  P --> L4a["Layer 4a — Sanctions Screen · sanctions_screen.screen_counterparty()<br/>vendor · vessel · IMO/MMSI · IBAN jurisdiction · PASS · WARN · FAIL"]
  P --> L4b["Layer 4b — Entity Verify · entity_verify.verify_entity()<br/>VAT validation · commercial register · status · name-match"]
  L2a --> C{"Combine Verdicts<br/>HIGH email OR any FAIL → BLOCKED<br/>WARN / REVIEW → REVIEW · all PASS → CLEAR"}
  L2b --> C
  L2c --> C
  L3a --> C
  L3b --> C
  L4a --> C
  L4b --> C
  C --> AU["audit_log.append()<br/>hash-chained JSONL tamper-evident log"]
  C --> R["JSON Response to UI<br/>all layer results + risk scores + audit hash"]
```

## How to Use

### 1. Clone the repo
```bash
git clone https://github.com/PabloPAF/Ship-Shield
cd Ship-Shield
```

### 2. Configure secrets
```toml
# .streamlit/secrets.toml
GROQ_API_KEY = "your_groq_api_key"
MARINETRAFFIC_API_KEY = "your_marinetraffic_api_key" 
```

### 3. Run
```bash
uvicorn bl_scanner_app:app --reload --port 8502
```

Then open your browser and drag-drop a Bill of Lading image (JPEG / PNG / WebP) or upload a `.eml` email file. You'll get a **CLEAR / REVIEW / BLOCKED** verdict with a per-flag risk score breakdown in seconds.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI + Uvicorn |
| Frontend | Vanilla HTML/CSS/JS |
| AI Model | LLaMA-4 Scout 17B via Groq API |
| ML | z-score outlier detection (NumPy / pandas) |
| Validation | Pydantic v2 |
| AIS Data | MarineTraffic REST API + AISStream (real-time) |
| Email Parsing | Python `imaplib` / RFC 2822 |

---

*Built for the Cybersecurity / Hack Tank (2026).*
