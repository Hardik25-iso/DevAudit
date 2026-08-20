#!/usr/bin/env python3
"""
evaluate_conversion.py — does the converter actually work? (design §4)

    python evaluate_conversion.py --family fam-01-dvttdhruvnor --run eval-1
    python evaluate_conversion.py --family fam-01-dvttdhruvnor --run eval-1 --report
    python evaluate_conversion.py --negative-control

Three measures, in the order their independence decreases. All three are
reported together, always: a high score on one and a low score on another is
itself the finding, and showing only the flattering one would hide it.

**Primary — structural validity.** Converted text must be well-formed
Devanagari (`invalid_rate_per_1k` under Phase 1's threshold of 2.0). Independent
of OCR, so it is not circular. Legacy-order output is structurally impossible
Devanagari, so a table producing plausible nonsense fails here.

**Secondary — OCR agreement, on documents held out of derivation.** The tables
were LEARNED from OCR, so this can never be the primary measure without scoring
the answer against its own source. Reported as corroboration only, and the same
OCR engine produced both sides, so the circularity is reduced rather than gone.

**Negative control — the converter must not damage clean text.** Run over
documents Phase 1 called CLEAN. A converter that fires on correct Devanagari
would corrupt working documents, which is worse than not converting at all.

Reads the manifest only — no external drive.
"""

import argparse
import difflib
import hashlib
import re
import sqlite3
from datetime import datetime

import config
import convert as cv
import derive_mapping as dm
import font_audit as fa
from phase4_schema import MAPPING_VERSION

TRAIN_SHARE = 70          # percent of documents used to derive
INVALID_MAX = 2.0         # Phase 1's threshold, unchanged


def split_of(sha256):
    """
    Deterministic train/test assignment from the document hash.

    Hash-based rather than random so the split is fixed by the document's
    identity, not by when the script happened to run. A split decided after
    derivation is not a split, and one that moves between runs cannot be
    checked.
    """
    h = int(hashlib.sha256(sha256.encode()).hexdigest()[:8], 16)
    return "train" if h % 100 < TRAIN_SHARE else "test"


def measure_text(text):
    """Structural signals for one page, from the Phase 1 battery unchanged."""
    m = fa.measure_font_text(text)
    body = re.sub(r"\s+", "", text)
    return {
        "invalid_rate": m["invalid_rate_per_1k"],
        "dev_share": m["dev_chars"] / len(body) if body else 0.0,
        "dev_chars": m["dev_chars"],
        "n_chars": len(body),
    }


