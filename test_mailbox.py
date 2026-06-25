"""
Tests for the ShipShield Accounts Payable mailbox endpoints.

Run:
    pip install -r requirements.txt pytest httpx
    pytest test_mailbox.py -v

These exercise the FastAPI routes added to bol_scanner_app.py:
    GET  /mailbox          → serves the Outlook-style inbox
    GET  /mailbox/emails   → inbox.json manifest (8 demo scenarios)
    POST /mailbox/check     → Layer 1 (VEC headers) + Layer 2 (telemetry)

No API keys required: telemetry runs in mock mode against maritime_registry.json.
Requires Python 3.11+ (bol_scanner_app imports the stdlib `tomllib`).
"""
import pytest
from fastapi.testclient import TestClient

from bol_scanner_app import app

client = TestClient(app)

# Expected combined verdict per demo email id (mock mode).
EXPECTED_VERDICT = {
    "vec": "BLOCKED",       # VEC reply-to mismatch (HIGH) + IBAN hijack
    "clean1": "CLEAR",      # Atlantic Explorer / Rotterdam — all matches
    "clean2": "CLEAR",      # Baltic Carrier / Hamburg — all matches
    "port": "BLOCKED",      # claims Antwerp, registry says Rotterdam
    "cargo": "BLOCKED",     # tanker invoiced for dry-bulk grain (Case B)
    "dwt": "BLOCKED",       # 50,000 MT > 45,000 DWT (Case C)
    "dup": "BLOCKED",       # voyage already invoiced (Case B)
    "postdate": "BLOCKED",  # invoice 12 days off dock date (Case A)
    "bol_clean": "CLEAR",   # Bill of Lading — Baltic Carrier / Hamburg, all matches
    "bol_forged": "BLOCKED",# Bill of Lading — claims Felixstowe, registry says Rotterdam
    "hygiene": "BLOCKED",   # clean invoice, but the attached PDF carries active content (Layer 0)
    "sanctioned": "BLOCKED",# clean telemetry, but vessel/carrier is sanctioned (Layer 4)
    "vessel_risk": "REVIEW", # clean telemetry, but vessel has PSC detentions (Layer 2 enrichment)
    "entity_fail": "BLOCKED",# clean telemetry, but vendor is dissolved / VAT invalid (Layer 4)
}

# The single telemetry check expected to FAIL for each fraud scenario.
EXPECTED_FAIL_FIELD = {
    "port": "discharge_port",
    "cargo": "cargo_type",
    "dwt": "cargo_quantity_mt",
    "dup": "voyage_id",
    "postdate": "invoice_date",
    "vec": "iban",
    "bol_forged": "discharge_port",
}


def test_mailbox_page_served():
    r = client.get("/mailbox")
    assert r.status_code == 200
    assert "ShipShield" in r.text
    assert "Ship-Shield" in r.text          # cross-check button label


def test_bol_scanner_still_served():
    # the original route must remain intact
    assert client.get("/").status_code == 200


def test_inbox_manifest_has_all_emails():
    r = client.get("/mailbox/emails")
    assert r.status_code == 200
    emails = r.json()
    assert len(emails) == 14
    assert {e["id"] for e in emails} == set(EXPECTED_VERDICT)
    for e in emails:
        assert e["invoice"]["number"]
        assert e["payload"]["mmsi"]


def test_bol_documents_present_and_have_no_iban():
    emails = {e["id"]: e for e in client.get("/mailbox/emails").json()}
    for bid in ("bol_clean", "bol_forged"):
        assert emails[bid]["doc_type"] == "bol"
        assert "iban" not in emails[bid]["payload"]        # a BOL carries no payment detail
    # BOL cross-check runs telemetry but skips the bank layer
    data = client.post("/mailbox/check", json={"id": "bol_clean"}).json()
    assert data["bank"] is None


@pytest.mark.parametrize("email_id, verdict", EXPECTED_VERDICT.items())
def test_cross_check_verdict(email_id, verdict):
    r = client.post("/mailbox/check", json={"id": email_id})
    assert r.status_code == 200
    data = r.json()
    assert data["verdict"] == verdict
    assert 0.0 <= data["risk_score"] <= 1.0
    assert "checks" in data["telemetry"]


@pytest.mark.parametrize("email_id, field", EXPECTED_FAIL_FIELD.items())
def test_fraud_scenarios_fail_expected_field(email_id, field):
    data = client.post("/mailbox/check", json={"id": email_id}).json()
    failed = [c["field"] for c in data["telemetry"]["checks"] if c["status"] == "FAIL"]
    if email_id == "vec":
        # VEC: IBAN fails in telemetry AND the email header risk is HIGH
        assert data["vec"]["risk"] == "HIGH"
    assert field in failed


def test_clean_emails_pass_all_checks():
    for email_id in ("clean1", "clean2"):
        data = client.post("/mailbox/check", json={"id": email_id}).json()
        assert data["verdict"] == "CLEAR"
        assert data["vec"]["risk"] == "CLEAN"
        assert all(c["status"] == "PASS" for c in data["telemetry"]["checks"])


def test_unknown_email_id_returns_404():
    assert client.post("/mailbox/check", json={"id": "does-not-exist"}).status_code == 404


# ── Layer 4: sanctions / dark-fleet screening ───────────────────────────────

