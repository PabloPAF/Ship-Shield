import asyncio
import json
import os
import requests
from datetime import datetime, timedelta

REGISTRY_PATH = os.path.join(os.path.dirname(__file__), "data", "maritime_registry.json")
MT_BASE = "https://services.marinetraffic.com/api"

_AIS_WS_URL = "wss://stream.aisstream.io/v0/stream"

# Port bounding boxes [[SW_lat, SW_lon], [NE_lat, NE_lon]] for AISStream subscriptions.
_AIS_PORTS = {
    "Port of Rotterdam":   [[51.85,  3.95],   [52.05,  4.55]],
    "Port of Antwerp":     [[51.20,  4.20],   [51.40,  4.45]],
    "Port of Hamburg":     [[53.45,  9.80],   [53.60, 10.10]],
    "Port of Singapore":   [[ 1.18, 103.60],  [ 1.32, 104.05]],
    "Port of Los Angeles": [[33.68, -118.30], [33.78, -118.20]],
    "Port of Barcelona":   [[41.30,  2.10],   [41.40,  2.25]],
}


async def _ais_stream_check_async(mmsi: str, port: str, window_seconds: int, api_key: str) -> dict:
    """Subscribe to AISStream and return whether `mmsi` broadcasts inside the port bbox."""
    try:
        import websockets
    except ImportError:
        return {"found": None, "fallback": True, "error": "websockets not installed — run: pip install websockets"}

    bbox = _AIS_PORTS.get(port)
    if bbox is None:
        return {"found": None, "fallback": True, "error": f"Port '{port}' not in AIS bounding-box reference"}

    subscribe_msg = {
        "APIKey": api_key,
        "BoundingBoxes": [bbox],
        "FiltersShipMMSI": [str(mmsi)],
        "FilterMessageTypes": ["PositionReport"],
    }
    try:
        async with websockets.connect(_AIS_WS_URL) as ws:
            await ws.send(json.dumps(subscribe_msg))
            loop = asyncio.get_running_loop()
            deadline = loop.time() + window_seconds
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                msg = json.loads(raw)
                if msg.get("MessageType") != "PositionReport":
                    continue
                meta = msg.get("MetaData", {})
                report = msg["Message"]["PositionReport"]
                seen_mmsi = str(meta.get("MMSI", report.get("UserID", "")))
                if seen_mmsi != str(mmsi):
                    continue
                return {
                    "found": True, "fallback": False, "error": None,
                    "ship_name": meta.get("ShipName", "Unknown").strip(),
                    "latitude": report.get("Latitude"),
                    "longitude": report.get("Longitude"),
                    "speed_knots": report.get("Sog"),
                }
    except Exception as e:
        return {"found": None, "fallback": True, "error": str(e)}
    return {"found": False, "fallback": False, "error": None}


