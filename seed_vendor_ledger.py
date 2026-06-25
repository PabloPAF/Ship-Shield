"""
Seed / rebuild vendor_ledger.json from the confirmed carrier accounts in
maritime_registry.json.

Stores ONLY peppered HMAC-SHA256 hashes of each IBAN (plus country code + dates),
never the raw IBAN, so the resulting file is safe to commit. Re-run this whenever
you add or confirm a vendor's bank account.

    python seed_vendor_ledger.py
"""
import json
from datetime import date
from pathlib import Path

from vendor_ledger import LEDGER_PATH, hash_iban, iban_country, normalize_vendor

REGISTRY_PATH = Path(__file__).parent / "maritime_registry.json"


def main():
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    today = date.today().isoformat()
    ledger: dict = {}

    for v in registry.values():
        vendor = v.get("carrier")
        iban = v.get("registered_iban")
        if not vendor or not iban:
            continue
        vkey = normalize_vendor(vendor)
        ledger.setdefault(vkey, {"display_name": vendor, "ibans": {}})
        ledger[vkey]["ibans"][hash_iban(iban)] = {
            "confirmed": True,            # registry accounts are treated as verified
            "country": iban_country(iban),
            "first_seen": today,
            "last_seen": today,
        }

    LEDGER_PATH.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    n_accts = sum(len(e["ibans"]) for e in ledger.values())
    print(f"Wrote {LEDGER_PATH.name}: {len(ledger)} vendors, {n_accts} confirmed accounts (hashes only).")


if __name__ == "__main__":
    main()
