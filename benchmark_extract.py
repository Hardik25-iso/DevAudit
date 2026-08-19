#!/usr/bin/env python3
"""
benchmark_extract.py — run every extractor arm over a sample and store the
measurements. The one drive-attached pass of Phase 3.

    python benchmark_extract.py --tier labelled  --max-pages 5
    python benchmark_extract.py --tier all --max-pages 1 --arms all
    python benchmark_extract.py --tier coverage --per-body 60 --dry-run

Tiers pick the document population; --arms and --max-pages pick the work done
on it. Keeping those separate is what lets the corpus-wide concordance run
(every non-SCAN document, page 1, all five arms) reuse this unchanged.

Resumable: rows already present for a run_id are skipped, so a drive that drops
mid-pass costs the current document and nothing else. The drive has dropped
twice in this project's history.

Verbose. Pipe it:

    python benchmark_extract.py --tier all > data/bench.log 2>&1 && tail -20 data/bench.log
"""

import argparse
import datetime
import random
import sqlite3
import sys
from collections import Counter, defaultdict

import config
import extractors as ex
from phase3_schema import SIGNALS_VERSION

TEXT_ARMS = ("pdftotext", "pymupdf", "pdfplumber", "pypdf")
ALL_ARMS = TEXT_ARMS + ("ocr",)


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# sampling
# ---------------------------------------------------------------------------
#
# Sampling has produced a wrong answer three times in this project (PMC's 0%
# from the oldest 5%; the regional comparison that reversed on an under-sampled
# body; per-source caps that limit a run rather than a total). Every draw here
# is seeded, capped per body, and written to extraction_sample so the frame can
# be audited after the fact instead of reconstructed from memory.

def select_documents(conn, tier, per_body, seed):
    """Returns [(sha256, stored_path, issuing_body, stratum)]."""
    if tier == "labelled":
        # The 320 documents carrying the 434 gt-v1 observations. Scored subset:
        # every page here also has font labels behind it.
        sql = """
          SELECT DISTINCT d.sha256, d.stored_path, d.issuing_body, 'labelled'
          FROM annotation_sample s
          JOIN font_observation o ON o.obs_id = s.obs_id
          JOIN documents d ON d.sha256 = o.sha256
          JOIN audit a ON a.sha256 = d.sha256
          WHERE s.sample_id = 'gt-v1' AND a.verdict != 'SCAN'
        """
    elif tier in ("coverage", "all"):
        # SCAN excluded throughout: no text layer means nothing for a text
        # extractor to recover, and OCR of a scan is a separate, well-studied
        # question that OVERVIEW.md already places out of scope.
        sql = """
          SELECT d.sha256, d.stored_path, d.issuing_body, a.verdict
          FROM documents d
          JOIN audit a ON a.sha256 = d.sha256
          WHERE a.verdict != 'SCAN'
        """
    else:
        raise SystemExit(f"unknown tier: {tier}")

    rows = [tuple(r) for r in conn.execute(sql)]

    if tier == "all":
        return rows      # census, not a sample; no draw to record

    # Cap per body so one prolific corporation cannot dominate, and so the
    # macro average by body stays computable.
    rng = random.Random(seed)
    by_body = defaultdict(list)
    for r in rows:
        by_body[r[2]].append(r)
    out = []
    for body in sorted(by_body):
        pool = sorted(by_body[body])
        rng.shuffle(pool)
        out.extend(pool[:per_body] if per_body else pool)
    return out


def record_sample(conn, run_id, tier, docs):
    """Store the frame, with the selection probability the reweighting needs."""
    sizes = Counter(d[2] for d in docs)
    if tier != "all":
        pool = Counter(r[0] for r in conn.execute("""
            SELECT d.issuing_body FROM documents d JOIN audit a ON a.sha256=d.sha256
            WHERE a.verdict != 'SCAN'"""))
    else:
        pool = sizes
    conn.executemany(
        """INSERT OR REPLACE INTO extraction_sample
           (run_id, sha256, issuing_body, stratum, stratum_size, drawn, selection_prob)
           VALUES (?,?,?,?,?,?,?)""",
        [(run_id, sha, body, stratum, pool.get(body, 0), sizes[body],
          (sizes[body] / pool[body]) if pool.get(body) else None)
         for sha, _path, body, stratum in docs])


# ---------------------------------------------------------------------------
# the pass
# ---------------------------------------------------------------------------

def done_keys(conn, run_id):
    return {(r[0], r[1], r[2]) for r in conn.execute(
        "SELECT sha256, page, arm FROM extraction WHERE run_id = ?", (run_id,))}


