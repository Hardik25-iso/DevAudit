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
import re
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


# ---------------------------------------------------------------------------
# personal-data guard
# ---------------------------------------------------------------------------
#
# phase0-schema.md §9.7 recorded that the corpus incidentally collected dates
# of birth and caste categories for identifiable private individuals. When it
# was written that meant 39 excerpts across 11 documents. Phases 2, 3 and 4
# each added a table holding extracted document text and none re-ran the
# measurement; by Phase 5 it was ~11.1M characters with 3,416 DOB matches.
#
# The export has always been clean -- it selects no text column -- but that was
# a property of a column list somebody has to remember. This makes it a check
# the code performs, the same reasoning as Phase 3's loss gate: a rule that is
# enforced cannot be forgotten during a hurried edit.
DOB_RE = re.compile(r"\b\d{1,2}[-/.]\d{1,2}[-/.](?:19|20)\d{2}\b")
CASTE_RE = re.compile(r"\b(?:SC|ST|OBC|NT|VJ|SBC|EWS)\b")
DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")

# The principle the guard enforces: an exported column may identify a document
# or measure it, never quote it.
#
# These columns identify. They carry free text that is ABOUT a document rather
# than FROM it, and the rebuild script cannot work without the URL and hash.
# Checked by inspection over all 1,602 rows rather than assumed:
#   - 100 filenames/URLs match a date pattern; all are publication dates
#     (`..._dtd-10-08-2026.pdf`), none a date of birth
#   - 65 filenames are Marathi document titles (`नामनिर्देशन_अर्ज_व_शासन_पत्र`,
#     `विद्युत_विभाग_जाहीर_प्रकटन`) — titles, not names of people
#   - `producer`/`creator` hold software strings (`HP Scan`, `Adobe PageMaker`)
IDENTIFIER_COLUMNS = {"sha256", "filename", "source_url", "downloaded_at",
                      "producer", "creator"}


def audit_for_personal_data(rows, fields):
    """
    Refuse to export a column that looks like it carries document text.

    Returns a list of complaints. Any complaint aborts the export: this is a
    gate, not a warning, because a warning printed during a long run is a
    warning nobody reads.
    """
    problems = []
    for field in fields:
        if field in IDENTIFIER_COLUMNS:
            continue
        values = [str(r[field]) for r in rows if r[field] is not None]
        if not values:
            continue
        # Devanagari outside an identifier column means quoted document text.
        # No measurement in this schema is written in Devanagari.
        deva = sum(1 for v in values if DEVANAGARI_RE.search(v))
        if deva:
            problems.append(f"{field}: {deva} values contain Devanagari — "
                            f"this quotes document text rather than measuring it")
        dob = sum(1 for v in values if DOB_RE.search(v))
        caste = sum(1 for v in values if CASTE_RE.search(v))
        if dob:
            problems.append(f"{field}: {dob} values match a date-of-birth pattern")
        if caste:
            problems.append(f"{field}: {caste} values match a caste category")
    return problems


def export_mapping_tables(conn, out):
    """
    The Phase 4 reverse-encoding tables.

    Safe to release and worth releasing. Safe because a rule is a character
    mapping — `Eò` -> क — with counts beside it, and quotes no document. Worth
    it because nothing comparable is published for these families: they are
    identified by output signature, not font name, and the commonest names in
    the corpus are `F1`-`F8` and `Calibri`.

    Shipped with the accuracy figures attached, because a table that recovers
    roughly a third of characters is useful to build on and misleading to
    trust. phase4-results.md is the full account.
    """
    try:
        rules = conn.execute("""
            SELECT m.family_id, f.label, f.example_fonts, m.source, m.target,
                   m.origin, m.n_attested, m.n_documents, m.confidence
            FROM mapping_entry m
            LEFT JOIN font_family f ON f.family_id = m.family_id
            ORDER BY m.family_id, m.n_attested DESC
        """).fetchall()
    except sqlite3.OperationalError:
        return          # Phase 4 tables not present; nothing to export
    if not rules:
        return

    fields = rules[0].keys()
    with open(out / "mapping_tables.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rules:
            w.writerow({k: r[k] for k in fields})

    fams = sorted({r["family_id"] for r in rules})
    (out / "MAPPING_TABLES.md").write_text(f"""# Reverse-encoding tables

{len(rules)} rules across {len(fams)} legacy Devanagari encoding families,
derived by aligning extracted text against OCR of the same rendered page.

**These are partial and they are not accurate enough to trust unattended.** On
the best-covered family, held-out median agreement with OCR of the same page is
0.37 — roughly a third of characters. One family produces well-formed
Devanagari that is not the right Devanagari. Read `phase4-results.md` before
using them for anything.

Families are identified by **output signature, not font name**. Font names in
this corpus are useless for the purpose: 546 distinct names cover 5,438
convicted observations and the commonest are `F1`-`F8` and `Calibri`.

| column | meaning |
|---|---|
| `family_id` | encoding family, clustered by character-frequency signature |
| `source` | the byte sequence as extracted |
| `target` | the Devanagari it stands for |
| `origin` | `manual` = hand-authored seed, `derived` = learned by alignment |
| `n_attested` | occurrences supporting the rule |
| `n_documents` | distinct documents supporting it |
| `confidence` | share of alignments choosing this target |

Applying a table needs two reordering passes as well as substitution: `ि` moves
forward past its consonant cluster, and repha `र्` moves backward past the
cluster it sits on. `convert.py` implements both.

Contains no document text.
""", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="release")
    ap.add_argument("--force", action="store_true",
                    help="export despite personal-data complaints (do not use "
                         "for anything published)")
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

    problems = audit_for_personal_data(rows, fields)
    if problems:
        print("REFUSING TO EXPORT — the personal-data guard fired:\n")
        for p in problems:
            print(f"  {p}")
        print("\nSee docs/phase0-schema.md §9.7 and docs/phase5-design.md §3.")
        print("Fix the column list rather than passing --force.")
        if not args.force:
            raise SystemExit(1)
        print("\n--force given; exporting anyway. Do not publish this.")
    else:
        print(f"personal-data guard: {len(fields)} columns checked, clean")

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

    export_mapping_tables(conn, out)

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
