#!/usr/bin/env python3
"""
compare_fonts.py — the secondary, per-font comparison (design §2.1).

Phase 3's primary unit is the page, because three of the five arms cannot say
which font produced which characters. Two of them can: PyMuPDF exposes a font
name per span, pdfplumber exposes one per character. For those two only, the
Phase 2 grain survives — which means their output can be joined directly onto
the 434 labelled observations instead of being diluted across mixed-font pages.

    python compare_fonts.py --run fonts-20260819          # the pass (needs the drive)
    python compare_fonts.py --run fonts-20260819 --report # read it back

This answers a question the page-level reports cannot: when two extractors
disagree on a page, is it because they decoded the *font* differently, or
because they laid the page out differently? Same characters under the same font
name means the disagreement is ordering; different characters means it is
decoding.

Deliberately a separate table from `extraction`. The grains are different
(document+font here, document+page there) and merging them would mean a nullable
discriminator column that every query has to remember to filter on.
"""

import argparse
import datetime
import sqlite3
from collections import Counter, defaultdict

import config
import extractors as ex
import font_audit as fa
from phase3_schema import SIGNALS_VERSION

SCHEMA = """
CREATE TABLE IF NOT EXISTS font_extraction (
    run_id      TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    font_name   TEXT NOT NULL,      -- subset prefix stripped, as font_observation
    arm         TEXT NOT NULL,      -- pymupdf | pdfplumber
    n_chars     INTEGER,
    dev_chars   INTEGER,
    dev_share   REAL,
    mojibake_ratio  REAL,
    ascii_k_ratio   REAL,
    invalid_rate_per_1k REAL,
    replacement_ratio   REAL,
    text_hash   TEXT,
    bag_hash    TEXT,
    text_sample TEXT,
    signals_version TEXT,
    extracted_at    TEXT,
    PRIMARY KEY (run_id, sha256, font_name, arm)
);
"""


def fonts_pymupdf(path, max_pages):
    """Reuses the Phase 1 primitive, so this arm's grouping is by definition
    the one every existing number was computed from."""
    import fitz
    doc = fitz.open(str(path))
    try:
        return {n: r["text"]
                for n, r in fa.collect_font_spans(doc, max_pages).items()}
    finally:
        doc.close()


def fonts_pdfplumber(path, max_pages):
    """
    Group characters by font name. pdfplumber has no span concept, so the
    spaces PyMuPDF gets from span boundaries have to come from pdfplumber's own
    word segmentation -- which is exactly the difference §3.4 is measuring, so
    it is left alone rather than normalised away.
    """
    import pdfplumber
    per = defaultdict(list)
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages[:max_pages]:
            for ch in page.chars:
                name = fa._base_name(ch.get("fontname") or "")
                if name:
                    per[name].append(ch.get("text") or "")
    return {n: "".join(v)[:fa.PERFONT_CAP_CHARS] for n, v in per.items()}


ARMS = {"pymupdf": fonts_pymupdf, "pdfplumber": fonts_pdfplumber}


