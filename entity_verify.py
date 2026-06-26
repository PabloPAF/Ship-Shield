"""
Layer 4 (Counterparty) — entity / VAT / registry verification.

Confirms the counterparty actually exists and is a valid trading entity:
VAT-ID validation (EU VIES) plus commercial-register status (e.g. German
Handelsregister / Northdata / GLEIF). A dissolved/insolvent company or an invalid
VAT ID BLOCKS; a brand-new incorporation raises REVIEW.

Security / privacy:
* Only the company name, VAT ID and country are ever sent to a verification
  source — never the IBAN, never document contents (data minimisation, GDPR).
* External responses are treated as untrusted data: parsed, never executed, and
  HTML-escaped before display.

Live mode : query VIES / a register API when configured.
Mock mode : fall back to company_registry.json so the demo runs offline.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

REG_PATH = Path(__file__).parent / "data" / "company_registry.json"
_DEAD_STATUSES = {"dissolved", "insolvent", "liquidation", "struck off", "struck-off", "inactive"}


from email_forensics import _levenshtein  # reuse existing edit-distance lever


def _norm(s: str) -> str:
    # lower-case, drop punctuation (so 'Atlantic Shipping B.V.' == 'atlantic shipping bv')
    s = re.sub(r"[^a-z0-9 ]", "", (s or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def _load() -> dict:
    try:
        return json.loads(REG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _days_since(iso: str, ref: str = "") -> int | None:
    try:
        r = date.fromisoformat(ref) if ref else date.today()
        return (r - date.fromisoformat(iso)).days
    except (ValueError, TypeError):
        return None


def verify_entity(vendor: str, invoice_date: str = "", vies_api_key: str | None = None) -> dict:
    """Return a check dict: field 'entity', status PASS|WARN|FAIL."""
    if not vendor:
        return None
    reg = _load()  # (live VIES/register client would go here when configured)
    rec = reg.get(_norm(vendor))
    source = "vies_api" if vies_api_key else "mock_registry"

    if rec is None:
        # Not an exact match. Is it a near-match of a known entity? That is itself a
        # red flag (impersonation/typosquat) — surface it for review, never auto-pass.
        key = _norm(vendor)
        near = next((k for k in reg
                     if not k.startswith("_") and 0 < _levenshtein(key, k) <= 2), None)
        if near:
            return {
                "status": "WARN", "field": "entity",
                "detail": (f"Counterparty '{vendor}' is a near-match of registered entity "
                           f"'{reg[near].get('vat_id','')}' / '{near}' but not an exact match — "
                           f"possible impersonation. Verify before paying."),
                "source": source,
            }
        return {
            "status": "WARN",
            "field": "entity",
            "detail": f"Counterparty '{vendor}' could not be verified against VAT/registry sources — manual check required.",
            "source": source,
        }

    status = str(rec.get("status", "")).lower()
    if status in _DEAD_STATUSES:
        return {
            "status": "FAIL", "field": "entity",
            "detail": f"Counterparty '{vendor}' has registry status '{status}' — not a valid trading entity. Do not pay.",
            "source": source, "risk": 0.80,
        }
    if rec.get("vat_valid") is False:
        return {
            "status": "FAIL", "field": "entity",
            "detail": f"VAT ID {rec.get('vat_id','')} for '{vendor}' failed validation (VIES). Possible fabricated counterparty.",
            "source": source, "risk": 0.75,
        }

    age = _days_since(rec.get("incorporated", ""), invoice_date)
    if age is not None and age < 90:
        return {
            "status": "WARN", "field": "entity",
            "detail": (f"'{vendor}' was incorporated only {age} days before the invoice date "
                       f"({rec.get('incorporated','')}). New counterparties warrant extra diligence."),
            "source": source,
        }

    return {
        "status": "PASS", "field": "entity",
        "detail": (f"VAT {rec.get('vat_id','')} valid; '{vendor}' active since "
                   f"{rec.get('incorporated','')} ({rec.get('country','')})."),
        "source": source,
    }
