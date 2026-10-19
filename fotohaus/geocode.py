"""Offline reverse geocoding: GPS coordinates → "Zermatt, Valais, Switzerland".

Uses a bundled list of ~145,000 places with a population over 1,000 (GeoNames, CC BY 4.0), so no
location ever leaves the Mac.
"""
from __future__ import annotations

import gzip
import json
import math
import threading
from pathlib import Path

import numpy as np

_DATA = Path(__file__).with_name("data")
_lock = threading.Lock()
_db = None


def _load():
    global _db
    with _lock:
        if _db is None:
            z = np.load(_DATA / "places_coords.npz")
            lat = np.radians(z["lat"].astype(np.float64))
            lon = np.radians(z["lon"].astype(np.float64))
            with gzip.open(_DATA / "places_names.txt.gz", "rt", encoding="utf-8") as f:
                names = f.read().split("\n")
            countries = json.loads((_DATA / "countries.json").read_text(encoding="utf-8"))
            # unit vectors make "nearest place" a single dot product
            xyz = np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], axis=1)
            _db = (xyz, names, countries)
    return _db


def available() -> bool:
    return (_DATA / "places_coords.npz").exists()


def lookup(lat: float, lon: float, max_km: float = 60.0) -> dict | None:
    """Nearest town. Returns {'city', 'region', 'country', 'cc', 'km'} or None when nothing is close."""
    if lat is None or lon is None or not available():
        return None
    xyz, names, countries = _load()
    la, lo = math.radians(lat), math.radians(lon)
    v = np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)])
    i = int(np.argmax(xyz @ v))
    dot = float(np.clip(xyz[i] @ v, -1.0, 1.0))
    km = math.acos(dot) * 6371.0
    if km > max_km:
        return None
    city, region, cc = (names[i].split("\t") + ["", "", ""])[:3]
    return {"city": city, "region": region, "country": countries.get(cc, cc), "cc": cc, "km": km}


def label(lat: float, lon: float) -> str:
    """'Zermatt, Switzerland' (or '' when the point is far from any town, e.g. at sea)."""
    p = lookup(lat, lon)
    if not p:
        return ""
    return ", ".join(x for x in (p["city"], p["country"]) if x)


def full_label(lat: float, lon: float) -> str:
    """'Zermatt, Valais, Switzerland' — used for searching."""
    p = lookup(lat, lon)
    if not p:
        return ""
    parts = [p["city"]]
    if p["region"] and p["region"] != p["city"]:
        parts.append(p["region"])
    parts.append(p["country"])
    return ", ".join(x for x in parts if x)
