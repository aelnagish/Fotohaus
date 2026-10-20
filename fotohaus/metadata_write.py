"""Write captions, tags and face regions into JPEG files (IPTC + XMP), without re-encoding.

Like Picasa, captions and tags go into the file's IPTC record (2:120 caption, 2:25 keywords)
and XMP (dc:description, dc:subject). Name tags can optionally be written as XMP
Metadata Working Group face regions (mwg-rs:Regions), which most photo apps read.
Other IPTC datasets, other Photoshop resources and other XMP properties are preserved.
"""
from __future__ import annotations

import os
import struct
import xml.etree.ElementTree as ET

XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
PS_HEADER = b"Photoshop 3.0\x00"

NS = {
    "x": "adobe:ns:meta/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "mwg-rs": "http://www.metadataworkinggroup.com/schemas/regions/",
    "stDim": "http://ns.adobe.com/xap/1.0/sType/Dimensions#",
    "stArea": "http://ns.adobe.com/xmp/sType/Area#",
    "xmp": "http://ns.adobe.com/xap/1.0/",
    "photoshop": "http://ns.adobe.com/photoshop/1.0/",
    "exif": "http://ns.adobe.com/exif/1.0/",
    "tiff": "http://ns.adobe.com/tiff/1.0/",
    "xml": "http://www.w3.org/XML/1998/namespace",
}
for _p, _u in NS.items():
    if _p != "xml":
        ET.register_namespace(_p, _u)


def _q(prefix, tag):
    return f"{{{NS[prefix]}}}{tag}"


# ------------------------------------------------------------------ JPEG segments

def _segments(data: bytes):
    """Yield (marker, payload_start, payload_end, seg_start) for header segments until SOS."""
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG")
    i = 2
    while i < len(data) - 4:
        if data[i] != 0xFF:
            raise ValueError("bad JPEG marker")
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        ln = struct.unpack(">H", data[i + 2:i + 4])[0]
        yield marker, i + 4, i + 2 + ln, i
        if marker == 0xDA:  # start of scan
            return
        i += 2 + ln


# ------------------------------------------------------------------ IPTC

def _iptc_records(blob: bytes) -> list[tuple[int, int, bytes]]:
    out, i = [], 0
    while i + 5 <= len(blob) and blob[i] == 0x1C:
        rec, ds = blob[i + 1], blob[i + 2]
        ln = struct.unpack(">H", blob[i + 3:i + 5])[0]
        if ln & 0x8000:  # extended length (rare) - stop parsing
            break
        out.append((rec, ds, blob[i + 5:i + 5 + ln]))
        i += 5 + ln
    return out


def _iptc_blob(records) -> bytes:
    b = bytearray()
    for rec, ds, val in records:
        val = val[:32767]
        b += bytes([0x1C, rec, ds]) + struct.pack(">H", len(val)) + val
    return bytes(b)


def _ps_resources(payload: bytes) -> list[tuple[int, bytes, bytes]]:
    """Parse Photoshop IRB: [(resource id, pascal name bytes, data)]."""
    out, i = [], len(PS_HEADER)
    while i + 12 <= len(payload) and payload[i:i + 4] == b"8BIM":
        rid = struct.unpack(">H", payload[i + 4:i + 6])[0]
        nlen = payload[i + 6]
        name_total = 1 + nlen + ((1 + nlen) % 2)
        name = payload[i + 6:i + 6 + name_total]
        j = i + 6 + name_total
        size = struct.unpack(">I", payload[j:j + 4])[0]
        data = payload[j + 4:j + 4 + size]
        out.append((rid, name, data))
        i = j + 4 + size + (size % 2)
    return out


def _ps_payload(resources) -> bytes:
    b = bytearray(PS_HEADER)
    for rid, name, data in resources:
        b += b"8BIM" + struct.pack(">H", rid) + (name or b"\x00\x00")
        b += struct.pack(">I", len(data)) + data
        if len(data) % 2:
            b += b"\x00"
    return bytes(b)


def build_iptc(existing: bytes | None, caption: str, keywords: list[str]) -> bytes:
    recs = _iptc_records(existing) if existing else []
    recs = [r for r in recs if not (r[0] == 2 and r[1] in (0, 120, 25, 90))]
    new = [(1, 90, b"\x1b%G")]  # coded character set = UTF-8
    new += [(2, 0, b"\x00\x04")]  # record version
    if caption:
        new.append((2, 120, caption.encode("utf-8")[:2000]))
    for k in keywords:
        if k:
            new.append((2, 25, k.encode("utf-8")[:64]))
    others = [r for r in recs if r[0] != 1 or r[1] != 90]
    return _iptc_blob(new[:1] + [r for r in others if r[0] == 1] + new[1:] + [r for r in others if r[0] != 1])


# ------------------------------------------------------------------ XMP

