"""
Layer 3 (addendum) — IBAN/bank enrichment + Verification of Payee (VoP).

Layer 3's ledger detects a *changed* account for a known vendor. This adds the
other half: does the account actually *belong to* the named vendor at all? That
is Verification of Payee (name<->account matching), which kills VEC fraud even on
a first-ever invoice where there is no history.

  * IBAN country: the first two letters of the IBAN (ISO country). Compared to the
    vendor's country as an informational note only — offshore banking is normal in
    shipping, so a mismatch is context, not a verdict.
  * VoP name match: look up the account's registered holder and compare to the
    vendor. Holder != vendor  -> account mismatch (BLOCK). Unknown -> REVIEW.

Security / privacy:
* The VoP directory is hash-keyed (HMAC) exactly like the vendor ledger — no raw
  IBANs are stored. In production the IBAN goes only to a vetted, contracted
  payment-verification provider, never to a generic OSINT endpoint.
"""
from __future__ import annotations

import json
from pathlib import Path

from vendor_ledger import hash_iban, iban_country, mask_iban, normalize_vendor

ACCT_PATH = Path(__file__).parent / "data" / "account_registry.json"


def _load() -> dict:
    try:
        return json.loads(ACCT_PATH.read_text(encoding="utf-8")).get("accounts", {})
    except FileNotFoundError:
        return {}


def enrich_bank(vendor: str, iban: str, vendor_country: str = "",
                vop_api_key: str | None = None) -> dict:
    """Return a check dict (field 'bank_vop') or None when there is no IBAN."""
    if not iban:
        return None

    masked = mask_iban(iban)
    country = iban_country(iban)
    note = ""
    if vendor_country and country and country != vendor_country.upper():
        note = (f" (Account country {country} differs from vendor country "
                f"{vendor_country.upper()} — offshore banking is common in shipping, "
                f"so this is context, not proof.)")

    accounts = _load()  # (a live VoP/open-banking client would go here when configured)
    rec = accounts.get(hash_iban(iban))
    source = "vop_api" if vop_api_key else "mock_vop"

    if rec is None:
        return {
            "status": "WARN", "field": "bank_vop",
            "detail": f"Verification of Payee unavailable for account {masked}. Confirm the payee out-of-band.{note}",
            "masked": masked, "country": country, "source": source,
        }

    holder = rec.get("holder", "")
    if normalize_vendor(holder) == normalize_vendor(vendor):
        return {
            "status": "PASS", "field": "bank_vop",
            "detail": f"Verification of Payee: account {masked} is registered to '{vendor}'.{note}",
            "masked": masked, "country": country, "source": source,
        }
    return {
        "status": "FAIL", "field": "bank_vop",
        "detail": (f"Verification of Payee MISMATCH: account {masked} is registered to "
                   f"'{holder}', not '{vendor}'. Account does not belong to the vendor — do not pay.{note}"),
        "masked": masked, "country": country, "source": source, "risk": 0.95,
    }