def _counterparty(data, field):
    return next((c for c in data.get("counterparty", []) if c["field"] == field), None)

def test_sanctioned_vessel_blocked():
    data = client.post("/mailbox/check", json={"id": "sanctioned"}).json()
    s = _counterparty(data, "sanctions")
    assert s and s["status"] == "FAIL"
    assert data["verdict"] == "BLOCKED"

def test_clean_emails_pass_sanctions():
    for eid in ("clean1", "clean2", "bol_clean"):
        s = _counterparty(client.post("/mailbox/check", json={"id": eid}).json(), "sanctions")
        assert s and s["status"] == "PASS"


# ── Layer 2 enrichment: vessel risk (Equasis / PSC) ─────────────────────────

def test_detained_vessel_review():
    data = client.post("/mailbox/check", json={"id": "vessel_risk"}).json()
    assert data["vessel_risk"]["status"] == "WARN"
    assert data["verdict"] == "REVIEW"

def test_clean_vessels_pass_risk():
    for eid in ("clean1", "clean2", "bol_clean"):
        assert client.post("/mailbox/check", json={"id": eid}).json()["vessel_risk"]["status"] == "PASS"


# ── Layer 4: counterparty entity / VAT / registry verification ──────────────

def test_dissolved_vendor_blocked():
    data = client.post("/mailbox/check", json={"id": "entity_fail"}).json()
    e = _counterparty(data, "entity")
    assert e and e["status"] == "FAIL"
    assert data["verdict"] == "BLOCKED"

def test_clean_vendors_pass_entity():
    for eid in ("clean1", "clean2", "bol_clean"):
        e = _counterparty(client.post("/mailbox/check", json={"id": eid}).json(), "entity")
        assert e and e["status"] == "PASS"


# ── Layer 0: document hygiene (active/hidden content in attachments) ─────────

def test_active_content_pdf_is_quarantined():
    data = client.post("/mailbox/check", json={"id": "hygiene"}).json()
    assert data["hygiene"]["status"] == "FAIL"
    assert data["verdict"] == "BLOCKED"

def test_clean_pdf_attachments_pass_hygiene():
    for eid in ("clean1", "clean2", "bol_clean"):
        data = client.post("/mailbox/check", json={"id": eid}).json()
        assert data["hygiene"]["status"] == "CLEAN"

def test_hygiene_scanner_unit():
    from document_hygiene import scan_attachment
    import io, zipfile
    active = b"%PDF-1.4\n1 0 obj<< /OpenAction << /S /JavaScript /JS (x) >> >>endobj\n%%EOF"
    assert scan_attachment("a.pdf", "application/pdf", active)["status"] == "FAIL"
    benign = b"%PDF-1.4\n1 0 obj<< /Type /Catalog >>endobj\n%%EOF"
    assert scan_attachment("a.pdf", "application/pdf", benign)["status"] == "CLEAN"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/vbaProject.bin", b"\x00")
    assert scan_attachment("m.docm", "application/octet-stream", buf.getvalue())["status"] == "FAIL"


# ── Email forensics: look-alike domains, homoglyphs, zero-width chars ────────

def test_vec_email_flagged_as_lookalike_domain():
    data = client.post("/mailbox/check", json={"id": "vec"}).json()
    codes = {f["code"] for f in data["vec"]["flags"]}
    assert "LOOKALIKE_DOMAIN" in codes        # atlantlc-shipping.com vs atlantic-shipping.com

def test_email_forensics_unit():
    from email_forensics import analyze
    # typosquat of a known vendor domain
    codes = {f["code"] for f in analyze('"Atlantic" <pay@atlantlc-shipping.com>')}
    assert "LOOKALIKE_DOMAIN" in codes
    # legitimate domain -> no look-alike flag
    assert not any(f["code"] == "LOOKALIKE_DOMAIN"
                   for f in analyze("billing@atlantic-shipping.com"))
    # zero-width character in the subject
    zw = analyze("billing@atlantic-shipping.com", subject="UR​GENT payment")
    assert any(f["code"] == "ZERO_WIDTH_CHARS" for f in zw)
    # Cyrillic homoglyph in the subject ('а' is U+0430, not Latin 'a')
    hg = analyze("billing@atlantic-shipping.com", subject="pаyment due")
    assert any(f["code"] == "HOMOGLYPH_TEXT" for f in hg)


# ── Layer 3: vendor bank-account change-detection (hashed ledger) ────────────

def test_vec_email_flags_new_bank_account():
    data = client.post("/mailbox/check", json={"id": "vec"}).json()
    assert data["bank"]["status"] == "FAIL"          # new account for known vendor
    assert data["verdict"] == "BLOCKED"

def test_clean_invoice_matches_confirmed_account():
    data = client.post("/mailbox/check", json={"id": "clean1"}).json()
    assert data["bank"]["status"] == "PASS"
    assert "****" in data["bank"]["masked"]            # IBAN is masked, never raw

def test_ledger_stores_no_raw_ibans():
    import json, pathlib
    raw = pathlib.Path("vendor_ledger.json").read_text()
    for iban in ("NL91ABNA0417164300", "DE89370400440532013000", "ES9121000418450200051332"):
        assert iban not in raw                         # only peppered hashes are stored