def similarity(a, b):
    """Character agreement, whitespace ignored — spacing is a separate defect."""
    a, b = re.sub(r"\s+", "", a), re.sub(r"\s+", "", b)
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def evaluate(conn, family_id, run_id, iterations):
    all_pages = dm.page_pairs(conn, family_id)
    train = {sha for _, _, sha in all_pages if split_of(sha) == "train"}
    test = {sha for _, _, sha in all_pages if split_of(sha) == "test"}
    print(f"family : {family_id}")
    print(f"pages  : {len(all_pages)}   train docs {len(train)}   test docs {len(test)}")
    if not test:
        print("no held-out documents — cannot evaluate honestly.")
        return

    print("\nderiving on TRAIN only...")
    table, _ = dm.derive(conn, family_id, iterations, verbose=False,
                         split_docs=train)
    tbl = {s: (v["target"], v["conf"]) for s, v in table.items()}
    print(f"  {len(tbl)} rules from {len(train)} documents")

    rows = conn.execute("""
        SELECT DISTINCT e.sha256, e.page, e.text_sample AS garbage,
               o.text_sample AS ocr
        FROM extraction e
        JOIN extraction o
          ON o.run_id = e.run_id AND o.sha256 = e.sha256
         AND o.page = e.page AND o.arm = 'ocr'
        JOIN font_observation f ON f.sha256 = e.sha256
        JOIN family_member m ON m.obs_id = f.obs_id
        WHERE e.arm = 'pymupdf' AND m.family_id = ?
          AND e.n_chars > 200 AND o.n_chars > 200 AND e.dev_share < 0.05
    """, (family_id,)).fetchall()

    now = datetime.now().isoformat(timespec="seconds")
    conn.execute("DELETE FROM conversion WHERE run_id=?", (run_id,))
    stored = 0
    for r in rows:
        split = split_of(r["sha256"])
        before = measure_text(r["garbage"] or "")
        after_text, coverage = cv.convert(r["garbage"] or "", tbl)
        after = measure_text(after_text)
        conn.execute("""INSERT OR REPLACE INTO conversion
            (run_id, sha256, page, family_id, split, n_chars_before,
             n_chars_after, coverage, invalid_rate_before, invalid_rate_after,
             dev_share_after, ocr_similarity, text_after, mapping_version,
             converted_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, r["sha256"], r["page"], family_id, split,
             before["n_chars"], after["n_chars"], coverage,
             before["invalid_rate"], after["invalid_rate"],
             after["dev_share"], similarity(after_text, r["ocr"] or ""),
             after_text[:600], MAPPING_VERSION, now))
        stored += 1
    conn.commit()
    print(f"  converted and stored {stored} pages\n")
    report(conn, run_id)


def report(conn, run_id):
    rows = conn.execute(
        "SELECT * FROM conversion WHERE run_id=?", (run_id,)).fetchall()
    if not rows:
        print("no conversion rows for this run — nothing to report.")
        return

    print("=== §4 conversion measures")
    print(f"{'split':8}{'pages':>7}{'coverage':>10}{'dev_share':>11}"
          f"{'valid_before':>14}{'valid_after':>13}{'ocr_sim':>9}")
    for split in ("train", "test"):
        s = [r for r in rows if r["split"] == split]
        if not s:
            continue
        n = len(s)
        # Structural validity is only meaningful where there is enough
        # Devanagari to judge; before conversion there is essentially none,
        # which is the whole point, so 'before' is reported as a baseline that
        # is expected to look artificially good.
        vb = sum(1 for r in s if (r["invalid_rate_before"] or 0) < INVALID_MAX) / n
        va = sum(1 for r in s if (r["invalid_rate_after"] or 0) < INVALID_MAX) / n
        print(f"{split:8}{n:>7}"
              f"{sum(r['coverage'] or 0 for r in s)/n:>10.3f}"
              f"{sum(r['dev_share_after'] or 0 for r in s)/n:>11.3f}"
              f"{vb:>14.3f}{va:>13.3f}"
              f"{sum(r['ocr_similarity'] or 0 for r in s)/n:>9.3f}")

    test = [r for r in rows if r["split"] == "test"]
    if test:
        print("\n--- held-out sample (the number that counts) ---")
        for r in sorted(test, key=lambda r: -(r["ocr_similarity"] or 0))[:2]:
            print(f"  best  sim={r['ocr_similarity']:.2f}  "
                  f"{' '.join((r['text_after'] or '').split())[:70]!r}")
        for r in sorted(test, key=lambda r: (r["ocr_similarity"] or 0))[:2]:
            print(f"  worst sim={r['ocr_similarity']:.2f}  "
                  f"{' '.join((r['text_after'] or '').split())[:70]!r}")

    print("\ncoverage  : share of characters the table matched. A page it barely")
    print("            touched would score well on everything else by default.")
    print("ocr_sim   : corroboration only — the table was LEARNED from OCR.")


def negative_control(conn, family_id, limit=200):
    """
    The control that makes the converter safe to recommend: run it over
    documents Phase 1 called CLEAN and confirm it leaves them alone.
    """
    tbl = cv.load_table(conn, family_id)
    if not tbl:
        print(f"no table stored for {family_id} — derive one first.")
        return
    rows = conn.execute("""
        SELECT e.text_sample AS text FROM extraction e
        JOIN audit a ON a.sha256 = e.sha256
        WHERE e.arm='pymupdf' AND a.verdict='CLEAN'
          AND e.dev_share > 0.30 AND e.n_chars > 200
        LIMIT ?""", (limit,)).fetchall()
    if not rows:
        print("no clean Devanagari pages available for the control.")
        return

    unchanged = 0
    worse = 0
    covs = []
    for r in rows:
        text = r["text"] or ""
        out, cov = cv.convert(text, tbl)
        covs.append(cov)
        unchanged += out == text
        if measure_text(out)["invalid_rate"] > measure_text(text)["invalid_rate"]:
            worse += 1

    n = len(rows)
    print(f"=== negative control — {family_id} over {n} CLEAN Devanagari pages")
    print(f"  left completely unchanged : {unchanged}/{n} = {unchanged/n:.1%}")
    print(f"  mean coverage (want ~0)   : {sum(covs)/n:.3f}")
    print(f"  made structurally WORSE   : {worse}/{n} = {worse/n:.1%}")
    if unchanged == n:
        print("\n  PASS — the converter does not touch correct Devanagari.")
    else:
        print("\n  ATTENTION — the converter modifies clean text. A table that")
        print("  fires on correct Devanagari corrupts working documents, which")
        print("  is worse than not converting at all.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--family", default="fam-01-dvttdhruvnor")
    ap.add_argument("--run", default="eval-1")
    ap.add_argument("--iterations", type=int, default=4)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--negative-control", action="store_true")
    ap.add_argument("--db", default=str(config.MANIFEST_DB))
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    if args.negative_control:
        negative_control(conn, args.family)
    elif args.report:
        report(conn, args.run)
    else:
        evaluate(conn, args.family, args.run, args.iterations)
    conn.close()


if __name__ == "__main__":
    main()
