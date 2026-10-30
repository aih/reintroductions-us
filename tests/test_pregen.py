import io
import json

import pytest

from conftest import CONGRESS
from reintro import db, generate, pregen


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}

    def download_file(self, bucket, key, path):
        with open(path, "wb") as f:
            f.write(self.objects[key])

    def get_paginator(self, name):
        s3 = self

        class P:
            def paginate(self, Bucket, Prefix):
                keys = sorted(k for k in s3.objects if k.startswith(Prefix))
                for i in range(0, max(len(keys), 1), 2):  # pages of two
                    yield {"Contents": [{"Key": k} for k in keys[i:i + 2]]} if keys else {}
        return P()


class FakeSQS:
    def __init__(self):
        self.sent = []

    def send_message_batch(self, QueueUrl, Entries):
        self.sent += [json.loads(e["MessageBody"]) for e in Entries]
        return {"Successful": [{"Id": e["Id"]} for e in Entries]}


@pytest.fixture()
def con(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.load_congress(c, CONGRESS, 119)
    return c


def test_plan_handler_sync(con, tmp_path, monkeypatch):
    s3, sqs, pdf_dir = FakeS3(), FakeSQS(), tmp_path / "pdf"
    build = lambda b: generate.build(con, b, cache_dir=tmp_path / "cache")  # noqa: E731
    r = pregen.plan(con, build, bucket="b", queue_url="q", s3=s3, sqs=sqs, pdf_dir=pdf_dir, congress=119)
    assert r["queued"] == len(sqs.sent) >= 1 and r["failed"] == 0
    m = next(m for m in sqs.sent if m["pdf_key"].startswith("pdf/hr206-119-"))
    d = build("hr206-119")
    assert m["pdf_key"] == "pdf/" + pregen.pdf_name("hr206-119", d.xml)
    assert m["xml_key"] in s3.objects and m["source_line"] == "Draft from H.R. 206, 119th Congress"

    # A second plan sends nothing: every bill's XML is in flight.
    again = pregen.plan(con, build, bucket="b", queue_url="q", s3=s3, sqs=FakeSQS(), pdf_dir=pdf_dir, congress=119)
    assert again["queued"] == 0 and again["in_flight"] == r["queued"]

    monkeypatch.setenv("REINTRO_PREGEN_BUCKET", "b")
    out = pregen.handler({"Records": [{"messageId": "1", "body": json.dumps(m)},
                                      {"messageId": "2", "body": json.dumps({**m, "xml_key": "in/missing.xml"})}]},
                         None, s3=s3)
    assert out == {"batchItemFailures": [{"itemIdentifier": "2"}]}
    assert s3.objects[m["pdf_key"]].startswith(b"%PDF")

    assert pregen.sync(bucket="b", s3=s3, pdf_dir=pdf_dir) == {"remote": 1, "downloaded": 1}
    assert pregen.sync(bucket="b", s3=s3, pdf_dir=pdf_dir)["downloaded"] == 0
    done = pregen.plan(con, build, bucket="b", queue_url="q", s3=s3, sqs=FakeSQS(), pdf_dir=pdf_dir, congress=119,
                       resend=True)
    assert done["have_pdf"] == 1 and done["queued"] == r["queued"] - 1
