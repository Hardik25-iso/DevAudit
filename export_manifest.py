#!/usr/bin/env python3
"""
export_manifest.py — export the releasable half of the corpus.

The documents themselves cannot be redistributed: the sources do not share a
licence, and at least one restricts redistribution outright (see
docs/LICENSING.md). What we can release is everything we measured, plus enough
information for anyone to rebuild the identical corpus themselves.

That is what this exports:
  - one row per document: source URL, SHA-256, size, issuing body, doc type,
    retrieval timestamp, and every audit measurement
  - a rebuild script that re-fetches from the recorded URLs and verifies each
    file against its checksum

The checksums are what make this a reproduction rather than an approximation.
If a source re-paths or edits a PDF, the rebuild reports a mismatch instead of
silently producing a different corpus.

Usage:
    python export_manifest.py                    # -> release/
    python export_manifest.py --out somewhere/
"""

import argparse
import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import config


COLUMNS = """
    d.sha256, d.source_url, d.issuing_body, d.doc_type, d.filename,
    d.size_bytes, d.downloaded_at,
    a.verdict, a.pages, a.n_fonts, a.fonts_no_unicode,
    a.legacy_fonts, a.unknown_fonts,
    a.chars, a.dev_chars, a.invalid_matras, a.invalid_rate_per_1k,
    a.detached_matras, a.producer, a.creator
"""

REBUILD_SCRIPT = '''#!/usr/bin/env python3
"""
rebuild_corpus.py — reconstruct the corpus from the released manifest.

Reads manifest.csv, re-fetches each document from its recorded source URL,
and verifies the SHA-256. Files that no longer match are reported rather than
silently accepted, so a partial reproduction is visible instead of quiet.

    python rebuild_corpus.py --out ./corpus

Please keep the delay: these are public servers run by municipal bodies.
"""
import argparse, csv, hashlib, time
from pathlib import Path
import requests

UA = "DevAudit-rebuild/0.1 (academic research)"
DELAY = 2.0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="manifest.csv")
    ap.add_argument("--out", default="./corpus")
    args = ap.parse_args()

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    s = requests.Session(); s.headers["User-Agent"] = UA
    ok = mismatch = missing = 0

    with open(args.manifest, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    for i, row in enumerate(rows, 1):
        dest = out / (row["sha256"] + ".pdf")
        if dest.exists():
            ok += 1
            continue
        time.sleep(DELAY)
        try:
            r = s.get(row["source_url"], timeout=60)
        except Exception:
            missing += 1
            print(f"  [{i}/{len(rows)}] unreachable  {row['source_url'][:70]}")
            continue
        if r.status_code != 200:
            missing += 1
            print(f"  [{i}/{len(rows)}] HTTP {r.status_code}  {row['source_url'][:70]}")
            continue
        digest = hashlib.sha256(r.content).hexdigest()
        if digest != row["sha256"]:
            mismatch += 1
            print(f"  [{i}/{len(rows)}] CHANGED since collection  {row['filename'][:50]}")
            continue
        dest.write_bytes(r.content)
        ok += 1

    print(f"\\nverified {ok}, changed {mismatch}, unreachable {missing}, of {len(rows)}")
    if mismatch or missing:
        print("Partial reproduction. Documents move and change on these sites;")
        print("report the counts above alongside any figures you derive.")

if __name__ == "__main__":
    main()
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="release")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(config.MANIFEST_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"SELECT {COLUMNS} FROM documents d "
        "LEFT JOIN audit a ON a.sha256 = d.sha256 "
        "ORDER BY d.issuing_body, d.downloaded_at").fetchall()

    if not rows:
        print("nothing to export - collect and audit first")
        return

    fields = rows[0].keys()
    with open(out / "manifest.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in fields})

    # Summary statistics travel with the data, so the headline numbers can be
    # checked against the rows rather than taken on trust.
    counts = {}
    bodies = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        bodies.setdefault(r["issuing_body"], {})
        b = bodies[r["issuing_body"]]
        b[r["verdict"]] = b.get(r["verdict"], 0) + 1

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_documents": len(rows),
        "n_issuing_bodies": len(bodies),
        "verdict_counts": counts,
        "by_issuing_body": bodies,
        "note": (
            "Documents are not redistributed. Sources do not share a licence "
            "and at least one restricts redistribution; see LICENSING.md. "
            "Use rebuild_corpus.py to reconstruct from source URLs."
        ),
    }
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    (out / "rebuild_corpus.py").write_text(REBUILD_SCRIPT, encoding="utf-8")

    lic = Path("docs/LICENSING.md")
    if lic.exists():
        (out / "LICENSING.md").write_text(
            lic.read_text(encoding="utf-8"), encoding="utf-8")

    print(f"exported {len(rows)} rows from {len(bodies)} issuing bodies -> {out}/")
    for name in sorted(p.name for p in out.iterdir()):
        print(f"    {name}")
    print("\nContains no PDFs by design. See LICENSING.md.")


if __name__ == "__main__":
    main()
