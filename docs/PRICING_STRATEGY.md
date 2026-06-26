# ShipShield — Pricing Strategy & 5-Year Outlook

> **Basis & disclaimer.** This is a planning document for a student/startup project,
> not a financial forecast. Market sizes, conversion rates and price points below are
> **illustrative assumptions** (sourced from the project's own `context.md` and general
> B2B-SaaS norms, not live market research). Validate every headline figure — fraud-loss
> statistics, SME counts, competitor pricing — before using them externally.

---

## 1. What buyers are actually paying to avoid

ShipShield's price has to be anchored to the loss it prevents, not to its running cost.

- A single Vendor-Email-Compromise payment redirect or duplicate-cargo invoice typically
  costs **€75k–€500k** per incident (per `context.md`).
- ShipShield's job is to stop *one* of those before the wire clears.
- Therefore even a **€5k–€20k/year** subscription is a rounding error against a single
  avoided loss — the ROI story carries the price, and we should never price on a
  cost-plus basis.

**Pricing principle:** value-based, anchored to "fraud loss avoided + AP analyst time
saved," with a usage metric (documents validated) that scales with the customer.

---

## 2. Value metric

The cleanest metric for this product is **documents cross-checked per month** (invoices
+ B/Ls), because it tracks both the value delivered and our variable cost (the enrichment
API calls). We pair it with a small **base platform fee** (so tiny customers still cover
onboarding) and **seats** as a secondary expansion lever.

---

## 3. Tiers (EUR, DACH/EU launch)

| Tier | Target | Price (€/mo, billed annually) | Docs/mo | What's included |
|---|---|---|---|---|
| **Pilot / Free** | Lead-gen, evaluation | €0 (30 days) | 25 | Mailbox + B/L scanner, offline/mock data, 1 user |
| **Starter** | Micro forwarders / ship agents (1–10 staff) | **€149** | 300 | All 5 layers, mock + basic AIS, audit log, email support, 2 users |
| **Professional** | SME forwarders / NVOCCs (10–80 staff) | **€499** | 1,500 | + live sanctions & Verification-of-Payee, vessel risk (Equasis/PSC), CSV/JSON & audit export, 5 users, priority support |
| **Enterprise** | Regional carriers / larger forwarders / groups | **from €1,800** (custom) | 5,000+ / unlimited | + SSO, EU-hosted or on-prem, ERP/AP-system integration, custom rules, SLA, dedicated success manager |

**Add-ons (expansion / COGS pass-through):**
- Enrichment-API usage above tier allowance (sanctions / Equasis / VIES / VoP) — metered.
- ERP / AP-automation connector (Odoo, ERPNext, SAP, QuickBooks) — one-time + maintenance.
- Extra seats; additional mailboxes/entities for groups.

**Why this shape works for SMEs:** low entry price removes the "do we really need this?"
objection, the free pilot lets the product sell itself on real invoices, and the
document metric means a customer's bill grows only as their volume (and value received)
grows — land-and-expand.

---

## 4. Cost of goods (margin discipline)

ShipShield's variable cost per document is small but real and must be watched:
- **LLM extraction** (Groq/LLaMA) — a few cents per document.
- **Enrichment calls** — AIS (MarineTraffic/AISStream), sanctions, VIES, VoP — cents to
  low euros per check depending on provider; cache aggressively and only call when a
  document reaches that layer.
- Hosting is negligible at SME scale.

Target **blended gross margin 78–85%**. The enrichment APIs are the main COGS lever, so
the Starter tier deliberately runs mostly on cached/mock + basic AIS, with the expensive
live feeds gated to Professional/Enterprise.

---

## 5. Five-year outlook (base case)

**Assumptions** (all illustrative — replace with researched values):
- Beachhead: Germany → DACH → broader EU. SAM ≈ tens of thousands of EU maritime-logistics
  SMEs (context.md cites ~80,000 EU SMEs in shipping/logistics).
- Bottom-up adoption; ARPA rises as the mix shifts toward Professional/Enterprise.
- Gross logo churn ~18% in Y1–2 improving to ~10% by Y5; net revenue retention >100%
  from add-ons/expansion in later years.