def store(conn, run_id, sha, page, arm, row, elapsed_ms, error):
    cols = ["run_id", "sha256", "page", "arm", "error", "elapsed_ms",
            "signals_version", "extracted_at"]
    vals = [run_id, sha, page, arm, error, elapsed_ms, SIGNALS_VERSION, now()]
    if row:
        for k, v in row.items():
            cols.append(k)
            vals.append(v)
    conn.execute(f"INSERT OR REPLACE INTO extraction ({','.join(cols)}) "
                 f"VALUES ({','.join('?' * len(cols))})", vals)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--tier", required=True,
                    choices=["labelled", "coverage", "all"])
    ap.add_argument("--arms", default="text",
                    help="'text' (4 text arms), 'all' (+ocr), or a comma list")
    ap.add_argument("--max-pages", type=int, default=5)
    ap.add_argument("--per-body", type=int, default=0,
                    help="cap per issuing body; 0 = no cap")
    ap.add_argument("--seed", type=int, default=20260819)
    ap.add_argument("--run-id")
    ap.add_argument("--limit", type=int, help="stop after N documents (smoke test)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default=str(config.MANIFEST_DB))
    args = ap.parse_args()

    arms = (list(TEXT_ARMS) if args.arms == "text"
            else list(ALL_ARMS) if args.arms == "all"
            else args.arms.split(","))
    for a in arms:
        if a not in ex.ARMS:
            raise SystemExit(f"unknown arm: {a}")

    run_id = args.run_id or f"{args.tier}-{datetime.date.today():%Y%m%d}"
    conn = sqlite3.connect(args.db)

    docs = select_documents(conn, args.tier, args.per_body, args.seed)
    if args.limit:
        docs = docs[:args.limit]

    print(f"run_id     : {run_id}")
    print(f"tier       : {args.tier}   documents: {len(docs)}")
    print(f"arms       : {', '.join(arms)}")
    print(f"pages      : 1..{args.max_pages}")
    by_body = Counter(d[2] for d in docs)
    for b in sorted(by_body):
        print(f"    {b[:44]:46} {by_body[b]:>5}")
    if args.dry_run:
        print("\ndry run — nothing extracted, nothing written")
        return

    if "ocr" in arms and not config.TESSERACT_EXE.exists():
        raise SystemExit(f"tesseract not found at {config.TESSERACT_EXE}; "
                         f"the ocr arm cannot run")

    conn.execute(
        """INSERT OR REPLACE INTO extraction_run
           (run_id, tier, arms, seed, per_body_cap, max_pages, n_documents,
            signals_version, started_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (run_id, args.tier, ",".join(arms), args.seed, args.per_body,
         args.max_pages, len(docs), SIGNALS_VERSION, now()))
    record_sample(conn, run_id, args.tier, docs)
    conn.commit()

    already = done_keys(conn, run_id)
    pages = list(range(1, args.max_pages + 1))
    tally = Counter()
    missing_streak = 0

    for i, (sha, path, body, _stratum) in enumerate(docs, 1):
        for arm in arms:
            todo = [p for p in pages if (sha, p, arm) not in already]
            if not todo:
                tally["skipped"] += 1
                continue
            try:
                out, elapsed = ex.extract(arm, path, todo)
                missing_streak = 0
            except FileNotFoundError as e:
                # The external drive drops. Ten in a row is not ten bad
                # documents, it is an unplugged disk, and continuing would
                # write a thousand spurious errors over a good run.
                missing_streak += 1
                tally["missing"] += 1
                if missing_streak >= 10:
                    conn.commit()
                    raise SystemExit(
                        "\n10 consecutive documents missing — the external "
                        "drive is almost certainly detached. Nothing written "
                        "since the last commit is lost; rerun to resume.")
                continue
            except Exception as e:
                store(conn, run_id, sha, 0, arm, None, None,
                      f"{type(e).__name__}: {str(e)[:300]}")
                tally[f"error:{arm}"] += 1
                continue

            per_page = max(1, elapsed // max(len(todo), 1))
            for p in todo:
                row = ex.measure(out.get(p, ""))
                store(conn, run_id, sha, p, arm, row, per_page, None)
                tally[f"ok:{arm}"] += 1

        if i % 25 == 0:
            conn.commit()
            print(f"  [{i}/{len(docs)}] {dict(sorted(tally.items()))}", flush=True)

    conn.execute("UPDATE extraction_run SET finished_at=? WHERE run_id=?",
                 (now(), run_id))
    conn.commit()

    print(f"\ndone: {len(docs)} documents")
    for k, v in sorted(tally.items()):
        print(f"  {k:24} {v}")
    n = conn.execute("SELECT COUNT(*) FROM extraction WHERE run_id=?",
                     (run_id,)).fetchone()[0]
    print(f"  rows in extraction       {n}")
    conn.close()


if __name__ == "__main__":
    main()
