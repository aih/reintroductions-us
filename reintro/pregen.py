"""PDFs rendered ahead of time by a Lambda function (docs/deploy.md, "Pre-generated PDFs").

On the box, `reintro pregen` builds each eligible bill's draft (the same draft, with the same
cache, as the site), uploads its XML to `s3://{bucket}/in/` and sends one SQS message per bill.
The function (`handler`) renders the PDF and writes it to `s3://{bucket}/pdf/` under the name
the site looks for in `{cache}/pdf/`; `reintro pregen-sync` copies the new ones down.

boto3 is imported where it is used: the site does not need it.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from lxml import etree


def pdf_name(bill_id: str, xml: bytes, warnings_on: bool = True) -> str:
    """The PDF's file name: the bill, the draft XML's hash, and whether warnings are shown."""
    return f"{bill_id}-{hashlib.sha256(xml).hexdigest()[:16]}-{int(warnings_on)}.pdf"


def source_line(d) -> str:
    return f"Draft from {d.source['citation']}"


def _keys(s3, bucket: str, prefix: str) -> set[str]:
    out = set()
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        out.update(o["Key"][len(prefix):] for o in page.get("Contents", []))
    return out


def plan(con, build, *, bucket: str, queue_url: str, s3, sqs, pdf_dir: Path, congress: int,
         limit: int | None = None, resend: bool = False, log=print) -> dict:
    """Queue every eligible bill of `congress` whose PDF is neither on disk nor in S3.

    `build(bill_id)` returns the draft (a `generate.DraftResult`). A bill whose XML is already
    in `in/` without a PDF is in flight and is skipped unless `resend`.
    """
    have = {p.name for p in pdf_dir.glob("*.pdf")} | _keys(s3, bucket, "pdf/")
    queued = set() if resend else {k.removesuffix(".xml") for k in _keys(s3, bucket, "in/")}
    rows = con.execute("select bill_id from bills where congress = ? and not enacted and text_path is not null"
                       " order by bill_id", (congress,)).fetchall()
    counts = {"bills": len(rows), "queued": 0, "have_pdf": 0, "in_flight": 0, "failed": 0}
    batch = []

    def flush():
        if batch:
            r = sqs.send_message_batch(QueueUrl=queue_url, Entries=list(batch))
            counts["failed"] += len(r.get("Failed", []))
            counts["queued"] += len(r.get("Successful", []))
            batch.clear()

    for (bill_id,) in rows:
        if limit is not None and counts["queued"] + len(batch) >= limit:
            break
        try:
            d = build(bill_id)
        except Exception as e:  # one bill's failure does not stop the run
            log(f"{bill_id}: {type(e).__name__}: {e}", file=sys.stderr)
            counts["failed"] += 1
            continue
        name = pdf_name(bill_id, d.xml)
        stem = name.removesuffix(".pdf")
        if name in have:
            counts["have_pdf"] += 1
            continue
        if stem in queued:
            counts["in_flight"] += 1
            continue
        s3.put_object(Bucket=bucket, Key=f"in/{stem}.xml", Body=d.xml, ContentType="application/xml")
        body = {"xml_key": f"in/{stem}.xml", "pdf_key": f"pdf/{name}", "source_line": source_line(d)}
        batch.append({"Id": str(len(batch)), "MessageBody": json.dumps(body)})
        if len(batch) == 10:
            flush()
    flush()
    return counts


def sync(*, bucket: str, s3, pdf_dir: Path) -> dict:
    """Copy into `pdf_dir` every PDF in `s3://{bucket}/pdf/` that is not there yet."""
    pdf_dir.mkdir(parents=True, exist_ok=True)
    local = {p.name for p in pdf_dir.glob("*.pdf")}
    remote = _keys(s3, bucket, "pdf/")
    new = sorted(remote - local)
    for name in new:
        tmp = pdf_dir / (name + ".tmp")
        s3.download_file(bucket, f"pdf/{name}", str(tmp))
        tmp.replace(pdf_dir / name)
    return {"remote": len(remote), "downloaded": len(new)}


def render(xml: bytes, line: str, warnings_on: bool = True) -> bytes:
    from .pdf import draft_pdf
    return draft_pdf(etree.ElementTree(etree.fromstring(xml)), source_line=line, warnings_on=warnings_on)


def handler(event, context, s3=None):
    """The Lambda entry point: an SQS batch of {xml_key, pdf_key, source_line} messages."""
    if s3 is None:
        import boto3
        s3 = boto3.client("s3")
    bucket = os.environ["REINTRO_PREGEN_BUCKET"]
    failures = []
    for rec in event.get("Records", []):
        try:
            m = json.loads(rec["body"])
            xml = s3.get_object(Bucket=bucket, Key=m["xml_key"])["Body"].read()
            pdf = render(xml, m["source_line"])
            s3.put_object(Bucket=bucket, Key=m["pdf_key"], Body=pdf, ContentType="application/pdf")
            print(json.dumps({"pdf_key": m["pdf_key"], "xml_bytes": len(xml), "pdf_bytes": len(pdf)}))
        except Exception as e:
            print(json.dumps({"message_id": rec.get("messageId"), "error": f"{type(e).__name__}: {e}"}))
            failures.append({"itemIdentifier": rec["messageId"]})
    return {"batchItemFailures": failures}
