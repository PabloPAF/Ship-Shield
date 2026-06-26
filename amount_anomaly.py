"""
Layer 2 (addendum) — per-vendor invoice-amount anomaly (pandas baseline).

Compares an invoice amount against that vendor's own historical distribution and
flags a statistical outlier (|z| > 3) as REVIEW — e.g. a normally ~€31k vendor
suddenly billing €250k. This is a *review* signal, never a hard block, and it only
fires when the vendor has enough history (≥4 samples).

Uses pandas to derive the mean/σ baseline (the project's existing data lever).
Degrades gracefully: if pandas is unavailable the layer is skipped (returns None),
so the FastAPI app imports fine without it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

try:
    import pandas as pd
    _PANDAS = True
except ImportError:
    _PANDAS = False

BASE_PATH = Path(__file__).parent / "data" / "vendor_baselines.json"
_MIN_SAMPLES = 4
_Z_THRESHOLD = 3.0


def _parse_amount(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^0-9.]", "", str(value))  # "31,800.00" / "EUR 250,000" -> digits
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _baselines() -> dict:
    try:
        return json.loads(BASE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def assess_amount(vendor: str, amount) -> dict | None:
    """Return a check dict (field 'amount_anomaly') or None when not applicable
    (no pandas, no amount, or insufficient vendor history)."""
    if not _PANDAS:
        return None
    amt = _parse_amount(amount)
    if amt is None:
        return None

    samples = _baselines().get(vendor)
    if not isinstance(samples, list) or len(samples) < _MIN_SAMPLES:
        return None

    s = pd.Series(samples, dtype="float64")
    mean, std = float(s.mean()), float(s.std())
    if std == 0 or pd.isna(std):
        return None

    z = abs(amt - mean) / std
    if z > _Z_THRESHOLD:
        return {
            "status": "WARN", "field": "amount_anomaly",
            "detail": (f"Invoice amount {amt:,.0f} is a statistical outlier for '{vendor}' "
                       f"(baseline mean {mean:,.0f}, σ {std:,.0f}; z={z:.1f}). "
                       f"Possible inflated or altered amount — review."),
            "z": round(z, 1),
        }
    return {
        "status": "PASS", "field": "amount_anomaly",
        "detail": f"Amount {amt:,.0f} is within '{vendor}' normal range (mean {mean:,.0f} ± {std:,.0f}).",
        "z": round(z, 1),
    }
