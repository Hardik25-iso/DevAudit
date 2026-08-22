#!/usr/bin/env python3
"""
transcribe.py — collect hand-typed Devanagari, to break the OCR circularity.

    python transcribe.py --draw --sample-id hand-v1 --n 100
    python transcribe.py --next                    # transcribe one line
    python transcribe.py --report

Every accuracy figure in Phase 4 is OCR-derived. The mapping tables are learned
by aligning against Tesseract, and `ocr_similarity` scores the result against
Tesseract. The held-out split separates the *documents*; it does not separate
the *engine*, so a systematic OCR error is invisible to the evaluation and
`phase4-results.md` §5 says so.

The only fix is a reference that did not come from OCR. This collects one: a
human reads the rendered page and types what it says.

**A hundred lines is enough.** This is not training data — it is a yardstick.
It measures the converter and, incidentally, measures Tesseract, which tells
you how much of the residual error in Phase 4 is the converter's and how much
is the reference's.

Needs the external drive only for `--next`, which renders the page. `--draw`
and `--report` read the manifest.
"""

import argparse
import random
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import config
import convert as cv
import derive_mapping as dm

SCHEMA = """
-- ---------------------------------------------------------------------------
-- transcription — a human's reading of a rendered page line.
--
-- The one measurement in this project that does not come from a machine. Kept
-- separate from every OCR-derived table so it can never be mistaken for one,
-- and so "which figures are independent of Tesseract" is a question the schema
-- answers rather than the prose.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transcription (
    transcription_id INTEGER PRIMARY KEY,
    sample_id   TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    page        INTEGER NOT NULL,
    family_id   TEXT,
    -- What the text layer gave, what OCR gave, and what a person read. Storing
    -- all three means the comparison is a query, and means OCR's own error
    -- rate falls out of the same sample at no extra cost.
    legacy_text TEXT,
    ocr_text    TEXT,
    human_text  TEXT,           -- NULL until transcribed
    -- 'skipped' is a real answer: an unreadable scan or a page of digits is
    -- not a failure of the transcriber and must not be coerced into one.
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending|done|skipped
    note        TEXT,
    seed        INTEGER,
    drawn_at    TEXT,
    typed_at    TEXT,
    UNIQUE(sample_id, sha256, page)
);
"""


def draw(conn, sample_id, n, seed, family):
    """
    Draw pages to transcribe, at random, from pages the converter can be
    scored on: the family is known and both a legacy and an OCR reading exist.
    """
    sql = """
        SELECT t.sha256, t.page, m.family_id, t.text AS legacy, o.text AS ocr
        FROM page_text t
        JOIN page_text o ON o.sha256=t.sha256 AND o.page=t.page AND o.arm='ocr'
        JOIN font_observation f ON f.sha256 = t.sha256
        JOIN family_member m ON m.obs_id = f.obs_id
        WHERE t.arm='pymupdf' AND t.n_chars > 200 AND o.n_chars > 200
          AND t.dev_share < 0.05 AND o.dev_share > 0.40
    """
    params = []
    if family:
        sql += " AND m.family_id = ?"
        params.append(family)
    sql += " GROUP BY t.sha256, t.page"
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        print("no scorable pages — run extract_training.py first")
        return

    rng = random.Random(seed)
    rng.shuffle(rows)
    picked = rows[:n]
    now = datetime.now().isoformat(timespec="seconds")
    conn.executemany("""INSERT OR IGNORE INTO transcription
        (sample_id, sha256, page, family_id, legacy_text, ocr_text, seed, drawn_at)
        VALUES (?,?,?,?,?,?,?,?)""",
        [(sample_id, r["sha256"], r["page"],
          family_of_page(conn, r["legacy"]) or r["family_id"],
          (r["legacy"] or "")[:2000], (r["ocr"] or "")[:2000], seed, now)
         for r in picked])
    conn.commit()
    print(f"drew {len(picked)} pages into sample '{sample_id}' (seed {seed})")
    print(f"pool was {len(rows)} scorable pages")
    print("\nnow: python transcribe.py --next")