| Year | Paying customers | Avg revenue / account (€/yr) | ARR (€) | Notes |
|---|---:|---:|---:|---|
| **Y1** | 15 | 3,000 | **~45k** | 3–5 design partners + early Starter/Pro; product-market-fit signal |
| **Y2** | 60 | 4,000 | **~240k** | DACH expansion; first Enterprise logo |
| **Y3** | 180 | 5,000 | **~900k** | EU expansion; channel partners; live enrichment feeds standard |
| **Y4** | 420 | 6,000 | **~2.5M** | Enterprise mix grows; ERP integrations drive expansion |
| **Y5** | 800 | 7,000 | **~5.6M** | ~1% of SAM; multi-modal & federated fraud-network upside |

**Scenario range** (rough): conservative ≈ 0.4× the ARR above; optimistic ≈ 1.8×, driven
mainly by Enterprise deal size and channel partnerships (insurers/P&I clubs, banks,
AP-automation vendors) rather than by raw logo count.

These are *demand-side* estimates; they assume the hard part — keeping the sanctions /
AIS / registry data accurate and live — is solved, which is the real cost and moat.

---

## 6. Unit economics (targets to validate)

| Metric | Target | Why it matters |
|---|---|---|
| CAC (Starter/Pro, inbound + channel) | < €1,500 | SME motion must be low-touch / self-serve |
| ARPA | €3k → €7k over 5y | Mix shift to Professional/Enterprise |
| Gross margin | 78–85% | Enrichment-API COGS is the lever |
| Logo churn | 18% → 10% | Trust product; switching cost via ERP integration |
| LTV:CAC | > 3:1 by Y3 | Sustainable growth |
| Payback | < 12 months | Capital efficiency for a bootstrapped/early raise |

---

## 7. First candidates (ICP & beachhead)

**Ideal Customer Profile:** an EU (DACH-first) maritime-logistics SME that pays
cross-border carrier/agency invoices, has **no dedicated fraud team**, and has either
been hit by VEC or is visibly worried about it.

- **Segments (in priority order):**
  1. **Freight forwarders / NVOCCs** (10–80 staff) — high invoice volume, thin AP teams.
  2. **Ship agents & port agents** (Hamburg, Bremen/Bremerhaven, Wilhelmshaven; later
     Rotterdam/Antwerp) — exactly the B/L + agency-invoice workflow ShipShield models.
  3. **Customs brokers & small regional carriers** — adjacent, similar pain.

- **Design-partner phase (Year 1):** sign **3–5 design partners** on free/discounted
  pilots in exchange for real (anonymised) invoice samples, feedback, and a case study —
  ideally one Hamburg forwarder, one regional NVOCC, one ship agency. Their data also
  improves the registry/baseline quality (the moat).

- **Go-to-market channels (the realistic way to reach SMEs):**
  - Industry associations — e.g. **DSLV** (Bundesverband Spedition und Logistik) and
    regional port/logistics communities — for credibility and reach.
  - **P&I clubs / marine insurers** (the fraud cases are literally modelled on Skuld
    case files) — a natural distribution and co-marketing partner; fraud avoided is
    aligned with their interest.
  - **Banks' fraud / payments teams** and **AP-automation vendors** — bundling/referral.
  - Logistics-tech accelerators and port-innovation hubs.

> Note: name *segments and channels* as candidates, not specific companies — we have no
> customer relationships yet, and listing named firms as "prospects" externally would be
> misleading.

---

## 8. Key risks & sensitivities

- **Data quality is the product.** Pricing assumes live sanctions/AIS/registry/VoP feeds
  are accurate and current — sourcing and maintaining them is the real cost and the moat,
  not the algorithm.
- **False-positive fatigue.** Too many REVIEW/BLOCK alerts and AP teams disengage; tune
  thresholds and lean on the graduated risk scores.
- **SME willingness-to-pay** is unproven — the free pilot exists precisely to de-risk this.
- **Competitive squeeze.** Bank-side Confirmation/Verification of Payee and AP-automation
  suites may absorb part of the value; ShipShield's defensible edge is the *physical*
  reality check (AIS/cargo/vessel) plus explainability/audit, which those don't do.
- **Concentration on one geography/regulation** (EU/GDPR) early — deliberate, but plan the
  expansion path.

---

## 9. Immediate next steps

1. Validate the three headline numbers (avg fraud loss, EU SME count, competitor pricing)
   with real sources before any external use.
2. Recruit 3–5 design partners; instrument the pilot to measure docs/month, true/false
   positive rates, and "losses avoided."
3. Decide the launch metric allowance per tier from observed pilot volumes.
4. Stand up metered billing for the enrichment add-ons (COGS visibility from day one).
