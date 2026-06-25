"""
Vendor bank-account (IBAN) ledger — secure change-detection layer.

Why this exists
---------------
Vendor Email Compromise (VEC) fraud almost always shows up as a *change of bank
account* on an existing supplier relationship: the invoice looks normal, but the
IBAN has been swapped for the attacker's. A static "registered IBAN" list only
helps if you already know every carrier's account. This ledger generalises that:
it remembers which account(s) each vendor has historically used and flags the
first time a *new* account appears for a *known* vendor.

Privacy & security by design (GDPR)
-----------------------------------
* Raw IBANs are NEVER stored. We store a keyed hash — HMAC-SHA256(IBAN, pepper).
* The pepper is a server-side secret (env var SHIPSHIELD_LEDGER_PEPPER or
  .streamlit/secrets.toml). Without it, the stored hashes cannot be reversed or
  brute-forced offline — IBANs have low entropy, so a plain unsalted hash could
  be enumerated; a secret-keyed HMAC prevents that. This means vendor_ledger.json
  is safe to commit: the hashes are useless without the pepper.
* We keep only the data minimally needed to detect a change: the IBAN hash, the
  2-letter country code (for the country-change heuristic), first/last seen
  timestamps, and a "confirmed" flag (set when an account has been verified
  out-of-band). No names of natural persons, no account holders, no balances.

This module is read-only at request time (it does not mutate the ledger during a
cross-check), so verdicts are deterministic. Use seed_vendor_ledger.py to build
or update the ledger with confirmed accounts.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from datetime import date
from pathlib import Path

LEDGER_PATH = Path(__file__).parent / "vendor_ledger.json"
SECRETS_PATH = Path(__file__).parent / ".streamlit" / "secrets.toml"

# Dev-only fallback pepper. Production MUST set SHIPSHIELD_LEDGER_PEPPER so that
# the committed demo ledger remains consistent in development, while real
# deployments use a secret nobody can read from the repo.
_DEV_PEPPER = "shipshield-dev-pepper-CHANGE-ME"


def _read_pepper() -> str:
    val = os.environ.get("SHIPSHIELD_LEDGER_PEPPER")
    if val:
        return val
    try:
        import tomllib
        with open(SECRETS_PATH, "rb") as f:
            secrets = tomllib.load(f)
        if secrets.get("SHIPSHIELD_LEDGER_PEPPER"):
            return secrets["SHIPSHIELD_LEDGER_PEPPER"]
    except (FileNotFoundError, ModuleNotFoundError, Exception):
        pass
    return _DEV_PEPPER


def normalize_iban(iban: str) -> str:
    return re.sub(r"\s+", "", (iban or "")).upper()


def iban_country(iban: str) -> str:
    """First two letters of an IBAN are the ISO 3166 country code."""
    n = normalize_iban(iban)
    return n[:2] if len(n) >= 2 and n[:2].isalpha() else ""


def hash_iban(iban: str) -> str:
    """Irreversible, secret-keyed fingerprint of an IBAN. Never store the raw value."""
    pepper = _read_pepper().encode()
    return hmac.new(pepper, normalize_iban(iban).encode(), hashlib.sha256).hexdigest()


def mask_iban(iban: str) -> str:
    """Display-only mask computed from the live (cleartext) IBAN, e.g. 'GB29 **** 6819'.
    Derived at processing time from the incoming document — never read back from storage."""
    n = normalize_iban(iban)
    if len(n) < 8:
        return "****"
    return f"{n[:4]} **** {n[-4:]}"


def normalize_vendor(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "")).strip().lower()


def _load_ledger() -> dict:
    try:
        with open(LEDGER_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def check_vendor_iban(vendor: str, iban: str, ledger: dict | None = None) -> dict:
    """
    Read-only bank-account change-detection for one (vendor, IBAN) pair.

    Returns a dict:
        status   : PASS | WARN | FAIL
        field    : "vendor_bank_account"
        detail   : human-readable explanation
        masked   : masked IBAN for display (from cleartext, not storage)
        country  : IBAN country code
    """
    if not iban:
        return None  # nothing to check (e.g. a Bill of Lading carries no IBAN)

    ledger = ledger if ledger is not None else _load_ledger()
    vkey = normalize_vendor(vendor)
    h = hash_iban(iban)
    country = iban_country(iban)
    masked = mask_iban(iban)

    entry = ledger.get(vkey)

    # Unknown vendor — no baseline yet. Not fraud, but cannot be auto-trusted.
    if not entry or not entry.get("ibans"):
        return {
            "status": "WARN",
            "field": "vendor_bank_account",
            "detail": (
                f"First invoice on record from '{vendor}'. No bank-account baseline "
                f"yet — confirm account {masked} out-of-band before paying, then add it "
                f"to the ledger."
            ),
            "masked": masked,
            "country": country,
        }

    known = entry["ibans"]  # {hash: {"confirmed": bool, "country": "NL", ...}}

    if h in known:
        rec = known[h]
        if rec.get("confirmed"):
            return {
                "status": "PASS",
                "field": "vendor_bank_account",
                "detail": f"IBAN {masked} matches the confirmed account on file for '{vendor}'.",
                "masked": masked,
                "country": country,
            }
        return {
            "status": "WARN",
            "field": "vendor_bank_account",
            "detail": (
                f"IBAN {masked} has been seen before for '{vendor}' but is not yet "
                f"out-of-band confirmed."
            ),
            "masked": masked,
            "country": country,
        }

    # KNOWN vendor, UNSEEN account → bank-detail change. Primary VEC indicator.
    prior_countries = sorted({r.get("country", "") for r in known.values() if r.get("country")})
    country_note = ""
    if country and prior_countries and country not in prior_countries:
        country_note = (
            f" Account country '{country}' also differs from this vendor's prior "
            f"country(ies) {prior_countries} — note: offshore banking is common in "
            f"shipping, so treat this as a flag, not proof."
        )
    return {
        "status": "FAIL",
        "field": "vendor_bank_account",
        "detail": (
            f"NEW bank account {masked} for an existing vendor '{vendor}'. The account "
            f"on this invoice has never been used by this vendor before — classic "
            f"account-substitution (VEC). Do NOT pay until confirmed out-of-band via a "
            f"known contact (not a number from this email).{country_note}"
        ),
        "masked": masked,
        "country": country,
        "risk": 0.90,
    }
