#!/usr/bin/env python3
"""
adjudicate.py — reconcile the two annotation passes into one final label.

`annotation` is append-only and holds every pass's opinion. `ground_truth`
joins `adjudication`, which holds exactly one final label per observation. This
is the tool that fills it; without it every report in evaluate.py stays empty
no matter how much labelling gets done.

    python adjudicate.py --auto              # agree -> unanimous, in bulk
    python adjudicate.py --next              # show the next disagreement
    python adjudicate.py --resolve 4127 CMAP_INVALID --note "..."
    python adjudicate.py --progress

Nothing here edits `annotation`. Agreement can only be computed from labels as
originally given, so the original opinions survive adjudication unchanged --
that is the point of the table being append-only.
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timezone

import config
from annotate import LABELS
from phase0_schema import GUIDELINE_VERSION


def verdict(labels):
    """
    What to do with one observation's round-1 labels.

    Returns (basis, label). `single` is deliberately not treated as agreement:
    one pass is an opinion, not a reconciliation, and docs/phase0-schema.md
    §5.3 bars those rows from any agreement calculation.
    """
    distinct = set(labels)
    if not labels:
        return None, None
    if len(labels) == 1:
        return "single", labels[0]
    if len(distinct) == 1:
        return "unanimous", labels[0]
    return "disagreed", None


def _by_observation(conn, sample_id):
    """{obs_id: [(annotator, label, confidence, note)]} for round 1."""
    rows = conn.execute("""
        SELECT s.obs_id, a.annotator, a.label, a.confidence, a.note
        FROM annotation_sample s
        JOIN annotation a ON a.obs_id = s.obs_id AND a.round = 1
        WHERE s.sample_id = ?
        ORDER BY s.obs_id, a.annotator""", (sample_id,)).fetchall()
    out = {}
    for obs_id, annotator, label, confidence, note in rows:
        out.setdefault(obs_id, []).append((annotator, label, confidence, note))
    return out


def _write(conn, obs_id, label, basis, adjudicator, note):
    conn.execute(
        "INSERT OR REPLACE INTO adjudication (obs_id, final_label, basis, "
        " adjudicator, note, guideline_version, decided_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (obs_id, label, basis, adjudicator, note, GUIDELINE_VERSION,
         datetime.now(timezone.utc).isoformat(timespec="seconds")))


def auto(conn, sample_id, include_single=False):
    """Write the uncontested cases; leave every disagreement for a human."""
    # Settled means reconciled. A basis='single' row is provisional -- one
    # opinion recorded because no second one existed yet -- so it must be
    # revisited when a second pass arrives. Treating it as done would let a
    # premature --single freeze the table and silently discard every label the
    # second annotator goes on to write.
    done = {r[0] for r in conn.execute(
        "SELECT obs_id FROM adjudication WHERE basis != 'single'")}
    counts = {"unanimous": 0, "single": 0, "disagreed": 0, "skipped": 0}
    for obs_id, opinions in _by_observation(conn, sample_id).items():
        if obs_id in done:
            counts["skipped"] += 1
            continue
        basis, label = verdict([lab for _, lab, _, _ in opinions])
        if basis == "unanimous" or (basis == "single" and include_single):
            _write(conn, obs_id, label, basis, "auto", None)
        elif basis == "disagreed":
            # A provisional row must not outlive the agreement it assumed.
            conn.execute(
                "DELETE FROM adjudication WHERE obs_id=? AND basis='single'",
                (obs_id,))
        counts[basis] = counts.get(basis, 0) + 1
    conn.commit()

    print(f"unanimous  {counts['unanimous']:5}  written")
    print(f"single     {counts['single']:5}  "
          f"{'written' if include_single else 'left alone (pass --single)'}")
    print(f"disagreed  {counts['disagreed']:5}  need --next")
    if counts["skipped"]:
        print(f"already adjudicated {counts['skipped']}, left alone")


def next_case(conn, sample_id, show_name=False):
    """Show one disagreement, with the excerpts and both opinions."""
    done = {r[0] for r in conn.execute("SELECT obs_id FROM adjudication")}
    for obs_id, opinions in _by_observation(conn, sample_id).items():
        if obs_id in done:
            continue
        basis, _ = verdict([lab for _, lab, _, _ in opinions])
        if basis != "disagreed":
            continue

        print("=" * 68)
        print(f"obs {obs_id}")
        print("=" * 68)
        for page, kind, text in conn.execute(
                "SELECT page, kind, text FROM excerpt WHERE obs_id=? "
                "ORDER BY CASE kind WHEN 'head' THEN 0 WHEN 'violation' THEN 1"
                " ELSE 2 END, char_start", (obs_id,)):
            print(f"\n--- {kind}, page {page} ---")
            print(text[:600])
        print("\n" + "-" * 68)
        for annotator, label, confidence, note in opinions:
            print(f"  {annotator:24} {label:20} conf {confidence or '-'}"
                  f"{'  ' + note if note else ''}")
        if show_name:
            # Allowed here and nowhere else: the label is already given, so
            # the name can inform the tie-break without anchoring a first pass.
            name = conn.execute("SELECT font_name FROM font_observation "
                                "WHERE obs_id=?", (obs_id,)).fetchone()[0]
            print(f"\n  font name (revealed for adjudication): {name}")
        print(f"\npython adjudicate.py --resolve {obs_id} <LABEL> "
              f"--note \"why\"")
        return obs_id

    print(f"no unresolved disagreements in '{sample_id}'")
    return None


def resolve(conn, obs_id, label, note, adjudicator):
    if label not in LABELS:
        sys.exit(f"unknown label {label!r}; one of {', '.join(LABELS)}")
    if not conn.execute("SELECT 1 FROM annotation WHERE obs_id=?",
                        (obs_id,)).fetchone():
        sys.exit(f"obs {obs_id} has no annotations to adjudicate")
    if not note:
        # The reason is the whole value of an adjudicated row: without it the
        # next reader cannot tell a considered tie-break from a coin flip.
        sys.exit("--note is required when resolving a disagreement")
    _write(conn, obs_id, label, "adjudicated", adjudicator, note)
    conn.commit()
    print(f"obs {obs_id} -> {label} (adjudicated by {adjudicator})")


def progress(conn, sample_id):
    total = conn.execute(
        "SELECT COUNT(*) FROM annotation_sample WHERE sample_id=?",
        (sample_id,)).fetchone()[0]
    opinions = _by_observation(conn, sample_id)
    pending = {"unanimous": 0, "single": 0, "disagreed": 0}
    for obs in opinions.values():
        basis, _ = verdict([lab for _, lab, _, _ in obs])
        pending[basis] = pending.get(basis, 0) + 1

    rows = conn.execute("""
        SELECT adj.basis, COUNT(*) FROM adjudication adj
        JOIN annotation_sample s ON s.obs_id = adj.obs_id
        WHERE s.sample_id = ? GROUP BY adj.basis""", (sample_id,)).fetchall()
    settled = sum(n for _, n in rows)

    print(f"sample '{sample_id}': {total} observations")
    print(f"  annotated (any pass) {len(opinions):5}")
    for basis, n in pending.items():
        print(f"    {basis:20} {n:5}")
    print(f"  adjudicated          {settled:5}")
    for basis, n in rows:
        print(f"    {basis:20} {n:5}")
    if settled == total and total:
        print("\nground_truth is complete -- evaluate.py has its input")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--sample-id", default="gt-v1")
    ap.add_argument("--adjudicator", default="hardik")
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--single", action="store_true",
                    help="with --auto, also settle single-pass observations")
    ap.add_argument("--next", action="store_true")
    ap.add_argument("--show-name", action="store_true")
    ap.add_argument("--resolve", nargs=2, metavar=("OBS_ID", "LABEL"))
    ap.add_argument("--note", default=None)
    ap.add_argument("--progress", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(config.MANIFEST_DB)
    if args.auto:
        auto(conn, args.sample_id, include_single=args.single)
    elif args.next:
        next_case(conn, args.sample_id, show_name=args.show_name)
    elif args.resolve:
        resolve(conn, int(args.resolve[0]), args.resolve[1], args.note,
                args.adjudicator)
    elif args.progress:
        progress(conn, args.sample_id)
    else:
        sys.exit("pass one of --auto, --next, --resolve, --progress")


if __name__ == "__main__":
    main()
