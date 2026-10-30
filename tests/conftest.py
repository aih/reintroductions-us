import json
import urllib.parse
from pathlib import Path

import httpx
import pytest

FIX = Path(__file__).parent / "fixtures"
CONGRESS = FIX / "congress"


def bill_text(congress, bill_type, number, version):
    return CONGRESS / str(congress) / "bills" / bill_type / f"{bill_type}{number}" / "text-versions" / version / "document.xml"


@pytest.fixture
def recorded_transport():
    """Answers recorded from uscode.linkedlegislation.org; anything else is a 404."""
    data = json.loads((FIX / "linkedleg.json").read_text())
    seen = []

    def handler(request: httpx.Request):
        path = urllib.parse.unquote(request.url.raw_path.decode()).split("/api/v1", 1)[1]
        seen.append(path)
        hit = data.get(path)
        if hit is None:
            return httpx.Response(404, json={"detail": "not recorded"})
        return httpx.Response(hit["status"], json=hit["body"])

    t = httpx.MockTransport(handler)
    t.seen = seen
    return t


@pytest.fixture
def clients(tmp_path, recorded_transport):
    from reintro.linkedleg import CachedClient, Statutes, USCode
    us = USCode(CachedClient("https://uscode.test/api/v1", str(tmp_path / "c.db"), transport=recorded_transport, min_interval=0))
    st = Statutes(CachedClient("https://statutes.test/api/v1", str(tmp_path / "c.db"), transport=recorded_transport, min_interval=0))
    return us, st
