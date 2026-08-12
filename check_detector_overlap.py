#!/usr/bin/env python3
"""
check_detector_overlap.py — is any detector carrying its own weight?

The audit grew four legacy detectors in the order the problems were found:

  1. font name matched LEGACY_PATTERNS
  2. mojibake_signature      (whole document, 8-bit / Marathi)
  3. ascii_remap_signature   (whole document, Kruti Dev / Hindi)
  4. classify_font_output    (per font, all of the above, undiluted)

Detector 4 runs the same measurements as 2 and 3 on strictly better input, so
2 and 3 are plausibly dead weight — a fossil of build order rather than a
design. This script answers that empirically instead of by argument: for every
document, which detectors fire, and does any document depend on exactly one?

A detector that never uniquely convicts anything can be deleted.

    python check_detector_overlap.py [--limit N]
"""

import argparse
import collections
import sqlite3
import sys
from pathlib import Path

import config
import font_audit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    conn = sqlite3.connect(config.MANIFEST_DB)
    q = "SELECT stored_path FROM documents ORDER BY sha256"
    if args.limit:
        q += f" LIMIT {args.limit}"
    paths = [r[0] for r in conn.execute(q)]

    fires = collections.Counter()      # how often each detector fires
    unique = collections.Counter()     # how often it is the ONLY one firing
    combos = collections.Counter()
    n = 0

    for p in paths:
        path = Path(p)
        if not path.exists():
            continue
        row = font_audit.audit(path)
        if row.get("verdict") == "ERROR":
            continue
        n += 1

        active = set()
        if row.get("legacy_fonts"):
            active.add("name")
        if row.get("mojibake_signature"):
            active.add("doc-mojibake")
        if row.get("ascii_remap_signature"):
            active.add("doc-ascii")
        if row.get("n_legacy_by_output"):
            active.add("per-font")

        if not active:
            continue
        for d in active:
            fires[d] += 1
        if len(active) == 1:
            unique[next(iter(active))] += 1
        combos["+".join(sorted(active))] += 1

    print(f"documents examined: {n}\n")
    print(f"{'detector':16} {'fires':>7} {'unique':>8}   verdict")
    for d in ("name", "doc-mojibake", "doc-ascii", "per-font"):
        u = unique[d]
        note = "REDUNDANT - delete" if fires[d] and u == 0 else \
               ("unused" if not fires[d] else "carries its own weight")
        print(f"{d:16} {fires[d]:7} {u:8}   {note}")

    print("\nco-occurrence (which detectors fire together):")
    for combo, c in combos.most_common(12):
        print(f"  {combo:44} {c}")


if __name__ == "__main__":
    main()