def build_xmp(existing: bytes | None, caption: str, keywords: list[str], regions=None, dims=None) -> bytes:
    root = None
    if existing:
        try:
            txt = existing.decode("utf-8", "replace")
            s, e = txt.find("<x:xmpmeta"), txt.rfind("</x:xmpmeta>")
            if s >= 0 and e > s:
                root = ET.fromstring(txt[s:e + len("</x:xmpmeta>")])
        except ET.ParseError:
            root = None
    if root is None:
        root = ET.Element(_q("x", "xmpmeta"))
    rdf = root.find(_q("rdf", "RDF"))
    if rdf is None:
        rdf = ET.SubElement(root, _q("rdf", "RDF"))
    descs = rdf.findall(_q("rdf", "Description"))
    if not descs:
        d = ET.SubElement(rdf, _q("rdf", "Description"))
        d.set(_q("rdf", "about"), "")
        descs = [d]
    # drop the properties we own from every Description
    for d in descs:
        for tag in (_q("dc", "description"), _q("dc", "subject")) + ((_q("mwg-rs", "Regions"),) if regions is not None else ()):
            for el in d.findall(tag):
                d.remove(el)
        for attr in (_q("dc", "description"),):
            d.attrib.pop(attr, None)
    d = descs[0]
    if caption:
        el = ET.SubElement(d, _q("dc", "description"))
        alt = ET.SubElement(el, _q("rdf", "Alt"))
        li = ET.SubElement(alt, _q("rdf", "li"))
        li.set(_q("xml", "lang"), "x-default")
        li.text = caption
    if keywords:
        el = ET.SubElement(d, _q("dc", "subject"))
        bag = ET.SubElement(el, _q("rdf", "Bag"))
        for k in keywords:
            ET.SubElement(bag, _q("rdf", "li")).text = k
    if regions:
        reg = ET.SubElement(d, _q("mwg-rs", "Regions"))
        reg.set(_q("rdf", "parseType"), "Resource")
        if dims:
            ad = ET.SubElement(reg, _q("mwg-rs", "AppliedToDimensions"))
            ad.set(_q("stDim", "w"), str(dims[0]))
            ad.set(_q("stDim", "h"), str(dims[1]))
            ad.set(_q("stDim", "unit"), "pixel")
        rl = ET.SubElement(reg, _q("mwg-rs", "RegionList"))
        bag = ET.SubElement(rl, _q("rdf", "Bag"))
        for name, (x, y, w, h) in regions:
            li = ET.SubElement(bag, _q("rdf", "li"))
            li.set(_q("rdf", "parseType"), "Resource")
            ET.SubElement(li, _q("mwg-rs", "Name")).text = name
            ET.SubElement(li, _q("mwg-rs", "Type")).text = "Face"
            area = ET.SubElement(li, _q("mwg-rs", "Area"))
            area.set(_q("stArea", "x"), f"{x + w / 2:.5f}")
            area.set(_q("stArea", "y"), f"{y + h / 2:.5f}")
            area.set(_q("stArea", "w"), f"{w:.5f}")
            area.set(_q("stArea", "h"), f"{h:.5f}")
            area.set(_q("stArea", "unit"), "normalized")
    body = ET.tostring(root, encoding="unicode")
    packet = ('<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n' + body + "\n"
              + " " * 1024 + '\n<?xpacket end="w"?>')
    return packet.encode("utf-8")


# ------------------------------------------------------------------ public API

def write_jpeg_metadata(path: str, caption: str, keywords: list[str], regions=None, dims=None,
                        keep_mtime: bool = False) -> bool:
    """Update caption/keywords (and optionally face regions) inside a JPEG. Returns True if written."""
    if not path.lower().endswith((".jpg", ".jpeg", ".jpe", ".jfif")):
        return False
    with open(path, "rb") as fh:
        data = fh.read()
    segs = list(_segments(data))
    sos = next((ss for marker, ps, pe, ss in segs if marker == 0xDA), None)
    if sos is None:
        return False
    xmp_old = ps_old = None
    kept = []
    for marker, ps, pe, ss in segs:
        if marker == 0xDA:
            break
        payload = data[ps:pe]
        if marker == 0xE1 and payload.startswith(XMP_HEADER):
            xmp_old = payload[len(XMP_HEADER):]
            continue
        if marker == 0xED and payload.startswith(PS_HEADER):
            ps_old = payload
            continue
        kept.append((marker, data[ss:pe], payload))
    resources = _ps_resources(ps_old) if ps_old else []
    iptc_old = next((d for rid, n, d in resources if rid == 0x0404), None)
    iptc = build_iptc(iptc_old, caption, keywords)
    resources = [r for r in resources if r[0] != 0x0404] + [(0x0404, b"\x00\x00", iptc)]
    ps_new = _ps_payload(resources)
    xmp_new = XMP_HEADER + build_xmp(xmp_old, caption, keywords, regions, dims)
    if len(xmp_new) > 65533 or len(ps_new) > 65533:
        return False

    def seg(marker, payload):
        return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload

    i = 0
    while i < len(kept) and (kept[i][0] == 0xE0 or (kept[i][0] == 0xE1 and kept[i][2].startswith(b"Exif"))):
        i += 1
    out = bytearray(b"\xff\xd8")
    for _, raw, _ in kept[:i]:
        out += raw
    out += seg(0xE1, xmp_new) + seg(0xED, ps_new)
    for _, raw, _ in kept[i:]:
        out += raw
    out += data[sos:]
    st = os.stat(path)
    tmp = f"{path}.fotohaus-{os.getpid()}.tmp"
    try:
        with open(tmp, "wb") as fh:
            fh.write(out)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, st.st_mode & 0o7777)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    if keep_mtime:
        os.utime(path, (st.st_atime, st.st_mtime))
    return True
