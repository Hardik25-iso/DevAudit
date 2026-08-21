#!/usr/bin/env python3
"""
extract_training.py — full page text for the two arms the deriver trains on.

    python extract_training.py --max-pages 5
    python extract_training.py --max-pages 5 --family fam-01-dvttdhruvnor
    python extract_training.py --dry-run

The deriver was training on `extraction.text_sample`, which holds 600
characters per page because it exists so a human can eyeball a page with the
drive detached. Learning a ~300-rule substitution cipher from that gave
coverage 0.684 and structural validity 0.000 on held-out documents.

This stores the WHOLE page, over pages 1..N rather than page 1 only, for the
documents that belong to a convertible family. Both levers at once: several
times more text per page, and several times more pages.

Needs the external drive. Resumable per (document, page, arm).
"""

import argparse
import random
import re
import sqlite3
from collections import Counter
from datetime import datetime

import config
import extractors as ex
import font_audit as fa

ARMS = ("pymupdf", "ocr")



def is_missing_file(exc):
    """
    Did this exception mean "the file is not there"?

    Cannot be a bare `except FileNotFoundError`. PyMuPDF defines its OWN
    FileNotFoundError that subclasses RuntimeError, not OSError, so the builtin
    never matches it -- while `type(e).__name__` still prints "FileNotFoundError"
    and makes the log look right.

    That is how the drive's fourth drop got past this guard: it wrote 22 stored
    errors instead of aborting. Phase 3 escaped only by luck, because its arm
    set included pypdf, which calls open() and raises the real builtin.
    """
    return (isinstance(exc, OSError)
            or type(exc).__name__ == "FileNotFoundError"
            or "no such file" in str(exc).lower())

def select_documents(conn, family_id, seed):
    sql = """
        SELECT DISTINCT o.sha256, d.stored_path, m.family_id
        FROM family_member m
        JOIN font_observation o ON o.obs_id = m.obs_id
        JOIN documents d ON d.sha256 = o.sha256
    """
    params = ()
    if family_id:
        sql += " WHERE m.family_id = ?"
        params = (family_id,)
    rows = [tuple(r) for r in conn.execute(sql, params)]
    # Shuffled, so an interrupted run is a random subset rather than whatever
    # order SQLite returned. Both Phase 3 drive drops taught this the hard way.
    random.Random(seed).shuffle(rows)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--max-pages", type=int, default=5)
    ap.add_argument("--family")
    ap.add_argument("--seed", type=int, default=20260820)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default=str(config.MANIFEST_DB))
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    docs = select_documents(conn, args.family, args.seed)
    if args.limit:
        docs = docs[:args.limit]

    print(f"documents : {len(docs)}")
    print(f"pages     : 1..{args.max_pages}   arms: {', '.join(ARMS)}")
    by_fam = Counter(d[2] for d in docs)
    for f in sorted(by_fam):
        print(f"    {f:26} {by_fam[f]:>5}")
    if args.dry_run:
        print("\ndry run — nothing extracted, nothing written")
        return
    if not config.TESSERACT_EXE.exists():
        raise SystemExit(f"tesseract not found at {config.TESSERACT_EXE}")

    done = {(r[0], r[1], r[2]) for r in conn.execute(
        "SELECT sha256, page, arm FROM page_text")}
    pages = list(range(1, args.max_pages + 1))
    tally = Counter()
    missing_streak = 0

    for i, (sha, path, _fam) in enumerate(docs, 1):
        for arm in ARMS:
            todo = [p for p in pages if (sha, p, arm) not in done]
            if not todo:
                tally["skipped"] += 1
                continue
            try:
                out, _ = ex.extract(arm, path, todo)
                missing_streak = 0
            except Exception as e:
                if is_missing_file(e):
                    missing_streak += 1
                    tally["missing"] += 1
                    if missing_streak >= 10:
                        conn.commit()
                        raise SystemExit(
                            "\n10 consecutive documents missing — the drive is "
                            "almost certainly detached. Rerun to resume.")
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO page_text "
                    "(sha256, page, arm, error, extracted_at) VALUES (?,?,?,?,?)",
                    (sha, 0, arm, f"{type(e).__name__}: {str(e)[:200]}",
                     datetime.now().isoformat(timespec="seconds")))
                tally[f"error:{arm}"] += 1
                continue

            for p in todo:
                text = out.get(p, "")
                body = re.sub(r"\s+", "", text)
                conn.execute("""INSERT OR REPLACE INTO page_text
                    (sha256, page, arm, text, n_chars, dev_share, error,
                     extracted_at) VALUES (?,?,?,?,?,?,NULL,?)""",
                    (sha, p, arm, text, len(body),
                     len(fa.DEV_RANGE.findall(body)) / len(body) if body else 0.0,
                     datetime.now().isoformat(timespec="seconds")))
                tally[f"ok:{arm}"] += 1

        if i % 20 == 0:
            conn.commit()
            print(f"  [{i}/{len(docs)}] {dict(sorted(tally.items()))}", flush=True)

    conn.commit()
    print(f"\ndone: {len(docs)} documents")
    for k, v in sorted(tally.items()):
        print(f"  {k:20} {v}")
    n, chars = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(n_chars),0) FROM page_text").fetchone()
    print(f"  rows {n}   characters {chars:,}")
    conn.close()


if __name__ == "__main__":
    main()
