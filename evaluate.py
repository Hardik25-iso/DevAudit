#!/usr/bin/env python3
"""
evaluate.py — turn labels into the numbers Phase 2 exists to produce.

Four reports, all reading the database and nothing else:

    python evaluate.py --agreement     # how consistent the labelling is
    python evaluate.py --detector      # precision and recall against truth
    python evaluate.py --sweep mojibake_ratio   # a threshold's PR curve
    python evaluate.py --estimate      # the corpus rate, with an interval

Order matters. Agreement gates the rest: labels that two passes cannot
reproduce are not ground truth, and a precision figure computed against them
measures nothing. docs/phase0-schema.md §5.3 fixes the threshold at kappa 0.7
per class, in advance, so it cannot be relaxed later to make a result work.
"""

import argparse
import collections
import math
import sqlite3
import sys

import config
import font_audit as fa

# Labels that assert corruption. The binary task -- is this font's text layer
# wrong -- is what the project claims, so it is scored separately from the
# harder question of which of the four mechanisms broke it.
POSITIVE = ("LEGACY_8BIT", "LEGACY_ASCII", "LEGACY_SYMBOL", "CMAP_INVALID")
KAPPA_FLOOR = 0.7

# Which class each signal is actually trying to catch. A signal must be scored
# against its own target, not against every corrupt label: mojibake_ratio
# cannot detect a Kruti Dev font at any threshold, so scoring it against all
# positives caps its recall at LEGACY_8BIT's share of them and makes a
# correctly-tuned threshold look broken.
SIGNAL_TARGET = {
    "mojibake_ratio": "LEGACY_8BIT",
    "ascii_k_ratio": "LEGACY_ASCII",
    "symbol_per_1k": "LEGACY_SYMBOL",
    "invalid_rate_per_1k": "CMAP_INVALID",
    "invalid_matras_nospace": "CMAP_INVALID",
}


def cohen_kappa(pairs):
    """
    Agreement between two labellings of the same items, corrected for chance.

    Raw percent agreement flatters any task with a dominant class: a corpus
    that is 70% one label gives 70% agreement to two annotators who never look
    at the text. Kappa subtracts the agreement two independent labellers would
    reach by chance given their own class frequencies.

    Returns None when chance agreement is 1.0 -- both passes used a single
    label throughout, so there is no variation to agree about. That is an
    undefined kappa, not a perfect one, and reporting 1.0 there would be a lie.
    """
    if not pairs:
        return None
    n = len(pairs)
    observed = sum(1 for a, b in pairs if a == b) / n
    count_a = collections.Counter(a for a, _ in pairs)
    count_b = collections.Counter(b for _, b in pairs)
    expected = sum((count_a[k] / n) * (count_b[k] / n)
                   for k in set(count_a) | set(count_b))
    if expected >= 1.0:
        return None
    return (observed - expected) / (1 - expected)


def prf(tp, fp, fn):
    """Precision, recall, F1. Zero denominators report as 0.0, not as errors."""
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if precision + recall else 0.0)
    return precision, recall, f1


def stratified_estimate(strata):
    """
    Corpus proportion and standard error from a stratified sample.

    `strata` is [(stratum_size, n_drawn, n_positive)]. The labelled proportion
    is NOT the corpus proportion -- the draw deliberately over-weights the
    cells where the detector has never been checked -- so each stratum is
    weighted back by its share of the frame.

    Carries the finite-population correction: a stratum where most of the
    members were labelled has little sampling error left, and omitting the
    correction would report an interval wider than the evidence.
    """
    total = sum(size for size, _, _ in strata)
    if not total:
        return 0.0, 0.0
    estimate = variance = 0.0
    for size, drawn, positive in strata:
        if not drawn:
            continue
        share = size / total
        p = positive / drawn
        estimate += share * p
        if drawn > 1:
            fpc = 1 - (drawn / size) if size else 0.0
            variance += (share ** 2) * (p * (1 - p) / drawn) * fpc
    return estimate, math.sqrt(variance)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
def _labels_by(conn, annotator, round_):
    return dict(conn.execute(
        "SELECT obs_id, label FROM annotation WHERE annotator=? AND round=?",
        (annotator, round_)).fetchall())