def render(conn, sha256, page):
    """Render the page to a PNG and try to open it. Needs the drive."""
    row = conn.execute("SELECT stored_path FROM documents WHERE sha256=?",
                       (sha256,)).fetchone()
    if not row:
        return None
    try:
        import fitz
        doc = fitz.open(row["stored_path"])
        png = Path(tempfile.gettempdir()) / f"devaudit_p{page}.png"
        doc[page - 1].get_pixmap(dpi=160).save(str(png))
        doc.close()
        return png
    except Exception as e:
        print(f"  (could not render: {type(e).__name__} — {e})")
        return None


def next_item(conn, sample_id, show_ocr):
    row = conn.execute("""SELECT * FROM transcription
        WHERE sample_id=? AND status='pending' ORDER BY transcription_id LIMIT 1""",
        (sample_id,)).fetchone()
    if not row:
        print(f"nothing pending in '{sample_id}'.")
        return

    done, total = conn.execute("""SELECT
        SUM(status!='pending'), COUNT(*) FROM transcription WHERE sample_id=?""",
        (sample_id,)).fetchone()
    print(f"\n=== {sample_id}  [{done or 0}/{total}]   {row['family_id']}")
    print(f"    document {row['sha256'][:12]}  page {row['page']}\n")

    png = render(conn, row["sha256"], row["page"])
    if png and png.exists():
        print(f"    rendered: {png}")
        try:                                    # best effort; not required
            subprocess.Popen(["cmd", "/c", "start", "", str(png)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    print("\n--- what the text layer gives (this is the corruption) ---")
    print("   ", " ".join((row["legacy_text"] or "").split())[:160])

    # The converter's attempt, shown because it costs nothing and makes the
    # session useful to watch. NOT shown before the human reads the page, for
    # the same reason annotate.py hides the detector's verdict: it is a prior.
    if show_ocr:
        print("\n--- what OCR read (SHOWN: this biases you toward its errors) ---")
        print("   ", " ".join((row["ocr_text"] or "").split())[:160])

    print("\nType the FIRST LINE of Devanagari you can read on the page.")
    print("Enter alone = skip (unreadable, or no Devanagari). Ctrl-C = stop.\n")
    try:
        typed = input("  > ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nstopped.")
        return

    if not typed:
        conn.execute("""UPDATE transcription SET status='skipped', typed_at=?
                        WHERE transcription_id=?""",
                     (datetime.now().isoformat(timespec="seconds"),
                      row["transcription_id"]))
        conn.commit()
        print("  skipped.")
    else:
        conn.execute("""UPDATE transcription SET human_text=?, status='done',
                        typed_at=? WHERE transcription_id=?""",
                     (typed, datetime.now().isoformat(timespec="seconds"),
                      row["transcription_id"]))
        conn.commit()
        print("  recorded.")
    print("  next: python transcribe.py --next")


def report(conn, sample_id):
    rows = conn.execute("SELECT * FROM transcription WHERE sample_id=?",
                        (sample_id,)).fetchall()
    if not rows:
        print(f"no sample '{sample_id}' — run --draw first")
        return
    done = [r for r in rows if r["status"] == "done"]
    print(f"sample '{sample_id}': {len(rows)} drawn, {len(done)} transcribed, "
          f"{sum(1 for r in rows if r['status']=='skipped')} skipped")
    if not done:
        print("\nNothing transcribed yet, so there is still no reference that is")
        print("independent of OCR. Every Phase 4 figure remains OCR-derived.")
        return

    import difflib
    import re
    norm = lambda s: re.sub(r"\s+", "", s or "")

    print(f"\n{'family':26}{'n':>4}{'converter vs human':>20}{'OCR vs human':>15}")
    fams = {}
    for r in done:
        fams.setdefault(r["family_id"], []).append(r)
    for fam, rs in sorted(fams.items()):
        tbl = cv.load_table(conn, fam)
        conv_s, ocr_s = [], []
        for r in rs:
            human = norm(r["human_text"])
            if not human:
                continue
            converted, _ = cv.convert(r["legacy_text"] or "", tbl)
            # The human typed ONE line; score the best matching window rather
            # than the whole page, or the measure reports page length.
            conv_s.append(best_window(norm(converted), human))
            ocr_s.append(best_window(norm(r["ocr_text"]), human))
        if conv_s:
            print(f"{fam:26}{len(conv_s):>4}"
                  f"{sum(conv_s)/len(conv_s):>20.3f}{sum(ocr_s)/len(ocr_s):>15.3f}")

    print("\nconverter vs human : the first figure in this project independent")
    print("                     of Tesseract.")
    print("OCR vs human       : Tesseract's own error rate on this corpus —")
    print("                     the ceiling every OCR-derived figure sits under.")


def best_window(haystack, needle):
    """
    Best similarity between `needle` and any same-length window of `haystack`.

    The transcriber types one line; the page holds many. Comparing against the
    whole page would measure page length, not accuracy.
    """
    import difflib
    if not haystack or not needle:
        return 0.0
    n = len(needle)
    if len(haystack) <= n:
        return difflib.SequenceMatcher(None, haystack, needle).ratio()
    best = 0.0
    for i in range(0, len(haystack) - n + 1, max(1, n // 4)):
        best = max(best, difflib.SequenceMatcher(
            None, haystack[i:i + n], needle).ratio())
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--sample-id", default="hand-v1")
    ap.add_argument("--draw", action="store_true")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=20260821)
    ap.add_argument("--family")
    ap.add_argument("--next", action="store_true")
    ap.add_argument("--show-ocr", action="store_true",
                    help="show OCR before you type (biases you; off by default)")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--relabel", action="store_true",
                    help="re-assign family by page signature")
    ap.add_argument("--db", default=str(config.MANIFEST_DB))
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()

    if args.draw:
        draw(conn, args.sample_id, args.n, args.seed, args.family)
    elif args.relabel:
        relabel(conn, args.sample_id)
    elif args.next:
        next_item(conn, args.sample_id, args.show_ocr)
    else:
        report(conn, args.sample_id)
    conn.close()


def family_of_page(conn, text, centroids=None):
    """
    Which family does this PAGE look like, by its own character signature?

    Not by document membership. `family_member` is keyed per observation, so a
    document carrying two families' fonts belongs to both, and a page drawn
    from it gets whichever label the join happened to return. Measured on the
    drawn sample: 14 of 97 pages were labelled as a family their own text does
    not match, mostly fam-02 rows that are really fam-01.

    That matters here more than anywhere else. Scoring a fam-01 page with
    fam-02's table produces near-zero agreement and blames the table.

    Uses `centroid_pagetext` where present -- these are pages, and the
    excerpt-derived centroids are the wrong grain for them.
    """
    import json
    import legacy_families as lf
    if centroids is None:
        centroids = {r["family_id"]: json.loads(r["centroid_pagetext"] or r["centroid"])
                     for r in conn.execute("SELECT * FROM font_family")}
    sig = lf.signature(text or "")
    if not sig or not centroids:
        return None
    return max(centroids, key=lambda f: lf.cosine(sig, centroids[f]))


def relabel(conn, sample_id):
    """Re-assign family by page signature. Safe while nothing is transcribed."""
    import json
    cents = {r["family_id"]: json.loads(r["centroid_pagetext"] or r["centroid"])
             for r in conn.execute("SELECT * FROM font_family")}
    rows = conn.execute("SELECT * FROM transcription WHERE sample_id=?",
                        (sample_id,)).fetchall()
    moved = 0
    for r in rows:
        fam = family_of_page(conn, r["legacy_text"], cents)
        if fam and fam != r["family_id"]:
            conn.execute("UPDATE transcription SET family_id=? WHERE transcription_id=?",
                         (fam, r["transcription_id"]))
            moved += 1
    conn.commit()
    print(f"relabelled {moved}/{len(rows)} pages by their own signature")
    from collections import Counter
    d = Counter(r[0] for r in conn.execute(
        "SELECT family_id FROM transcription WHERE sample_id=?", (sample_id,)))
    for f, n in sorted(d.items()):
        print(f"   {f:26} {n:>4}")


if __name__ == "__main__":
    main()
