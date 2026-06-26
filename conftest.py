"""Put the repo root on sys.path so tests/ can import the top-level modules
(bl_scanner_app, vendor_ledger, …) when pytest is run from the project root."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
