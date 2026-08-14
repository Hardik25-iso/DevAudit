#!/usr/bin/env python3
"""
annotate.py — label one font observation at a time.

Reads `annotation_sample`, shows the stored excerpts, writes `annotation`
rows. Touches no PDFs and needs no external drive; that is the whole reason
excerpts are stored.

    python annotate.py --next                     # show the next unlabelled card
    python annotate.py --label 4127 LEGACY_8BIT   # record a judgement
    python annotate.py --progress                 # how far through, by stratum

Deliberately not a prompt-driven REPL. One command per action means the same
tool works over SSH, in a notebook, from a script driving an LLM pass, and in
a terminal that cannot supply stdin — and every judgement is a shell line that
can be reviewed afterwards.

Blindness is enforced here rather than promised in a document. The card shows
the extracted text and how much of it there is. It does not show the font
name, the detector's verdict, the signal values, the Phase 1 bucket, or the
issuing body. Knowing that a document is from Nashik (72% legacy) is a real
prior, and so is a font called DVBW-TTSurekh.
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timezone

import config
from phase0_schema import GUIDELINE_VERSION

# docs/phase0-schema.md §3.1. NO_EVIDENCE is missing on purpose: it is what
# the detector says when nothing fired, and a human who has read the text
# either judges it or abstains with UNDECIDABLE.
LABELS = [
    "CORRECT", "LEGACY_8BIT", "LEGACY_ASCII", "LEGACY_SYMBOL",
    "CMAP_INVALID", "PARTIAL", "NO_LINGUISTIC_TEXT", "UNDECIDABLE",
]
SCRIPTS = ["deva", "latin", "mixed", "other", "none"]


def next_card(conn, sample_id, annotator):
    row = conn.execute("""
        SELECT o.obs_id, s.stratum, o.sampled_chars, o.n_pages_seen,
               o.n_pages_declared, o.signals_version
        FROM annotation_sample s
        JOIN font_observation o ON o.obs_id = s.obs_id
        WHERE s.sample_id = ?
          AND o.obs_id NOT IN (SELECT obs_id FROM annotation
                               WHERE annotator = ? AND round = 1)
        ORDER BY o.obs_id LIMIT 1""", (sample_id, annotator)).fetchone()
    if not row:
        print(f"nothing left unlabelled in '{sample_id}' for {annotator}")
        return None

    obs_id, stratum, chars, seen, declared, version = row
    print("=" * 68)
    print(f"obs {obs_id}    {chars} chars sampled over {seen} page(s); "
          f"font declared on {declared}")
    if version.endswith("+deep"):
        print("           (whole document read, not just the first 8 pages)")
    print("=" * 68)
    for page, kind, text in conn.execute(
            "SELECT page, kind, text FROM excerpt WHERE obs_id=? "
            "ORDER BY CASE kind WHEN 'head' THEN 0 WHEN 'violation' THEN 1 "
            "ELSE 2 END, char_start", (obs_id,)):
        print(f"\n--- {kind}, page {page} ---")
        print(text[:600])
    print("\n" + "-" * 68)
    print(f"stratum {stratum}")
    print(f"python annotate.py --label {obs_id} <{'|'.join(LABELS[:4])}|...>")
    return obs_id


def record(conn, obs_id, label, script, confidence, note, annotator,
           round_, saw_name):
    if label not in LABELS:
        sys.exit(f"unknown label {label!r}; one of {', '.join(LABELS)}")
    if script and script not in SCRIPTS:
        sys.exit(f"unknown script {script!r}; one of {', '.join(SCRIPTS)}")
    if not conn.execute("SELECT 1 FROM font_observation WHERE obs_id=?",
                        (obs_id,)).fetchone():
        sys.exit(f"no observation {obs_id}")

    try:
        conn.execute("""
            INSERT INTO annotation
              (obs_id, sample_id, annotator, round, label, script, confidence,
               saw_detector_output, saw_font_name, guideline_version, note,
               annotated_at)
            VALUES (?, (SELECT sample_id FROM annotation_sample
                        WHERE obs_id=? LIMIT 1), ?,?,?,?,?, 0, ?, ?, ?, ?)""",
            (obs_id, obs_id, annotator, round_, label, script, confidence,
             int(saw_name), GUIDELINE_VERSION, note,
             datetime.now(timezone.utc).isoformat(timespec="seconds")))
    except sqlite3.IntegrityError:
        # Append-only: a second opinion is a new round, never an overwrite.
        # Agreement can only be computed from labels as originally given.
        sys.exit(f"obs {obs_id} already labelled by {annotator} in round "
                 f"{round_}; use --round 2 for a blind re-annotation")
    conn.commit()
    print(f"obs {obs_id} -> {label}"
          f"{f' ({script})' if script else ''} as {annotator}, "
          f"guidelines v{GUIDELINE_VERSION}")


def progress(conn, sample_id, annotator):
    rows = conn.execute("""
        SELECT s.stratum, COUNT(*),
               SUM(a.obs_id IS NOT NULL)
        FROM annotation_sample s
        LEFT JOIN annotation a
          ON a.obs_id = s.obs_id AND a.annotator = ? AND a.round = 1
        WHERE s.sample_id = ?
        GROUP BY s.stratum ORDER BY s.stratum""", (annotator, sample_id))
    print(f"{'stratum':36} {'done':>6} {'of':>5}")
    total = done = 0
    for stratum, n, d in rows:
        print(f"{stratum:36} {d or 0:6} {n:5}")
        total += n
        done += d or 0
    print(f"{'':36} {done:6} {total:5}   ({100.0 * done / max(total, 1):.0f}%)")

    labels = conn.execute(
        "SELECT label, COUNT(*) FROM annotation WHERE annotator=? AND round=1"
        " GROUP BY label ORDER BY 2 DESC", (annotator,)).fetchall()
    if labels:
        print("\nlabels so far: "
              + ", ".join(f"{l} {n}" for l, n in labels))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--sample-id", default="gt-v1")
    ap.add_argument("--annotator", default="hardik")
    ap.add_argument("--round", type=int, default=1,
                    help="2 for a blind re-annotation, at least 7 days later")
    ap.add_argument("--next", action="store_true")
    ap.add_argument("--progress", action="store_true")
    ap.add_argument("--label", nargs=2, metavar=("OBS_ID", "LABEL"))
    ap.add_argument("--script", default=None, choices=SCRIPTS)
    ap.add_argument("--confidence", type=int, default=None, choices=[1, 2, 3])
    ap.add_argument("--note", default=None,
                    help="required tags: #spurious-space, #reading-order")
    ap.add_argument("--show-name", action="store_true",
                    help="reveal the font name; recorded on the row, and "
                         "excluded from any name-blind analysis")
    args = ap.parse_args()

    conn = sqlite3.connect(config.MANIFEST_DB)
    if args.label:
        record(conn, int(args.label[0]), args.label[1], args.script,
               args.confidence, args.note, args.annotator, args.round,
               args.show_name)
    elif args.progress:
        progress(conn, args.sample_id, args.annotator)
    else:
        obs = next_card(conn, args.sample_id, args.annotator)
        if obs and args.show_name:
            name = conn.execute("SELECT font_name FROM font_observation "
                                "WHERE obs_id=?", (obs,)).fetchone()[0]
            print(f"\nfont name (revealed): {name}")


if __name__ == "__main__":
    main()
