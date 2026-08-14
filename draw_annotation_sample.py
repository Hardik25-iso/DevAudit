#!/usr/bin/env python3
"""
draw_annotation_sample.py — draw the stratified ground-truth sample.

Reads `font_observation`, writes `annotation_sample`. Touches no PDFs and
needs no external drive.

    python draw_annotation_sample.py --dry-run
    python draw_annotation_sample.py --sample-id gt-v1 --seed 20260814

Two things this script exists to prevent.

Sampling has bitten this project three times, so the frame is recorded before
anything is drawn: stratum size, number drawn, and selection probability per
row. Without those the labelled proportion cannot be turned back into a corpus
proportion, and a stratified sample read as if it were a random one is exactly
the PMC mistake in a new costume.

The second axis of the strata breaks a circularity. Phase 1 validated the
per-font detector against fonts identifiable BY NAME, which cannot say
anything about the 32% of legacy documents where no name matched. So the draw
deliberately over-weights fonts whose names are uninformative — the cell where
the detector has never been checked against anything.
"""

import argparse
import random
import sqlite3
import sys
from datetime import datetime, timezone

import config
import font_audit as fa


POSITIVE = ("LEGACY_8BIT", "LEGACY_ASCII", "LEGACY_SYMBOL", "CMAP_INVALID")

# (detector axis, name axis) -> how many observations to draw.
# The uninformative-name cells are the largest on purpose; see the docstring.
QUOTAS = {
    ("positive", "name_legacy"):      50,
    ("positive", "name_known_good"):  50,
    ("positive", "name_uninformative"): 100,
    ("no_evidence", "name_legacy"):     50,
    ("no_evidence", "name_known_good"): 100,
    ("no_evidence", "name_uninformative"): 100,
}


def name_axis(font_name):
    """Which side of the name lists a font falls on — or neither."""
    if fa._match_any(font_name, fa.LEGACY_PATTERNS):
        return "name_legacy"
    if fa._match_any(font_name, fa.KNOWN_GOOD):
        return "name_known_good"
    return "name_uninformative"


def detector_axis(label):
    if label in POSITIVE:
        return "positive"
    if label == "NO_EVIDENCE":
        return "no_evidence"
    return None      # UNDECIDABLE: nothing to show an annotator


def plan_strata(rows, quotas=QUOTAS, seed=0):
    """
    Assign observations to strata and draw from each.

    Pure: takes (obs_id, font_name, detector_label) tuples and returns
    (assignments, summary). Kept free of the database so the draw can be
    tested and re-derived without one.

    Sorting by obs_id before shuffling matters — SQLite row order is not
    guaranteed stable across versions, and a seed that reproduces only on one
    machine is not a reproducible sample.
    """
    buckets = {}
    for obs_id, font_name, label in rows:
        axis = detector_axis(label)
        if axis is None:
            continue
        buckets.setdefault((axis, name_axis(font_name)), []).append(obs_id)

    assignments, summary = [], []
    for stratum, ids in sorted(buckets.items()):
        ids = sorted(ids)
        want = quotas.get(stratum, 0)
        # A stratum smaller than its quota is taken whole, at probability 1.0.
        # Silently drawing fewer without recording it would leave the weights
        # wrong in the direction that makes the sample look better than it is.
        take = min(want, len(ids))
        rng = random.Random(f"{seed}:{stratum[0]}:{stratum[1]}")
        drawn = sorted(rng.sample(ids, take)) if take else []
        prob = (take / len(ids)) if ids else 0.0
        for obs_id in drawn:
            assignments.append({
                "obs_id": obs_id,
                "stratum": f"{stratum[0]}/{stratum[1]}",
                "stratum_size": len(ids),
                "drawn": take,
                "selection_prob": prob,
            })
        summary.append({
            "stratum": f"{stratum[0]}/{stratum[1]}",
            "size": len(ids), "want": want, "took": take, "prob": prob,
        })
    return assignments, summary


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--sample-id", default="gt-v1")
    ap.add_argument("--seed", type=int, default=20260814)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan, write nothing")
    args = ap.parse_args()

    conn = sqlite3.connect(config.MANIFEST_DB)
    rows = conn.execute(
        "SELECT obs_id, font_name, detector_label FROM font_observation "
        "WHERE sampled_chars >= ?", (fa.PERFONT_MIN_CHARS,)).fetchall()
    if not rows:
        print("no judgeable observations — run extract_observations.py first")
        return

    # Reported, never quietly filtered: these are fonts the instrument saw and
    # could not judge, and their number is a finding about the 8-page sampling
    # cap rather than a fact about the fonts.
    thin, thin_but_used = conn.execute(
        "SELECT COUNT(*), SUM(n_pages_declared >= 5) FROM font_observation "
        "WHERE sampled_chars < ?", (fa.PERFONT_MIN_CHARS,)).fetchone()

    assignments, summary = plan_strata(rows, seed=args.seed)

    print(f"sample_id={args.sample_id}  seed={args.seed}\n")
    print(f"{'stratum':36} {'size':>6} {'want':>5} {'took':>5} {'p':>7}")
    for s in summary:
        print(f"{s['stratum']:36} {s['size']:6} {s['want']:5} {s['took']:5} "
              f"{s['prob']:7.3f}")
    print(f"\n{len(assignments)} observations drawn from {len(rows)} judgeable")
    print(f"{thin} observations below the {fa.PERFONT_MIN_CHARS}-char floor "
          f"are outside the frame")
    print(f"  of those, {thin_but_used or 0} are declared on 5+ pages — "
          f"under-sampled by the {fa.PERFONT_MAX_PAGES}-page cap, not empty")

    if args.dry_run:
        print("\ndry run — nothing written")
        return

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.executemany(
        "INSERT OR REPLACE INTO annotation_sample "
        "(sample_id, obs_id, stratum, stratum_size, drawn, selection_prob, "
        " seed, created_at) VALUES (?,?,?,?,?,?,?,?)",
        [(args.sample_id, a["obs_id"], a["stratum"], a["stratum_size"],
          a["drawn"], a["selection_prob"], args.seed, now)
         for a in assignments])
    conn.commit()
    print(f"\nwritten to annotation_sample as '{args.sample_id}'")


if __name__ == "__main__":
    main()
