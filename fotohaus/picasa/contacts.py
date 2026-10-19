"""Picasa's global contacts.xml (face-tag id -> person name)."""
from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def find_picasa_contacts_files() -> list[Path]:
    """Default locations where Picasa 3 kept contacts.xml."""
    home = Path.home()
    cands = [
        home / "Library/Application Support/Google/Picasa3/contacts/contacts.xml",
        home / "AppData/Local/Google/Picasa2/contacts/contacts.xml",
        home / "Local Settings/Application Data/Google/Picasa2/contacts/contacts.xml",
    ]
    if sys.platform.startswith("win") and os.environ.get("LOCALAPPDATA"):
        cands.insert(0, Path(os.environ["LOCALAPPDATA"]) / "Google/Picasa2/contacts/contacts.xml")
    return [p for p in cands if p.exists()]


def load_contacts_xml(path: str | os.PathLike) -> dict[str, str]:
    """Return {contact_id: display name}."""
    out: dict[str, str] = {}
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return out
    for c in root.iter("contact"):
        cid = (c.get("id") or "").lower()
        name = c.get("name") or c.get("display") or ""
        if cid and name:
            out[cid] = name
    return out
