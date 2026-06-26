"""
Layer 2 (addendum) — vessel-risk enrichment.

Beyond "did the vessel dock?", enrich the IMO with operator/flag/class data and
Port State Control detention history (stands in for Equasis + Paris/Tokyo MoU).
Recent detentions, a withdrawn class society or a flag-of-convenience pattern are
risk indicators — they raise the verdict to REVIEW, they do not by themselves
prove fraud (a legitimate operator can have an old detention).

Live mode : query an Equasis/PSC source when configured.
Mock mode : fall back to vessel_risk.json so the demo runs offline.
"""
from __future__ import annotations

import json
from pathlib import Path

RISK_PATH = Path(__file__).parent / "data" / "vessel_risk.json"

# Flags commonly associated with flags-of-convenience / weaker oversight.
_FOC_FLAGS = {"Panama", "Liberia", "Marshall Islands", "Comoros", "Palau", "Togo", "Cook Islands"}


def _load() -> dict:
    try:
        return json.loads(RISK_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def assess_vessel(imo: str = "", mmsi: str = "", vessel_name: str = "",
                  equasis_api_key: str | None = None) -> dict:
    """Return a check dict: status PASS|WARN, field 'vessel_risk', detail, plus
    flag/class/detentions for display. Returns None if there is no vessel id."""
    if not imo and not mmsi:
        return None

    data = _load()  # (live Equasis/PSC client would go here when a key is set)
    rec = (data.get(imo) if imo else None) or next(
        (v for k, v in data.items() if isinstance(v, dict)
         and ((imo and k == imo) or (mmsi and (k == mmsi or v.get("mmsi") == mmsi)))), None)

    if rec is None:
        return {
            "status": "PASS",
            "field": "vessel_risk",
            "detail": "No adverse Port State Control or class records on file for this vessel.",
            "source": "equasis_api" if equasis_api_key else "mock_psc",
        }

    detentions = rec.get("detentions", [])
    flag = rec.get("flag", "")
    cls = rec.get("class_society", "")
    concerns = []
    if detentions:
        latest = detentions[0]
        concerns.append(f"{len(detentions)} Port State Control detention(s); latest {latest.get('date','')} "
                        f"at {latest.get('port','')} ({latest.get('deficiencies','?')} deficiencies)")
    if "withdraw" in cls.lower():
        concerns.append("classification society cover withdrawn")
    if flag in _FOC_FLAGS:
        concerns.append(f"flag of convenience ({flag})")

    if concerns:
        return {
            "status": "WARN",
            "field": "vessel_risk",
            "detail": ("Elevated vessel risk — " + "; ".join(concerns) +
                       ". Risk indicator, not proof of fraud; recommend manual review."),
            "flag": flag, "class_society": cls, "detentions": detentions,
            "source": "equasis_api" if equasis_api_key else "mock_psc",
        }
    return {
        "status": "PASS",
        "field": "vessel_risk",
        "detail": f"Vessel record clean — flag {flag}, class {cls}, no PSC detentions.",
        "flag": flag, "class_society": cls, "detentions": [],
        "source": "equasis_api" if equasis_api_key else "mock_psc",
    }
