"""Clients for uscode.linkedlegislation.org and statutes.linkedlegislation.org.

Every answer is cached in SQLite for `ttl` seconds, keyed by URL; a 404 is cached
too. A request waits `min_interval` seconds after the previous one, and a 429 is
retried once after its `Retry-After`. A token in `USCODE_API_TOKEN` or
`STATUTES_API_TOKEN` is sent as `Authorization: Bearer`.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import urllib.parse
from dataclasses import dataclass

import httpx

USCODE_BASE = os.environ.get("USCODE_API", "https://uscode.linkedlegislation.org/api/v1")
STATUTES_BASE = os.environ.get("STATUTES_API", "https://statutes.linkedlegislation.org/api/v1")
USER_AGENT = "reintroductions/0.1 (+https://github.com/aih/reintroductions-us)"


@dataclass
class Answer:
    status: int
    data: dict | list | None


class CachedClient:
    def __init__(self, base: str, cache_path: str, *, token: str | None = None, ttl: float = 86400,
                 min_interval: float = 0.2, timeout: float = 30.0, transport: httpx.BaseTransport | None = None):
        self.base = base.rstrip("/")
        self.ttl = ttl
        self.min_interval = min_interval
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.http = httpx.Client(headers=headers, timeout=timeout, transport=transport, follow_redirects=True)
        self.db = sqlite3.connect(cache_path, check_same_thread=False, timeout=30)
        if cache_path != ":memory:":
            self.db.execute("pragma journal_mode=wal")  # the site and the pre-generation share it
        self.db.execute("create table if not exists http_cache (url text primary key, status int, body text, fetched real)")
        self._lock = threading.Lock()
        self._last = 0.0

    def get(self, path: str, **params) -> Answer:
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        row = self.db.execute("select status, body, fetched from http_cache where url = ?", (url,)).fetchone()
        if row and time.time() - row[2] < self.ttl:
            return Answer(row[0], json.loads(row[1]) if row[1] else None)
        for attempt in range(2):
            with self._lock:
                wait = self.min_interval - (time.monotonic() - self._last)
                if wait > 0:
                    time.sleep(wait)
                self._last = time.monotonic()
            try:
                r = self.http.get(url)
            except httpx.HTTPError:
                return Answer(0, None)
            if r.status_code == 429 and attempt == 0:
                time.sleep(min(30.0, float(r.headers.get("Retry-After", "5"))))
                continue
            break
        # Not cached: a server error, a spent budget, and a refused token (401)
        # or User-Agent (403) all answer differently once fixed.
        if r.status_code >= 500 or r.status_code in (401, 403, 429):
            return Answer(r.status_code, None)
        try:
            data = r.json()
        except ValueError:
            data = None
        self.db.execute("insert or replace into http_cache values (?, ?, ?, ?)",
                        (url, r.status_code, json.dumps(data) if data is not None else None, time.time()))
        self.db.commit()
        return Answer(r.status_code, data)


def usc_path(title: str, section: str) -> str:
    """`/us/usc/t42/s1395w–4`: OLRC writes the hyphen in a section number as an
    EN DASH, and no section in the Code contains a hyphen."""
    return f"/us/usc/t{title}/s{section.replace('-', '–')}"


def quote_path(p: str) -> str:
    return urllib.parse.quote(p, safe="/")


class USCode:
    def __init__(self, client: CachedClient):
        self.c = client

    def history(self, title: str, section: str) -> Answer:
        return self.c.get("/sections" + quote_path(usc_path(title, section)) + "/versions")

    def section(self, title: str, section: str) -> Answer:
        return self.c.get(quote_path(usc_path(title, section)))

    def classifications(self, title: str, section: str) -> Answer:
        return self.c.get(f"/classifications/code/{title}/{urllib.parse.quote(section)}")

    def status(self) -> Answer:
        return self.c.get("/status")


class Statutes:
    def __init__(self, client: CachedClient):
        self.c = client

    def law(self, congress: int, number: int, section: str | None = None) -> Answer:
        p = f"/us/pl/{congress}/{number}" + (f"/s{section}" if section else "")
        return self.c.get(p)


def default_clients(cache_path: str) -> tuple[USCode, Statutes]:
    return (
        USCode(CachedClient(USCODE_BASE, cache_path, token=os.environ.get("USCODE_API_TOKEN"))),
        Statutes(CachedClient(STATUTES_BASE, cache_path, token=os.environ.get("STATUTES_API_TOKEN"))),
    )
