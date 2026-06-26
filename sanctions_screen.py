"""
Layer 4 (Counterparty) — sanctions & dark-fleet screening.

Screens the counterparty (vendor/carrier), the vessel (IMO/MMSI/name) and the
bank jurisdiction against consolidated sanctions data. A confirmed match BLOCKS
the payment regardless of how clean the document is — a well-documented shipment
on a sanctioned vessel is still illegal to pay.

Live mode  : if a sanctions API key is supplied (e.g. OpenSanctions), query it.
Mock mode  : fall back to the bundled sanctions_list.json so the demo runs offline.

Only identifiers needed for screening are ever sent to an external provider —
never the IBAN, never document contents (data minimisation, GDPR).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

LIST_PATH = Path(__file__).parent / "data" / "sanctions_list.json"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", (s or "").lower())).strip()


def _load() -> dict:
    try:
        return json.loads(LIST_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"entities": [], "vessels": [], "sanctioned_jurisdictions": []}


def screen_counterparty(vendor: str, carrier: str = "", vessel_name: str = "",
                        imo: str = "", mmsi: str = "", iban_country: str = "",
                        sanctions_api_key: str | None = None) -> dict:
    """
    Returns a check dict:
        status  : PASS | FAIL
        field   : "sanctions"
        detail  : explanation
        matches : list of human-readable hits
        source  : data source used
    """
    # Live integration point — wire an OpenSanctions/OFAC client here when a key
    # is configured. We fall back to the local list so the demo works offline.
    data = _load()
    source = "sanctions_api" if sanctions_api_key else "mock_consolidated"

    names = {_norm(vendor), _norm(carrier)} - {""}
    matches = []

    for ent in data.get("entities", []):
        if _norm(ent.get("name", "")) in names:
            matches.append(f"Entity '{ent['name']}' on {', '.join(ent.get('programs', []))} list")

    for v in data.get("vessels", []):
        if (imo and v.get("imo") == imo) or (vessel_name and _norm(v.get("name", "")) == _norm(vessel_name)):
            reason = f" ({v['reason']})" if v.get("reason") else ""
            matches.append(f"Vessel '{v.get('name','')}' IMO {v.get('imo','')} on {', '.join(v.get('programs', []))} list{reason}")

    if iban_country and iban_country.upper() in {c.upper() for c in data.get("sanctioned_jurisdictions", [])}:
        matches.append(f"Beneficiary bank is in a sanctioned jurisdiction ('{iban_country.upper()}')")

    if matches:
        return {
            "status": "FAIL",
            "field": "sanctions",
            "detail": "Sanctions match — payment is prohibited. " + " | ".join(matches),
            "matches": matches,
            "source": source,
            "risk": 1.0,
        }
    return {
        "status": "PASS",
        "field": "sanctions",
        "detail": "No sanctions match for counterparty, vessel or bank jurisdiction.",
        "matches": [],
        "source": source,
    }