def _run_ais_stream_check(mmsi: str, port: str, window_seconds: int, api_key: str) -> dict:
    """Sync wrapper — runs the AISStream async check in a dedicated event loop."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                _ais_stream_check_async(mmsi, port, window_seconds, api_key)
            )
        finally:
            loop.close()
    except Exception as e:
        return {"found": None, "fallback": True, "error": str(e)}


def _load_registry() -> dict:
    with open(REGISTRY_PATH) as f:
        return json.load(f)


def _normalize_port(name: str) -> str:
    return name.lower().replace("port of ", "").replace("  ", " ").strip()


def _date_diff_days(date1_str: str, date2_str: str):
    """Return absolute day difference between two ISO date strings, or None if unparseable."""
    try:
        return abs((datetime.fromisoformat(date1_str) - datetime.fromisoformat(date2_str)).days)
    except (ValueError, TypeError):
        return None


def _live_port_check(mmsi: str, claimed_port: str, invoice_date: str, api_key: str) -> dict:
    """
    Query MarineTraffic /portcalls. On network/API error returns fallback=True
    so the caller can silently fall back to the local registry instead of
    raising a false HIGH RISK verdict.
    """
    try:
        date = datetime.fromisoformat(invoice_date)
    except (ValueError, TypeError):
        date = datetime.utcnow()

    params = {
        "v": 6,
        "mmsi": mmsi,
        "fromdate": (date - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%S"),
        "todate": (date + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S"),
        "movetype": 0,
        "msgtype": "simple",
        "protocol": "json",
    }

    try:
        resp = requests.get(
            f"{MT_BASE}/portcalls/{api_key}",
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        calls = data if isinstance(data, list) else data.get("portcalls", data.get("data", []))
        ports = [c.get("PORT_NAME", "") for c in calls if c.get("PORT_NAME")]
        found = any(_normalize_port(p) == _normalize_port(claimed_port) for p in ports)
        return {"found": found, "actual_ports": ports, "fallback": False, "error": None}
    except Exception as e:
        return {"found": None, "actual_ports": [], "fallback": True, "error": str(e)}


def telemetry_context_validation(
    extracted_invoice_json: dict,
    marinetraffic_api_key: str = None,
    aisstream_api_key: str = None,
    ais_window_seconds: int = 30,
) -> dict:
    """
    Validate invoice logistics identifiers against AIS data.

    Verdict values:
        CLEAR   — all checks passed
        BLOCKED — at least one hard mismatch (port, IBAN, date, late submission)
        REVIEW  — API unavailable or missing IBAN on file; needs manual check

    Live mode  (marinetraffic_api_key provided): port verified via MarineTraffic
                /portcalls; falls back to local registry if the API is unavailable.
    Mock mode  (no API key): all checks use maritime_registry.json.

    Invoice payload fields:
        mmsi              - 9-digit vessel MMSI (preferred)
        imo               - 7-digit IMO number (fallback)
        discharge_port    - claimed port of discharge
        invoice_date      - ISO date string (date window check + portcalls query)
        submission_date   - ISO date string when invoice was submitted (Case D)
        iban              - beneficiary bank account
        cargo_type        - cargo description, e.g. "grain", "crude_oil" (Case B)
        voyage_id         - voyage identifier for duplicate detection (Case B)
        cargo_quantity_mt - cargo weight in metric tonnes for DWT check (Case C)
    """
    mmsi = extracted_invoice_json.get("mmsi", "").strip()
    imo = extracted_invoice_json.get("imo", "").strip()
    claimed_port = extracted_invoice_json.get("discharge_port", "").strip()
    claimed_iban = extracted_invoice_json.get("iban", "").strip()
    invoice_date = extracted_invoice_json.get("invoice_date", "").strip()
    submission_date = extracted_invoice_json.get("submission_date", "").strip()
    cargo_type = extracted_invoice_json.get("cargo_type", "").strip().lower()
    voyage_id = extracted_invoice_json.get("voyage_id", "").strip()
    cargo_quantity_mt = extracted_invoice_json.get("cargo_quantity_mt")

    checks = []
    risk_score = 0.0
    is_tampered = False
    needs_review = False

    if not mmsi and not imo:
        return {
            "is_tampered": True,
            "verdict": "BLOCKED",
            "risk_score": 1.0,
            "overall_reason": "No vessel identifier provided — MMSI or IMO required.",
            "checks": [{"field": "mmsi", "status": "FAIL",
                        "detail": "MMSI or IMO is required for telemetry validation."}],
            "vessel_name": None,
            "carrier": None,
            "dock_date": None,
            "source": "none",
        }

    use_mt = bool(marinetraffic_api_key)
    use_ais = bool(aisstream_api_key) and not use_mt
    use_live = use_mt or use_ais
    source = "marinetraffic_api" if use_mt else ("aisstream_live" if use_ais else "mock_registry")
    identifier = mmsi or imo

    registry = _load_registry()
    registry_entry = (
        registry.get(mmsi)
        or registry.get(imo)
        or next((v for v in registry.values()
                 if v.get("mmsi") == mmsi or v.get("imo") == imo), {})
    )

    if not use_live and not registry_entry:
        return {
            "is_tampered": True,
            "verdict": "BLOCKED",
            "risk_score": 1.0,
            "overall_reason": f"Vessel MMSI '{identifier}' not found in registry.",
            "checks": [{"field": "mmsi", "status": "FAIL",
                        "detail": f"MMSI '{identifier}' is not registered."}],
            "vessel_name": None,
            "carrier": None,
            "dock_date": None,
            "source": source,
        }

    vessel_name = registry_entry.get("vessel_name")
    carrier = registry_entry.get("carrier")
    dock_date = registry_entry.get("dock_date")

    # --- Port of discharge ---
    def _registry_port_fallback(claimed, registry_entry, api_label):
        """Shared fallback: check claimed port against local registry when a live API is unavailable."""
        nonlocal is_tampered, risk_score, needs_review
        if registry_entry:
            registry_port = registry_entry.get("last_docked_port", "")
            if _normalize_port(claimed) == _normalize_port(registry_port):
                checks.append({
                    "field": "discharge_port",
                    "status": "WARN",
                    "detail": f"Port '{claimed}' matches local registry ({api_label} unavailable — using fallback).",
                })
                needs_review = True
            else:
                checks.append({
                    "field": "discharge_port",
                    "status": "FAIL",
                    "detail": f"Invoice claims '{claimed}'. Registry shows: '{registry_port}'. ({api_label} unavailable — verified via fallback)",
                })
                is_tampered = True
                risk_score = max(risk_score, 0.80)
        else:
            checks.append({
                "field": "discharge_port",
                "status": "WARN",
                "detail": f"{api_label} unavailable and vessel not in local registry. Manual review required.",
            })
            needs_review = True

    if claimed_port:
        if use_mt:
            result = _live_port_check(identifier, claimed_port, invoice_date, marinetraffic_api_key)
            if result["fallback"]:
                _registry_port_fallback(claimed_port, registry_entry, "MarineTraffic API")
            elif result["found"]:
                checks.append({
                    "field": "discharge_port",
                    "status": "PASS",
                    "detail": f"Port '{claimed_port}' confirmed via MarineTraffic AIS.",
                })
            else:
                actual = ", ".join(result["actual_ports"][:3]) or "none on record"
                checks.append({
                    "field": "discharge_port",
                    "status": "FAIL",
                    "detail": f"Invoice claims '{claimed_port}'. AIS recent ports: {actual}.",
                })
                is_tampered = True
                risk_score = max(risk_score, 0.80)

        elif use_ais:
            result = _run_ais_stream_check(identifier, claimed_port, ais_window_seconds, aisstream_api_key)
            if result["fallback"]:
                _registry_port_fallback(claimed_port, registry_entry, f"AISStream ({result.get('error', 'unavailable')})")
            elif result["found"]:
                loc = ""
                if result.get("latitude") is not None:
                    loc = f" at ({result['latitude']:.4f}, {result['longitude']:.4f}), speed {result.get('speed_knots')} kn"
                checks.append({
                    "field": "discharge_port",
                    "status": "PASS",
                    "detail": f"Vessel {result.get('ship_name', identifier)} confirmed live in '{claimed_port}' via AISStream{loc}.",
                })
            else:
                checks.append({
                    "field": "discharge_port",
                    "status": "FAIL",
                    "detail": (
                        f"Vessel MMSI {identifier} did NOT appear in '{claimed_port}' within "
                        f"{ais_window_seconds}s of live AIS. Claimed discharge port unverified "
                        f"— HIGH FRAUD RISK (possible VEC port swap)."
                    ),
                })
                is_tampered = True
                risk_score = max(risk_score, 0.80)

        else:
            registry_port = registry_entry.get("last_docked_port", "")
            if _normalize_port(claimed_port) != _normalize_port(registry_port):
                checks.append({
                    "field": "discharge_port",
                    "status": "FAIL",
                    "detail": f"Invoice claims '{claimed_port}'. Registry shows: '{registry_port}'.",
                })
                is_tampered = True
                risk_score = max(risk_score, 0.80)
            else:
                checks.append({
                    "field": "discharge_port",
                    "status": "PASS",
                    "detail": f"Port confirmed: '{registry_port}'.",
                })

    # --- Case A: invoice date vs AIS dock date ---
    # MarineTraffic enforces this implicitly via the portcalls date window.
    # AISStream and mock mode check explicitly against the registry dock_date.
    if not use_mt and invoice_date and dock_date:
        diff = _date_diff_days(invoice_date, dock_date)
        if diff is not None:
            if diff > 2:
                checks.append({
                    "field": "invoice_date",
                    "status": "FAIL",
                    "detail": (
                        f"Invoice date {invoice_date} is {diff} days from AIS dock date "
                        f"{dock_date}. Possible post/ante-dating (Case A)."
                    ),
                })
                is_tampered = True
                risk_score = max(risk_score, 0.55)
            else:
                checks.append({
                    "field": "invoice_date",
                    "status": "PASS",
                    "detail": f"Invoice date within ±2 days of confirmed dock date ({dock_date}).",
                })

    # --- Case B: vessel type vs cargo type incompatibility ---
    _TANKER_CARGOES = {"crude_oil", "lng", "lpg", "chemical", "oil_products", "fuel_oil"}
    _BULK_CARGOES = {"grain", "coal", "ore", "fertiliser", "cement", "scrap", "bauxite"}

    if cargo_type and registry_entry:
        vessel_type = registry_entry.get("vessel_type", "").lower()
        if vessel_type == "tanker" and cargo_type in _BULK_CARGOES:
            checks.append({
                "field": "cargo_type",
                "status": "FAIL",
                "detail": (
                    f"Tanker vessel '{vessel_name}' invoiced for dry bulk cargo '{cargo_type}' "
                    f"— physically incompatible (Case B)."
                ),
            })
            is_tampered = True
            risk_score = max(risk_score, 0.75)
        elif vessel_type == "bulk_carrier" and cargo_type in _TANKER_CARGOES:
            checks.append({
                "field": "cargo_type",
                "status": "FAIL",
                "detail": (
                    f"Bulk carrier '{vessel_name}' invoiced for liquid cargo '{cargo_type}' "
                    f"— physically incompatible (Case B)."
                ),
            })
            is_tampered = True
            risk_score = max(risk_score, 0.75)
        elif vessel_type:
            checks.append({
                "field": "cargo_type",
                "status": "PASS",
                "detail": f"Cargo type '{cargo_type}' is compatible with vessel type '{vessel_type}'.",
            })

    # --- Case B: voyage ID duplicate detection ---
    if voyage_id and registry_entry:
        invoiced_voyages = registry_entry.get("invoiced_voyages", [])
        if voyage_id in invoiced_voyages:
            checks.append({
                "field": "voyage_id",
                "status": "FAIL",
                "detail": (
                    f"Voyage '{voyage_id}' has already been invoiced — "
                    f"possible duplicate cargo sale or ghost shipment (Case B)."
                ),
            })
            is_tampered = True
            risk_score = max(risk_score, 0.85)
        else:
            checks.append({
                "field": "voyage_id",
                "status": "PASS",
                "detail": f"Voyage ID '{voyage_id}' not previously invoiced.",
            })

    # --- Case C: DWT capacity check ---
    if cargo_quantity_mt is not None and registry_entry:
        max_dwt = registry_entry.get("deadweight_tonnes")
        if max_dwt:
            try:
                qty = float(cargo_quantity_mt)
                if qty > max_dwt:
                    checks.append({
                        "field": "cargo_quantity_mt",
                        "status": "FAIL",
                        "detail": (
                            f"Invoice claims {qty:,.0f} MT but vessel DWT is {max_dwt:,} MT "
                            f"— physically impossible (Case C)."
                        ),
                    })
                    is_tampered = True
                    risk_score = max(risk_score, 0.70)
                else:
                    checks.append({
                        "field": "cargo_quantity_mt",
                        "status": "PASS",
                        "detail": (
                            f"Cargo quantity {qty:,.0f} MT is within vessel DWT capacity "
                            f"({max_dwt:,} MT)."
                        ),
                    })
            except (ValueError, TypeError):
                pass

    # --- Case D: late submission check ---
    if submission_date and dock_date:
        try:
            days_after = (datetime.fromisoformat(submission_date) - datetime.fromisoformat(dock_date)).days
            if days_after > 14:
                checks.append({
                    "field": "submission_date",
                    "status": "FAIL",
                    "detail": (
                        f"Invoice submitted {days_after} days after vessel departure ({dock_date}). "
                        f"Late submission is a key indicator of fake agency invoices (Case D)."
                    ),
                })
                is_tampered = True
                risk_score = max(risk_score, 0.50)
            else:
                checks.append({
                    "field": "submission_date",
                    "status": "PASS",
                    "detail": f"Invoice submitted {days_after} days after vessel departure — within normal window.",
                })
        except (ValueError, TypeError):
            pass

    # --- IBAN check (always local — MarineTraffic carries no bank data) ---
    if claimed_iban:
        registry_iban = registry_entry.get("registered_iban", "")
        if registry_iban:
            if claimed_iban.replace(" ", "").upper() != registry_iban.replace(" ", "").upper():
                checks.append({
                    "field": "iban",
                    "status": "FAIL",
                    "detail": "IBAN on invoice does not match registered carrier IBAN. Possible account hijack.",
                })
                is_tampered = True
                risk_score = max(risk_score, 0.90)
            else:
                checks.append({
                    "field": "iban",
                    "status": "PASS",
                    "detail": "IBAN matches registered carrier account.",
                })
        else:
            checks.append({
                "field": "iban",
                "status": "WARN",
                "detail": "No registered IBAN on file for this vessel — IBAN verification skipped.",
            })
            needs_review = True

    if not checks:
        checks.append({
            "field": "general",
            "status": "PASS",
            "detail": f"Vessel MMSI '{identifier}' found. No additional fields to verify.",
        })

    # --- Derive verdict ---
    if is_tampered:
        verdict = "BLOCKED"
        failed = [c for c in checks if c["status"] == "FAIL"]
        overall_reason = " | ".join(c["detail"] for c in failed)
    elif needs_review:
        verdict = "REVIEW"
        risk_score = max(risk_score, 0.3)
        warned = [c for c in checks if c["status"] == "WARN"]
        overall_reason = " | ".join(c["detail"] for c in warned) or "Unverifiable — manual review required."
    else:
        verdict = "CLEAR"
        risk_score = 0.0
        overall_reason = "All telemetry checks passed. Physical event confirmed."

    return {
        "is_tampered": is_tampered,
        "verdict": verdict,
        "risk_score": round(risk_score, 2),
        "overall_reason": overall_reason,
        "checks": checks,
        "vessel_name": vessel_name,
        "carrier": carrier,
        "dock_date": dock_date,
        "source": source,
    }
