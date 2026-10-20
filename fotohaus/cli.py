"""Command-line interface and MCP (Model Context Protocol) server.

    fotohaus-cli add ~/Pictures         add a folder to the library
    fotohaus-cli scan                   rescan all folders
    fotohaus-cli analyze                find faces / labels / duplicates hashes
    fotohaus-cli search "beach 2019"    search (see Help > Search Tips in the app)
    fotohaus-cli info <id|path>
    fotohaus-cli star <id...>  /  unstar <id...>
    fotohaus-cli caption <id> "text"
    fotohaus-cli albums  /  album-add "Name" <id...>
    fotohaus-cli people
    fotohaus-cli dupes
    fotohaus-cli export --to DIR [--size 2048] <id...>
    fotohaus-cli stats
    fotohaus-cli mcp                    run an MCP server on stdio (for Claude and other clients)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")


def _lib():
    from .library import get_library
    return get_library()


def _photo_dict(lib, r, detail=False) -> dict:
    d = {
        "id": r["id"], "path": r["path"], "filename": r["filename"], "kind": r["kind"],
        "taken": _dt.datetime.fromtimestamp(r["taken"]).isoformat(timespec="seconds") if r["taken"] else None,
        "starred": bool(r["star"]), "caption": r["caption"] or "",
        "tags": [k for k in (r["keywords"] or "").split(",") if k],
        "width": r["width"], "height": r["height"],
    }
    if r["lat"] is not None:
        d["location"] = {"lat": r["lat"], "lon": r["lon"]}
    if detail:
        d["camera"] = r["camera"]
        d["exposure"] = r["exposure"]
        d["people"] = sorted({f["person_name"] for f in lib.faces_for_photo(r["id"])
                              if f["person_name"] and f["confirmed"]})
        d["albums"] = [a["name"] for a in lib.albums_for_photo(r["id"])]
        d["recognized"] = [l["label"] for l in lib.labels_for_photo(r["id"])[:15]]
        if r["ocr"]:
            d["text_in_photo"] = r["ocr"][:1000]
        d["edits"] = r["filters"] or ""
    return d


def _resolve(lib, ref):
    if str(ref).isdigit():
        return lib.photo(int(ref))
    return lib.photo_by_path(ref)


def _ids(lib, refs) -> list[int]:
    out = []
    for ref in refs:
        r = _resolve(lib, ref)
        if r:
            out.append(r["id"])
        else:
            print(f"not found: {ref}", file=sys.stderr)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fotohaus-cli", description="Fotohaus photo library tools",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("add")
    p.add_argument("folder")
    p.add_argument("--readonly", action="store_true")
    sub.add_parser("scan")
    sub.add_parser("analyze")
    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("info")
    p.add_argument("ref")
    for n in ("star", "unstar"):
        p = sub.add_parser(n)
        p.add_argument("refs", nargs="+")
    p = sub.add_parser("caption")
    p.add_argument("ref")
    p.add_argument("text")
    sub.add_parser("albums")
    p = sub.add_parser("album-add")
    p.add_argument("name")
    p.add_argument("refs", nargs="+")
    sub.add_parser("people")
    sub.add_parser("dupes")
    p = sub.add_parser("export")
    p.add_argument("--to", required=True)
    p.add_argument("--size", type=int)
    p.add_argument("--format", default="JPEG", type=str.upper,
                   choices=["JPEG", "PNG", "WEBP", "HEIF", "ORIGINAL"])
    p.add_argument("refs", nargs="+")
    sub.add_parser("stats")
    sub.add_parser("mcp")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 0
    if a.cmd == "mcp":
        return run_mcp()
    lib = _lib()
    if a.cmd == "add":
        lib.add_root(a.folder, readonly=a.readonly)
        print(lib.scan())
    elif a.cmd == "scan":
        print(lib.scan(progress=lambda p, n: print(f"\r{n} new  {p[-60:]:<60}", end="", file=sys.stderr)))
    elif a.cmd == "analyze":
        from .ai.indexer import Indexer
        print(Indexer(lib).run(progress=lambda d, t, m: print(f"\r{m} {d}/{t}", end="", file=sys.stderr)))
    elif a.cmd == "search":
        from .search import search
        rows = search(lib, a.query)[:a.limit]
        if a.json:
            print(json.dumps([_photo_dict(lib, r) for r in rows], indent=2))
        else:
            for r in rows:
                print(f"{r['id']:>7}  {'★' if r['star'] else ' '}  {r['path']}")
            print(f"{len(rows)} result(s)", file=sys.stderr)
    elif a.cmd == "info":
        r = _resolve(lib, a.ref)
        print(json.dumps(_photo_dict(lib, r, True), indent=2) if r else "not found")
    elif a.cmd in ("star", "unstar"):
        lib.set_star(_ids(lib, a.refs), a.cmd == "star")
    elif a.cmd == "caption":
        r = _resolve(lib, a.ref)
        if r:
            lib.set_caption(r["id"], a.text)
    elif a.cmd == "albums":
        for al in lib.albums():
            print(f"{al['id']:>5}  {al['n']:>6}  {al['name']}")
    elif a.cmd == "album-add":
        al = lib.album_by_name(a.name)
        aid = al["id"] if al else lib.create_album(a.name)
        lib.add_to_album(aid, _ids(lib, a.refs))
    elif a.cmd == "people":
        for pe in lib.people():
            print(f"{pe['id']:>5}  {pe['n']:>6}  {pe['name']}")
    elif a.cmd == "dupes":
        from .ai.dupes import find_duplicates
        for g in find_duplicates(lib):
            print("  ".join(r["path"] for r in g))
    elif a.cmd == "export":
        from .exporter import ExportOptions, export_photo
        opts = ExportOptions(dest=a.to, max_size=a.size, fmt=a.format)
        for r in lib.photos_by_ids(_ids(lib, a.refs)):
            print(export_photo(r, opts))
    elif a.cmd == "stats":
        print(json.dumps(lib.counts(), indent=2))
    return 0


# ---------------------------------------------------------------------- MCP server

MCP_TOOLS = [
    {"name": "search_photos",
     "description": "Search the user's photo library. Supports free words (matched against captions, tags, "
                    "folders, albums, people, recognized objects/scenes and text in photos) and filters like "
                    "person:NAME album:NAME year:2019 before:2020-05-01 after:2018 is:starred is:video "
                    "has:location camera:iphone.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"},
                                                      "limit": {"type": "integer", "default": 30}},
                     "required": ["query"]}},
    {"name": "get_photo", "description": "Full details for one photo (people, albums, recognized objects, text).",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
    {"name": "library_stats", "description": "Counts of photos, videos, albums, people, etc.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_albums", "description": "List albums with photo counts.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_people", "description": "List named people with photo counts.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "set_star", "description": "Star or unstar photos.",
     "inputSchema": {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "integer"}},
                                                      "starred": {"type": "boolean"}},
                     "required": ["ids", "starred"]}},
    {"name": "set_caption", "description": "Set a photo's caption.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "integer"}, "caption": {"type": "string"}},
                     "required": ["id", "caption"]}},
    {"name": "add_tags", "description": "Add a tag (keyword) to photos.",
     "inputSchema": {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "integer"}},
                                                      "tag": {"type": "string"}}, "required": ["ids", "tag"]}},
    {"name": "add_to_album", "description": "Add photos to an album (created if it doesn't exist).",
     "inputSchema": {"type": "object", "properties": {"album": {"type": "string"},
                                                      "ids": {"type": "array", "items": {"type": "integer"}}},
                     "required": ["album", "ids"]}},
    {"name": "find_duplicates", "description": "Groups of duplicate / near-duplicate photos (paths).",
     "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 50}}}},
]


def _mcp_call(lib, name, args):
    from .search import search
    if name == "search_photos":
        rows = search(lib, args.get("query", ""))[: int(args.get("limit") or 30)]
        return [_photo_dict(lib, r) for r in rows]
    if name == "get_photo":
        r = lib.photo(int(args["id"]))
        return _photo_dict(lib, r, True) if r else {"error": "not found"}
    if name == "library_stats":
        return lib.counts()
    if name == "list_albums":
        return [{"id": a["id"], "name": a["name"], "count": a["n"]} for a in lib.albums()]
    if name == "list_people":
        return [{"id": p["id"], "name": p["name"], "photos": p["n"]} for p in lib.people()]
    if name == "set_star":
        lib.set_star([int(i) for i in args["ids"]], bool(args["starred"]))
        return {"ok": True}
    if name == "set_caption":
        lib.set_caption(int(args["id"]), str(args["caption"]))
        return {"ok": True}
    if name == "add_tags":
        lib.add_keyword([int(i) for i in args["ids"]], str(args["tag"]))
        return {"ok": True}
    if name == "add_to_album":
        al = lib.album_by_name(args["album"])
        aid = al["id"] if al else lib.create_album(args["album"])
        lib.add_to_album(aid, [int(i) for i in args["ids"]])
        return {"ok": True, "album_id": aid}
    if name == "find_duplicates":
        from .ai.dupes import find_duplicates
        return [[r["path"] for r in g] for g in find_duplicates(lib)[: int(args.get("limit") or 50)]]
    raise ValueError(f"unknown tool {name}")


def run_mcp() -> int:
    from . import __version__
    # stdout carries the JSON-RPC stream: anything else printed (warnings from libraries, progress)
    # must go to stderr or the client sees a corrupt message
    out = sys.stdout
    sys.stdout = sys.stderr
    lib = _lib()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        if mid is None:  # notification
            continue
        try:
            if method == "initialize":
                result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": "fotohaus", "version": __version__}}
            elif method == "tools/list":
                result = {"tools": MCP_TOOLS}
            elif method == "tools/call":
                data = _mcp_call(lib, params.get("name"), params.get("arguments") or {})
                result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=1)}]}
            elif method == "ping":
                result = {}
            else:
                out.write(json.dumps({"jsonrpc": "2.0", "id": mid,
                                      "error": {"code": -32601, "message": f"Method not found: {method}"}}) + "\n")
                out.flush()
                continue
            out.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}) + "\n")
        except Exception as e:
            out.write(json.dumps({"jsonrpc": "2.0", "id": mid,
                                  "result": {"content": [{"type": "text", "text": f"Error: {e}"}],
                                             "isError": True}}) + "\n")
        out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
