# SmartInvoiceAI — Physical Telemetry Validation Engine

> A maritime logistics fraud detection system that combines AI-powered document extraction with a physical reality check layer. While standard tools verify *structure*, this system verifies *whether the shipment actually happened* — by cross-referencing identifiers against live AIS vessel tracking data.


*Built as a graduate project (open-source) on top of an open-source parser.*

---

## Table of Contents

1. [The Problem](#1-the-problem)
2. [Impact & Analysis](#2-impact--analysis)
3. [Real-World Attack Scenarios](#3-real-world-attack-scenarios)
4. [What It Detects](#4-what-it-detects)
5. [Features](#5-features)
6. [Tech Stack](#6-tech-stack)
7. [Architecture](#7-architecture)
8. [Installation & Setup](#8-installation--setup)
9. [Usage](#9-usage)
10. [Performance](#10-performance)
11. [Roadmap](#11-roadmap)
12. [Team](#12-team)

---

## 1. The Problem

### VEC and Bill of Lading (B/L) Fraud in the Maritime Industry

The maritime shipping and logistics industry has seen a sharp increase in sophisticated cyber-enabled financial fraud. Legacy verification tools only validate a document's text layout or basic mathematical totals — they fail to cross-reference data with underlying physical reality.

**Vendor Email Compromise (VEC):** Attackers clone or spoof trusted accounts, inject highly convincing invoices into active, ongoing business threads. Because the threads appear legitimate, standard manual rule-based filters miss them. Most critically, a Bill of Lading serves as a cargo receipt, contract of carriage, and title of legal ownership of goods — forged B/Ls can trigger false bank payments and facilitate smuggling.

**The Detection Gap:** Over **65% of B/L frauds are discovered after funds are already or permanently lost**. Few are stopped before processing. This gap exists due to fragmented systems, swift turnaround pressures at ports, and high-volume workflows.

---

## 2. Impact & Analysis

### Losses & Frequency

| Metric | Value |
|---|---|
| Direct fraud losses (annually) | $300M – $500M |
| Wider supply-chain costs | $1.8B – $3.3B |
| Cascading costs (delays, litigation, reputational damage) | Exceeds $8B |
| Median individual incident loss | $500K – $5M |

### Geographic Risk

Fraud is concentrated in high-volume shipping lanes and routes (10,000+ vessels involved). West Africa and Southeast Asia show the highest escalation rates. The fraud primarily follows four categories: invoice redirection, forged B/Ls, ghost voyages, and duplicate cargo sales.

---

## 3. Real-World Attack Scenarios

### Scenario A — Small/Medium Business Freight Forwarder
An attacker registers a lookalike domain, monitors active transactions, and injects a forged PDF invoice into an existing email thread. The AP clerk sees a known vessel name, processes the payment — but the vessel was never in the claimed port.

**SmartInvoiceAI catches it:** AIS data shows no port call during the claimed window.

### Scenario B — Portal Upload
A customs broker uploads a B/L obtained via a dark marketplace. The alphanumeric record looks valid, but the vessel's discharge port, submission date, and cargo tonnage don't match any confirmed voyage.

**SmartInvoiceAI catches it:** Cross-reference with AIS voyage history returns no matching record. The system returns **BLOCKED** with a full flag breakdown.

---

## 4. What It Detects

### Email Header Analysis (VEC Indicators)
- Reply-to domain differs from sender domain (impersonation pattern)
- Sender using a consumer provider (Gmail, Yahoo) claiming to be a shipping company
- Urgency / redirect language in subject or body
- Suspicious keywords: `"action required"`, `"update bank details"`, `"URGENT"`

### Bill of Lading Physical Validation
| Check | Description |
|---|---|
| Port call verification | Did the vessel call the claimed port? (via AIS) |
| Date plausibility | Post/ante-dating detection (Skuld Case reference) |
| Cargo compatibility | Can the vessel physically carry the claimed cargo type? |
| Voyage alignment | Does the claimed route match actual AIS voyage history? |
| Ghost voyage / duplicate sale | Was cargo already flagged as sold on this voyage? |
| DWT overstatement | Does claimed tonnage exceed vessel deadweight? |
| Submission anomaly | Suspiciously low submission count or duplicate IBAN |

### Example AIS Cross-Reference
```json
{
  "mmsi": "244170218",
  "discharge_port": "Amsterdam",
  "submission_date": "2026-06-20",
  "iban": "NL91ABNA0417164300",
  "cargo": "grain",
  "quantity_mt": 15000,
  "result": "BLOCKED",
  "flags": [
    "Vessel not in Amsterdam port — 14-day AIS window shows no port call",
    "IBAN previously flagged: possible account hijack",
    "DWT overstated: claimed 50,000 MT, vessel max 45,000 MT"
  ]
}
```

---

## 5. Features

- **BOL scanner web app** — drag-drop a Bill of Lading image (JPEG / PNG / WebP), get instant verdict
- **Standalone FastAPI backend** — no complex setup beyond a server
- **5-tab dashboard** — chatbot, ingestion, analytics, AIS live map, alert history
- **LLaMA-4 Scout** via Groq API — multilingual (8+ languages); BOL-specific extraction
- **Isolation Forest** — unsupervised ML anomaly detection across four fraud signal categories
- **P&I Club case database** — cross-reference against Skuld P&I Club historical cases
- **Email ingestion** — `.eml` upload, IMAP mailbox fetch, or demo scenarios
- **AIS live positions** via WebSocket (free tier); falls back to mock integration
- **Three-verdict system:** `CLEAR` / `REVIEW` (AIS unavailable) / `BLOCKED`
- **Per-flag risk score breakdown** with 10 built-in scoring rules
- **Progress stream** — real-time processing updates via Server-Sent Events
- **Data export** — JSON download, queryable audit log

---

## 6. Tech Stack

| Technology | Role |
|---|---|
| FastAPI + Uvicorn | Backend API server |
| Vanilla HTML / CSS / JS | Frontend (no framework required) |
| LLaMA-4 Scout 17B (Groq API) | Document AI extraction, multilingual |
| scikit-learn (Isolation Forest) | Unsupervised ML fraud pattern detection |
| Pydantic v2 | Data validation & schema enforcement |
| Python `imaplib` / `email` stdlib | RFC 2822 / MIME email parsing (SSL) |
| `requests` | MarineTraffic REST API calls |
| MarineTraffic AIS API | `GET /vessels/{api_key}` — MMSI-keyed, 3.10+ |
| Python 3.10+ | Runtime |

---

## 7. Architecture

```
┌─────────────────────────────────────────────┐
│                   Web App                   │
│   (Drag-drop BOL / Upload .eml / Demo)      │
└────────────────────┬────────────────────────┘
                     │
          ┌──────────▼──────────┐
          │   FastAPI Backend   │
          │  (bol_app:app)      │
          └──┬──────────────┬───┘
             │              │
    ┌─────────▼────┐  ┌──────▼──────────┐
    │  LLaMA-4     │  │  Isolation      │
    │  Scout 17B   │  │  Forest ML      │
    │  (Groq API)  │  │  (scikit-learn) │
    └─────────┬────┘  └──────┬──────────┘
             │              │
    ┌─────────▼──────────────▼───────┐
    │     AIS Telemetry Layer        │
    │  MarineTraffic REST / WS API   │
    │  14-day voyage window          │
    └────────────────────────────────┘
             │
    ┌─────────▼───────────────────────┐
    │  Verdict Engine                 │
    │  CLEAR / REVIEW / BLOCKED       │
    │  + per-flag risk score          │
    └─────────────────────────────────┘
```

Single-module architecture: `bol_app.py` + `frontend/index.html` + `utils/` + `helpers/`.

---

## 8. Installation & Setup

### Prerequisites
- Python 3.10+
- Groq API key (free tier available)
- MarineTraffic API key (free tier; WebSocket for live positions)

### Steps

```bash
# 1. Clone the repository
git clone https://github.com/JaanuNan/SmartInvoiceAI
cd SmartInvoiceAI

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure secrets
# Create secrets.toml in the project root:
```

```toml
# secrets.toml
GROQ_API_KEY = "your_groq_api_key"
MARINETRAFFIC_API_KEY = "your_marinetraffic_api_key"
```

```bash
# 4. Run the server
uvicorn bol_app:app --reload --port 8502
```

Without a Groq API key, AI extraction is disabled but rule-based checks still run. Without a MarineTraffic key, AIS checks fall back to mock data and verdicts downgrade to `REVIEW` rather than `BLOCKED`.

---

## 9. Usage

### Web Interface
1. Open `http://localhost:8502` in your browser
2. **Tab 1 — Scanner:** Drag-drop a Bill of Lading image or upload a `.eml` email file
3. The system extracts fields (vessel name, MMSI, IMO, port, date, IBAN, cargo, tonnage)
4. AIS cross-reference runs automatically in the background
5. Verdict appears within ~3.2 seconds average, with a full flag breakdown

### Example Email Scenario (built-in demo)
```
From: "Atlantic Shipping BV" <billing@atlant1c-shipping.com>
To: accounts@yourcompany.com
Subject: URGENT: Update Bank Details — INV-2026-441 ACTION REQUIRED

Flags raised:
  HIGH   — Urgency keywords: "URGENT", "ACTION REQUIRED"
  HIGH   — Reply-to domain differs from sender (impersonation pattern)
  MEDIUM — Sender using consumer email provider claiming to be shipping company
  LOW    — Invoice total ($42,500.00 exactly) matches known round-number fraud pattern
```

### API (Headless / Programmatic)
```python
import requests

result = requests.post("http://localhost:8502/scan", json={
    "mmsi": "244170218",
    "discharge_port": "Amsterdam",
    "submission_date": "2026-06-20",
    "iban": "GB29NWBK60161331926819",
    "cargo": "grain",
    "quantity_mt": 15000
})
print(result.json())
```

---

## 10. Performance

| Metric | Value |
|---|---|
| BOL extraction accuracy | 92.7% |
| Email header detection rate | 89.3% |
| Average processing time | 3.2 s/document |
| Built-in scoring rules | 10 |

*Tested on internal dataset. Performance on live data may vary.*

---

## 11. Roadmap

- [x] BOL image extraction (LLaMA-4 Scout)
- [x] AIS telemetry cross-reference
- [x] Email header VEC analysis
- [x] Isolation Forest anomaly scoring
- [x] Streamlit / FastAPI live demo
- [ ] Auto-fill from Outlook / any email client
- [ ] SWIFT gpi plugin integration
- [ ] ERP integration (ERPNext / QuickBooks)
- [ ] AIS Hub + extended data sources
- [ ] IBAN check against known fraud registries

---

## 12. Team

| Role | Name |
|---|---|
| Project Lead | Janani N |
| Team Members | Akar Sarpal, Anup Bangalore, Pablo Fernandez, Vicky Kohnen |
| Module | Cybersecurity — Hack Tank |
| Date | 2026-06-25 |

---

## License

MIT License — see `LICENSE` for details.

---

*SmartInvoiceAI is an academic project. AIS data is sourced from MarineTraffic (free tier). Skuld P&I Club case references are used for educational benchmarking only.*
