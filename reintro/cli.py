"""reintro — fetch bills, load them, build drafts, serve the site.

    reintro fetch --congress 119 [--types hr,s]     usc-run govinfo + bills (unitedstates/congress)
    reintro load --congress 119                     data.json + texts → the database
    reintro update --congress 119                   fetch, then load
    reintro link --older 118 --newer 119            mark 118th bills reintroduced in the 119th
    reintro draft hr1514-118 [--to 120] [--out F.xml] [--pdf F.pdf] [--no-check]
    reintro serve [--port 8020]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import db

# usc-run's working directory; it writes `data/` and `cache/` inside it.
USC_ROOT = Path(os.environ.get("REINTRO_USC_ROOT", "data/usc"))
DB_PATH = os.environ.get("REINTRO_DB", "data/reintro.db")
CACHE = Path(os.environ.get("REINTRO_CACHE", "data/cache"))


def usc_run(args: list[str], data_root: Path) -> int:
    """Run unitedstates/congress's `usc-run` with its data and cache directories
    under `data_root` (it writes `data/` and `cache/` in its working directory)."""
    exe = shutil.which("usc-run")
    if not exe:
        print("usc-run is not installed: uv sync --extra ingest", file=sys.stderr)
        return 2
    data_root.mkdir(parents=True, exist_ok=True)
    print("+ usc-run " + " ".join(args), file=sys.stderr)
    return subprocess.call([exe, *args], cwd=str(data_root))


def cmd_fetch(a) -> int:
    root = USC_ROOT
    types = f"--type={a.types}" if a.types else None
    steps = [
        ["govinfo", "--bulkdata=BILLSTATUS", f"--congress={a.congress}", *( [types] if types else [])],
        ["bills", f"--congress={a.congress}"],
        ["govinfo", "--bulkdata=BILLS", f"--congress={a.congress}", *( [types] if types else [])],
    ]
    for s in steps:
        rc = usc_run([*s, "--log=warn"], root)
        if rc not in (0,):
            print(f"usc-run {' '.join(s)} exited {rc}", file=sys.stderr)
    return 0


def _data_dir() -> Path:
    return USC_ROOT / "data"


def cmd_load(a) -> int:
    con = db.connect(DB_PATH)
    types = tuple(a.types.split(",")) if a.types else db.BILL_TYPES
    r = db.load_congress(con, _data_dir(), a.congress, types=types,
                         progress=lambda i, n: print(f"  {i}/{n}", file=sys.stderr))
    print(json.dumps(r))
    return 0


def cmd_update(a) -> int:
    cmd_fetch(a)
    return cmd_load(a)


def cmd_link(a) -> int:
    con = db.connect(DB_PATH)
    n = db.mark_reintroductions(con, _data_dir(), a.older, a.newer)
    print(json.dumps({"older": a.older, "newer": a.newer, "matched": n}))
    return 0


def cmd_draft(a) -> int:
    from . import generate
    from .linkedleg import default_clients
    con = db.connect(DB_PATH)
    us = st = None
    if not a.no_check:
        CACHE.mkdir(parents=True, exist_ok=True)
        us, st = default_clients(str(CACHE / "http.db"))
    d = generate.build(con, a.bill_id, to_congress=a.to, uscode=us, statutes=st, cache_dir=CACHE,
                       force=a.force, chamber=a.chamber, allow_enacted=a.allow_enacted)
    if a.out:
        Path(a.out).write_bytes(d.xml)
    else:
        sys.stdout.buffer.write(d.xml)
    if a.pdf:
        from .pdf import draft_pdf
        Path(a.pdf).write_bytes(draft_pdf(d.tree, source_line=f"Draft from {d.source['citation']}"))
    for w in d.warnings:
        print(f"{w['kind']:22} {w.get('where') or '':40} {w['message']}", file=sys.stderr)
    return 0


def cmd_serve(a) -> int:
    import uvicorn
    uvicorn.run("reintro.web.app:app", host=a.host, port=a.port, reload=a.reload)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="reintro")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("fetch", cmd_fetch), ("load", cmd_load), ("update", cmd_update)):
        p = sub.add_parser(name)
        p.add_argument("--congress", type=int, required=True)
        p.add_argument("--types", default=None, help="comma-separated bill types, e.g. hr,s")
        p.set_defaults(fn=fn)
    p = sub.add_parser("link")
    p.add_argument("--older", type=int, required=True)
    p.add_argument("--newer", type=int, required=True)
    p.set_defaults(fn=cmd_link)
    p = sub.add_parser("draft")
    p.add_argument("bill_id")
    p.add_argument("--to", type=int, default=None)
    p.add_argument("--out")
    p.add_argument("--pdf")
    p.add_argument("--chamber", choices=["house", "senate"])
    p.add_argument("--no-check", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--allow-enacted", action="store_true")
    p.set_defaults(fn=cmd_draft)
    p = sub.add_parser("serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8020)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(fn=cmd_serve)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
