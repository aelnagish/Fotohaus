"""Perceptual hashing and duplicate / near-duplicate grouping."""
from __future__ import annotations

import numpy as np
from PIL import Image

_DCT = None


def _dct_matrix(n: int = 32) -> np.ndarray:
    global _DCT
    if _DCT is None:
        k = np.arange(n)
        m = np.cos(np.pi * (2 * k[None, :] + 1) * k[:, None] / (2 * n))
        m[0] *= 1 / np.sqrt(2)
        _DCT = (m * np.sqrt(2 / n)).astype(np.float64)
    return _DCT


def phash(im: Image.Image) -> str:
    """64-bit DCT perceptual hash as 16 hex chars."""
    g = im.convert("L").resize((32, 32), Image.LANCZOS)
    a = np.asarray(g, dtype=np.float64)
    d = _dct_matrix()
    coeffs = d @ a @ d.T
    block = coeffs[:8, :8].flatten()
    med = np.median(block[1:])
    bits = block > med
    v = 0
    for b in bits:
        v = (v << 1) | int(b)
    return f"{v:016x}"


def _popcount(x: np.ndarray) -> np.ndarray:
    if hasattr(np, "bitwise_count"):
        return np.bitwise_count(x).astype(np.int64)
    b = x.view(np.uint8).reshape(len(x), 8)
    return np.unpackbits(b, axis=1).sum(1).astype(np.int64)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def group_duplicates(items: list[tuple[int, str]], max_distance: int = 4) -> list[list[int]]:
    """items: [(photo_id, phash_hex)] -> groups of ids with hash distance <= max_distance.

    Uses multi-index hashing: the 64 bits are split into max_distance+1 chunks; any pair within
    distance d <= max_distance shares at least one identical chunk (pigeonhole), so only pairs
    sharing a bucket are compared.
    """
    if not items:
        return []
    ids = np.array([i for i, _ in items], dtype=np.int64)
    hv = np.array([int(h, 16) for _, h in items], dtype=np.uint64)
    n_chunks = max(1, max_distance + 1)
    widths = [64 // n_chunks + (1 if i < 64 % n_chunks else 0) for i in range(n_chunks)]
    parent = list(range(len(items)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    shift = 0
    for w in widths:
        mask = np.uint64((1 << w) - 1)
        keys = (hv >> np.uint64(shift)) & mask
        shift += w
        order = np.argsort(keys, kind="stable")
        sk = keys[order]
        bounds = np.flatnonzero(np.diff(sk)) + 1
        for grp in np.split(order, bounds):
            if len(grp) < 2:
                continue
            if len(grp) > 3000:  # degenerate (e.g. blank frames) - compare to first only
                grp = grp[:3000]
            sub = hv[grp]
            for i in range(len(grp) - 1):
                x = sub[i] ^ sub[i + 1:]
                d = _popcount(x)
                close = np.flatnonzero(d <= max_distance)
                for c in close:
                    a, b = find(int(grp[i])), find(int(grp[i + 1 + c]))
                    if a != b:
                        parent[a] = b
    groups: dict[int, list[int]] = {}
    for idx in range(len(items)):
        groups.setdefault(find(idx), []).append(int(ids[idx]))
    return [g for g in groups.values() if len(g) > 1]


def find_duplicates(lib, max_distance: int = 4) -> list[list]:
    rows = lib.db.q("SELECT id, phash FROM photos WHERE phash IS NOT NULL AND phash != '' AND hidden=0")
    groups = group_duplicates([(r["id"], r["phash"]) for r in rows], max_distance)
    out = []
    for g in groups:
        photos = lib.photos_by_ids(g)
        photos.sort(key=lambda r: (-(r["width"] or 0) * (r["height"] or 0), -(r["size"] or 0), r["path"]))
        out.append(photos)
    out.sort(key=lambda grp: -len(grp))
    return out
