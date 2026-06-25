"""
Seed / rebuild account_registry.json — the (mock) bank-side Verification-of-Payee
directory mapping an account to its registered HOLDER name.

Like vendor_ledger, it stores ONLY peppered HMAC-SHA256 hashes of the IBAN as keys
(never the raw IBAN), so the file is safe to commit. In production this data lives
at the bank / open-banking aggregator; here it is mocked for the offline demo.

    python seed_bank_accounts.py
"""
import json
from pathlib import Path

from vendor_ledger import hash_iban, iban_country

OUT = Path(__file__).parent / "account_registry.json"
REGISTRY = json.loads((Path(__file__).parent / "maritime_registry.json").read_text(encoding="utf-8"))

# Known-good accounts: each carrier's registered account is held by that carrier.
accounts = {}
for v in REGISTRY.values():
    iban, holder = v.get("registered_iban"), v.get("carrier")
    if iban and holder:
        accounts[hash_iban(iban)] = {"holder": holder, "country": iban_country(iban)}

# Mule / attacker accounts seen in fraud (the account is NOT held by the vendor).
FRAUD = {
    "GB29NWBK60161331926819": {"holder": "QuickPay Solutions Ltd", "country": "GB"},
}
for iban, info in FRAUD.items():
    accounts[hash_iban(iban)] = info

OUT.write_text(json.dumps({"_note": "Mock Verification-of-Payee directory. Hash-keyed (HMAC); no raw IBANs.",
                           "accounts": accounts}, indent=2) + "\n", encoding="utf-8")
print(f"Wrote {OUT.name}: {len(accounts)} account holder records (hash-keyed).")
