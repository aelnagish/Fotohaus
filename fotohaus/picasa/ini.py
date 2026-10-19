"""Reader/writer for Picasa's per-folder `.picasa.ini` sidecar files.

Format (community-documented):

    [Picasa]
    name=Folder title
    [.album:7d3c8e...]
    name=Holiday
    token=7d3c8e...
    date=2009-07-14T10:22:31+02:00
    [Contacts2]
    8e62398ebda8c1a5=Jane Doe;;
    [IMG_0001.JPG]
    star=yes
    caption=At the beach
    keywords=sea,summer
    albums=7d3c8e...
    faces=rect64(3f845bcb59418507),8e62398ebda8c1a5;rect64(...),ffffffffffffffff
    filters=crop64=1,45930000ba03defe;enhance=1;
    rotate=rotate(1)

Unknown sections and keys are preserved verbatim on save.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

INI_NAMES = (".picasa.ini", "Picasa.ini", "picasa.ini", ".Picasa.ini")
UNKNOWN_CONTACT = "ffffffffffffffff"


# ----------------------------------------------------------------- rect64

def decode_rect64(hexstr: str) -> tuple[float, float, float, float]:
    """rect64 hex -> (left, top, right, bottom) as fractions 0..1."""
    h = hexstr.strip().lower()
    if h.startswith("rect64(") and h.endswith(")"):
        h = h[7:-1]
    h = h.zfill(16)[-16:]
    vals = [int(h[i:i + 4], 16) / 65536.0 for i in range(0, 16, 4)]
    l, t, r, b = vals
    return (min(l, r), min(t, b), max(l, r), max(t, b))


def encode_rect64(l: float, t: float, r: float, b: float) -> str:
    def q(v: float) -> int:
        return max(0, min(65535, int(round(v * 65536))))
    return "".join(f"{q(v):04x}" for v in (l, t, r, b))


# ----------------------------------------------------------------- filters

@dataclass
class Filter:
    """One entry of a Picasa-style filter chain, e.g. ``crop64=1,45930000ba03defe``."""
    id: str
    args: list[str] = field(default_factory=list)
    enabled: str = "1"

    def floats(self, n: int, defaults: list[float] | None = None) -> list[float]:
        out = []
        defaults = defaults or []
        for i in range(n):
            try:
                out.append(float(self.args[i]))
            except (IndexError, ValueError):
                out.append(defaults[i] if i < len(defaults) else 0.0)
        return out

    def to_str(self) -> str:
        return self.id + "=" + ",".join([self.enabled] + list(self.args))

    @staticmethod
    def make(fid: str, *values) -> "Filter":
        args = []
        for v in values:
            if isinstance(v, float):
                args.append(f"{v:.6f}")
            else:
                args.append(str(v))
        return Filter(fid, args)


def parse_filters(s: str | None) -> list[Filter]:
    out: list[Filter] = []
    if not s:
        return out
    for part in s.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        fid, _, rest = part.partition("=")
        vals = rest.split(",") if rest else []
        enabled = vals[0] if vals else "1"
        out.append(Filter(fid.strip(), [v.strip() for v in vals[1:]], enabled))
    return out


def serialize_filters(filters: list[Filter]) -> str:
    if not filters:
        return ""
    return "".join(f.to_str() + ";" for f in filters)


# ----------------------------------------------------------------- ini file

_SECTION_RE = re.compile(r"^\s*\[(.*)\]\s*$")


class PicasaIni:
    """Order-preserving, case-insensitive (for section names) .picasa.ini file."""

    def __init__(self, folder: str | os.PathLike):
        self.folder = Path(folder)
        self.path = self._locate()
        self.sections: dict[str, dict[str, str]] = {}
        self._keys: dict[str, str] = {}   # lower-case name -> actual section name
        self.newline = "\r\n"
        self.dirty = False
        self.load()

    # -- io
    def _locate(self) -> Path:
        for n in INI_NAMES:
            p = self.folder / n
            if p.exists():
                return p
        return self.folder / ".picasa.ini"

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def mtime(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0

    def load(self) -> None:
        self.sections.clear()
        self._keys.clear()
        if not self.path.exists():
            return
        raw = self.path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")
        self.newline = "\r\n" if "\r\n" in text else "\n"
        current: dict[str, str] | None = None
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith(";"):
                continue
            m = _SECTION_RE.match(line)
            if m:
                current = self.section(m.group(1), create=True)
                continue
            if current is None or "=" not in line:
                continue
            k, _, v = line.partition("=")
            current[k.strip()] = v.strip()
        self.dirty = False

    def save(self) -> None:
        if not self.dirty:
            return
        lines: list[str] = []
        for name, kv in self.sections.items():
            if not kv and not name.startswith("."):
                continue
            lines.append(f"[{name}]")
            for k, v in kv.items():
                lines.append(f"{k}={v}")
        text = self.newline.join(lines) + self.newline
        tmp = self.path.with_name(self.path.name + ".fotohaus-tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, self.path)
        self.dirty = False

    # -- generic access
    def section(self, name: str, create: bool = False) -> dict[str, str] | None:
        key = name.lower()
        if key in self._keys:
            return self.sections[self._keys[key]]
        if not create:
            return None
        self._keys[key] = name
        self.sections[name] = {}
        return self.sections[name]

    def get(self, sec: str, key: str, default: str | None = None) -> str | None:
        s = self.section(sec)
        if s is None:
            return default
        for k, v in s.items():
            if k.lower() == key.lower():
                return v
        return default

    def set(self, sec: str, key: str, value: str | None) -> None:
        s = self.section(sec, create=value is not None)
        if s is None:
            return
        existing = next((k for k in s if k.lower() == key.lower()), None)
        if value is None or value == "":
            if existing is not None:
                del s[existing]
                self.dirty = True
            return
        if existing is not None and s[existing] == value:
            return
        s[existing or key] = value
        self.dirty = True

    def remove_section(self, name: str) -> None:
        key = name.lower()
        if key in self._keys:
            del self.sections[self._keys.pop(key)]
            self.dirty = True

    # -- folder level
    @property
    def folder_name(self) -> str | None:
        return self.get("Picasa", "name")

    def folder_meta(self) -> dict[str, str]:
        return dict(self.section("Picasa") or {})

    # -- photo level
    def photo(self, filename: str) -> dict[str, str]:
        return dict(self.section(filename) or {})

    def star(self, filename: str) -> bool:
        return (self.get(filename, "star") or "").lower() == "yes"

    def set_star(self, filename: str, on: bool) -> None:
        self.set(filename, "star", "yes" if on else None)

    def caption(self, filename: str) -> str:
        return self.get(filename, "caption") or ""

    def keywords(self, filename: str) -> list[str]:
        v = self.get(filename, "keywords") or ""
        return [k.strip() for k in v.split(",") if k.strip()]

    def photo_albums(self, filename: str) -> list[str]:
        v = self.get(filename, "albums") or ""
        return [a.strip() for a in v.split(",") if a.strip()]

    def rotate(self, filename: str) -> int:
        v = self.get(filename, "rotate") or ""
        m = re.search(r"rotate\((-?\d+)\)", v)
        return int(m.group(1)) % 4 if m else 0

    def set_rotate(self, filename: str, quarter_turns: int) -> None:
        q = quarter_turns % 4
        self.set(filename, "rotate", f"rotate({q})" if q else None)

    def faces(self, filename: str) -> list[tuple[tuple[float, float, float, float], str]]:
        v = self.get(filename, "faces") or ""
        out = []
        for part in v.split(";"):
            m = re.match(r"\s*rect64\(([0-9a-fA-F]+)\)\s*,\s*([0-9a-fA-F]+)", part)
            if m:
                out.append((decode_rect64(m.group(1)), m.group(2).lower()))
        return out

    def set_faces(self, filename: str, faces: list[tuple[tuple[float, float, float, float], str]]) -> None:
        if not faces:
            self.set(filename, "faces", None)
            return
        s = ";".join(f"rect64({encode_rect64(*r)}),{cid}" for r, cid in faces)
        self.set(filename, "faces", s)

    def filters(self, filename: str) -> list[Filter]:
        """Full ordered edit chain, merging Fotohaus-only filters back in."""
        picasa = parse_filters(self.get(filename, "filters"))
        ours = self.get(filename, "fotohaus_filters")
        if ours:
            full = parse_filters(ours)
            subset = [f for f in full if not f.id.startswith("fh_")]
            if serialize_filters(subset) == serialize_filters(picasa):
                return full
            # Picasa (or someone) changed the chain: keep theirs, re-append ours.
            return picasa + [f for f in full if f.id.startswith("fh_")]
        return picasa

    def set_filters(self, filename: str, chain: list[Filter]) -> None:
        picasa = [f for f in chain if not f.id.startswith("fh_")]
        self.set(filename, "filters", serialize_filters(picasa) or None)
        if any(f.id.startswith("fh_") for f in chain):
            self.set(filename, "fotohaus_filters", serialize_filters(chain))
        else:
            self.set(filename, "fotohaus_filters", None)

    # -- albums ([.album:token] sections)
    def albums(self) -> dict[str, dict[str, str]]:
        out = {}
        for name, kv in self.sections.items():
            if name.lower().startswith(".album:"):
                token = name.split(":", 1)[1]
                out[token] = dict(kv)
        return out

    def ensure_album(self, token: str, name: str, date: str | None = None) -> None:
        sec = f".album:{token}"
        self.set(sec, "name", name)
        self.set(sec, "token", token)
        if date:
            self.set(sec, "date", date)

    # -- contacts ([Contacts2] / [Contacts])
    def contacts(self) -> dict[str, str]:
        out = {}
        for sec in ("Contacts2", "Contacts"):
            s = self.section(sec)
            if not s:
                continue
            for k, v in s.items():
                name = v.split(";")[0].strip()
                if name:
                    out.setdefault(k.lower(), name)
        return out

    def set_contact(self, contact_id: str, name: str) -> None:
        self.set("Contacts2", contact_id, f"{name};;")

    def photo_sections(self) -> list[str]:
        return [n for n in self.sections
                if not n.startswith(".") and n.lower() not in ("picasa", "contacts", "contacts2", "encoding")]