def _kappa_table(title, pairs, caveat=None):
    print(f"\n--- {title} ---")
    if not pairs:
        print("  no overlapping labels yet")
        return
    overall = cohen_kappa(pairs)
    agree = sum(1 for a, b in pairs if a == b)
    print(f"  n={len(pairs)}  agreement {100.0 * agree / len(pairs):.0f}%  "
          f"kappa {'n/a' if overall is None else f'{overall:.3f}'}")
    if caveat:
        print(f"  {caveat}")

    # Per class, one-vs-rest: an aggregate kappa can clear the floor while a
    # rare class nobody can label consistently sits underneath it.
    print(f"  {'class':22} {'n':>4} {'kappa':>7}")
    for label in sorted({a for a, _ in pairs} | {b for _, b in pairs}):
        binary = [(a == label, b == label) for a, b in pairs]
        k = cohen_kappa(binary)
        n = sum(1 for a, b in pairs if label in (a, b))
        flag = "" if k is None or k >= KAPPA_FLOOR else "   BELOW FLOOR"
        print(f"  {label:22} {n:4} "
              f"{'n/a' if k is None else f'{k:7.3f}'}{flag}")


def agreement(conn, annotator):
    """Intra-annotator (round 1 vs 2) and human-vs-model reliability."""
    r1, r2 = _labels_by(conn, annotator, 1), _labels_by(conn, annotator, 2)
    _kappa_table(
        f"intra-annotator: {annotator} round 1 vs round 2",
        [(r1[o], r2[o]) for o in r1.keys() & r2.keys()],
        "this is the honest reliability figure for a one-person project")

    for (other,) in conn.execute(
            "SELECT DISTINCT annotator FROM annotation WHERE annotator != ?",
            (annotator,)):
        pairs = [(r1[o], lab) for o, lab in _labels_by(conn, other, 1).items()
                 if o in r1]
        _kappa_table(
            f"{annotator} vs {other} (round 1)", pairs,
            "a second opinion, not an independent annotator -- adjudicate the "
            "disagreements, do not report this as reliability"
            if other.startswith("llm:") else None)


def detector(conn):
    """Detector output against adjudicated truth."""
    rows = conn.execute(
        "SELECT detector_label, final_label, basis FROM ground_truth").fetchall()
    if not rows:
        print("no adjudicated labels yet -- nothing to evaluate")
        return

    single = sum(1 for _, _, basis in rows if basis == "single")
    print(f"{len(rows)} adjudicated observations"
          + (f" ({single} single-pass)" if single else ""))

    tp = sum(1 for d, t, _ in rows if d in POSITIVE and t in POSITIVE)
    fp = sum(1 for d, t, _ in rows if d in POSITIVE and t not in POSITIVE)
    fn = sum(1 for d, t, _ in rows if d not in POSITIVE and t in POSITIVE)
    tn = len(rows) - tp - fp - fn
    p, r, f1 = prf(tp, fp, fn)
    print(f"\n--- is this text layer wrong? ---")
    print(f"  precision {p:.3f}   recall {r:.3f}   F1 {f1:.3f}")
    print(f"  tp {tp}  fp {fp}  fn {fn}  tn {tn}")
    if fn:
        # The documented failure mode. Every defect found in the original tool
        # pushed the estimate down, so misses are the direction to distrust.
        print(f"  {fn} missed -- the instrument fails toward silence, so this "
              f"is the number that matters")

    print(f"\n--- which mechanism? ---")
    print(f"  {'truth':22} {'n':>4} {'prec':>6} {'rec':>6} {'F1':>6}")
    for label in sorted({t for _, t, _ in rows}):
        ltp = sum(1 for d, t, _ in rows if d == label and t == label)
        lfp = sum(1 for d, t, _ in rows if d == label and t != label)
        lfn = sum(1 for d, t, _ in rows if d != label and t == label)
        p, r, f1 = prf(ltp, lfp, lfn)
        n = sum(1 for _, t, _ in rows if t == label)
        print(f"  {label:22} {n:4} {p:6.3f} {r:6.3f} {f1:6.3f}")

    print(f"\n--- disagreements, most common first ---")
    confusion = collections.Counter(
        (d, t) for d, t, _ in rows if d != t
        and not (d == "NO_EVIDENCE" and t == "CORRECT"))
    for (d, t), n in confusion.most_common(10):
        print(f"  detector {d:20} truth {t:20} {n}")
    if not confusion:
        print("  none")