def run_pass(conn, run_id, max_pages, limit):
    docs = conn.execute("""
        SELECT DISTINCT d.sha256, d.stored_path
        FROM annotation_sample s
        JOIN font_observation o ON o.obs_id = s.obs_id
        JOIN documents d ON d.sha256 = o.sha256
        JOIN audit a ON a.sha256 = d.sha256
        WHERE s.sample_id = 'gt-v1' AND a.verdict != 'SCAN'
    """).fetchall()
    if limit:
        docs = docs[:limit]
    print(f"run_id : {run_id}\ndocs   : {len(docs)}\narms   : {', '.join(ARMS)}")

    tally = Counter()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for i, (sha, path) in enumerate(docs, 1):
        for arm, fn in ARMS.items():
            try:
                per_font = fn(path, max_pages)
            except Exception as e:
                tally[f"error:{arm}"] += 1
                continue
            for name, text in per_font.items():
                m = ex.measure(text)
                conn.execute("""INSERT OR REPLACE INTO font_extraction
                    (run_id, sha256, font_name, arm, n_chars, dev_chars,
                     dev_share, mojibake_ratio, ascii_k_ratio,
                     invalid_rate_per_1k, replacement_ratio, text_hash,
                     bag_hash, text_sample, signals_version, extracted_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (run_id, sha, name, arm, m["n_chars"], m["dev_chars"],
                     m["dev_share"], m["mojibake_ratio"], m["ascii_k_ratio"],
                     m["invalid_rate_per_1k"], m["replacement_ratio"],
                     m["text_hash"], m["bag_hash"], m["text_sample"],
                     SIGNALS_VERSION, now))
                tally[f"ok:{arm}"] += 1
        if i % 25 == 0:
            conn.commit()
            print(f"  [{i}/{len(docs)}] {dict(sorted(tally.items()))}", flush=True)
    conn.commit()
    print(f"\ndone: {dict(sorted(tally.items()))}")


def report(conn, run_id):
    rows = conn.execute("""
        SELECT f.*, gt.final_label
        FROM font_extraction f
        LEFT JOIN font_observation o
               ON o.sha256 = f.sha256 AND o.font_name = f.font_name
        LEFT JOIN ground_truth gt ON gt.obs_id = o.obs_id
        WHERE f.run_id = ?
    """, (run_id,)).fetchall()
    if not rows:
        print("no rows for this run — nothing to report.")
        return

    by_key = defaultdict(dict)
    labels = {}
    for r in rows:
        by_key[(r["sha256"], r["font_name"])][r["arm"]] = r
        if r["final_label"]:
            labels[(r["sha256"], r["font_name"])] = r["final_label"]

    print(f"\n=== per-font agreement, pymupdf vs pdfplumber   (run {run_id})")
    print("The two arms that can attribute text to a font, so the only place")
    print("Phase 2's grain survives and the 434 labels can be joined directly.\n")
    print(f"{'label':20}{'fonts':>7}{'identical':>11}{'same chars':>12}"
          f"{'differ':>8}{'only 1 arm':>12}")

    grid = defaultdict(Counter)
    for key, arms in by_key.items():
        lab = labels.get(key, "(unlabelled)")
        g = grid[lab]
        g["n"] += 1
        if len(arms) < 2:
            g["one_arm"] += 1
        elif arms["pymupdf"]["text_hash"] == arms["pdfplumber"]["text_hash"]:
            g["identical"] += 1
        elif arms["pymupdf"]["bag_hash"] == arms["pdfplumber"]["bag_hash"]:
            g["bag"] += 1
        else:
            g["differ"] += 1

    order = ["CORRECT", "LEGACY_8BIT", "LEGACY_ASCII", "LEGACY_SYMBOL",
             "CMAP_INVALID", "PARTIAL", "NO_LINGUISTIC_TEXT", "UNDECIDABLE",
             "(unlabelled)"]
    for lab in [l for l in order if l in grid] + \
               [l for l in sorted(grid) if l not in order]:
        g = grid[lab]
        n = max(g["n"], 1)
        print(f"{lab:20}{g['n']:>7}{g['identical']/n:>11.3f}{g['bag']/n:>12.3f}"
              f"{g['differ']/n:>8.3f}{g['one_arm']/n:>12.3f}")

    print("\nidentical : both arms decoded this font to the same characters,")
    print("            in the same order — the disagreement, if any, is spacing")
    print("same chars: same characters, different order — a reading-order defect")
    print("differ    : the arms decoded the same font differently — the")
    print("            fallback-ladder divergence of design §2.2, isolated")
    print("only 1 arm: one arm did not see this font at all")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--run", required=True)
    ap.add_argument("--max-pages", type=int, default=fa.PERFONT_MAX_PAGES)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--db", default=str(config.MANIFEST_DB))
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()

    if args.report:
        report(conn, args.run)
    else:
        run_pass(conn, args.run, args.max_pages, args.limit)
        report(conn, args.run)
    conn.close()


if __name__ == "__main__":
    main()