def sweep(conn, signal, steps=20):
    """
    Precision and recall across the range of one signal.

    This is the query the whole schema exists to make possible. Phase 1 stored
    only the rule that fired, so re-tuning a threshold meant re-reading 1,602
    PDFs; every signal is now stored for every font, convicted or not.
    """
    rows = conn.execute(
        f"SELECT {signal}, final_label FROM ground_truth "
        f"WHERE {signal} IS NOT NULL").fetchall()
    if not rows:
        print(f"no adjudicated observations carry {signal}")
        return

    values = sorted(v for v, _ in rows)
    lo, hi = values[0], values[-1]
    if lo == hi:
        print(f"{signal} is {lo} for every labelled observation; nothing to sweep")
        return

    shipped = {"mojibake_ratio": fa.PERFONT_MOJIBAKE,
               "ascii_k_ratio": fa.PERFONT_ASCII_K,
               "symbol_per_1k": fa.PERFONT_SYMBOL,
               "invalid_rate_per_1k": fa.SUSPECT_RATE_PER_1K}.get(signal)

    target = SIGNAL_TARGET.get(signal)
    hits = (lambda lab: lab == target) if target else (lambda lab: lab in POSITIVE)
    n_target = sum(1 for _, lab in rows if hits(lab))

    print(f"sweeping {signal} over {len(rows)} labelled observations "
          f"[{lo:.4g}, {hi:.4g}]")
    if target:
        print(f"  scored against {target} ({n_target} observations) -- the class "
              f"this signal targets, not every corrupt label")
    else:
        print(f"  no target class recorded; scored against all corrupt labels")
    print(f"  {'threshold':>10} {'prec':>6} {'rec':>6} {'F1':>6}")
    for i in range(steps + 1):
        t = lo + (hi - lo) * i / steps
        tp = sum(1 for v, lab in rows if v >= t and hits(lab))
        fp = sum(1 for v, lab in rows if v >= t and not hits(lab))
        fn = sum(1 for v, lab in rows if v < t and hits(lab))
        p, r, f1 = prf(tp, fp, fn)
        mark = ""
        if shipped is not None and i and abs(t - shipped) <= (hi - lo) / steps / 2:
            mark = "  <- shipped"
        print(f"  {t:10.4g} {p:6.3f} {r:6.3f} {f1:6.3f}{mark}")
    if shipped is not None:
        print(f"  shipped threshold is {shipped}")


def estimate(conn, sample_id):
    """Corpus proportion, reweighted from the stratified draw."""
    rows = conn.execute("""
        SELECT s.stratum, s.stratum_size, COUNT(*),
               SUM(adj.final_label IN ('LEGACY_8BIT','LEGACY_ASCII',
                                       'LEGACY_SYMBOL','CMAP_INVALID'))
        FROM annotation_sample s
        JOIN adjudication adj ON adj.obs_id = s.obs_id
        WHERE s.sample_id = ?
        GROUP BY s.stratum, s.stratum_size""", (sample_id,)).fetchall()
    if not rows:
        print(f"no adjudicated labels in sample '{sample_id}'")
        return

    print(f"{'stratum':36} {'size':>6} {'lab':>5} {'pos':>5} {'rate':>7}")
    for stratum, size, drawn, positive in rows:
        print(f"{stratum:36} {size:6} {drawn:5} {positive or 0:5} "
              f"{(positive or 0) / drawn:7.3f}")

    point, se = stratified_estimate([(s, d, p or 0) for _, s, d, p in rows])
    print(f"\ncorpus rate of corrupt font observations: "
          f"{100 * point:.1f}% +/- {100 * 1.96 * se:.1f} (95%)")
    print("Font observations, not documents. The document-level figure needs "
          "the same weighting applied after collapsing fonts per document, "
          "and the macro average by issuing body alongside it.")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--agreement", action="store_true")
    ap.add_argument("--detector", action="store_true")
    ap.add_argument("--sweep", metavar="SIGNAL", default=None)
    ap.add_argument("--estimate", action="store_true")
    ap.add_argument("--annotator", default="hardik")
    ap.add_argument("--sample-id", default="gt-v1")
    args = ap.parse_args()

    conn = sqlite3.connect(config.MANIFEST_DB)
    if args.agreement:
        agreement(conn, args.annotator)
    elif args.detector:
        detector(conn)
    elif args.sweep:
        sweep(conn, args.sweep)
    elif args.estimate:
        estimate(conn, args.sample_id)
    else:
        sys.exit("pass one of --agreement, --detector, --sweep SIGNAL, "
                 "--estimate")


if __name__ == "__main__":
    main()
