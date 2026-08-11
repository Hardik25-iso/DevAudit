#!/usr/bin/env python3
"""
resample_pmc.py — replace PMC's biased sample with a fresh unbiased draw.

Why this exists:
  PMC's documents come from a Drupal JSON:API that returns files in upload
  order. Discovery originally capped the walk at 25 pages, so the pool it
  sampled from was one slice of time rather than PMC's document set. Sampling
  randomly from a truncated pool still produces a biased estimate, because the
  bias lives in the pool, not the draw. PMC consequently reported 0% legacy
  while the document that motivated this whole project is a PMC budget set in
  Shree-Dev.

What it does:
  1. Drops PMC rows from `documents` and `audit`, and resets its `discovered`
     rows to pending, so the next draw comes from the complete pool.
  2. Leaves downloaded files on disk. They are content-addressed by SHA-256,
     so anything the new draw happens to reselect is reused rather than
     re-fetched from a public server.

It does NOT re-run discovery or collection - run those separately so each step
is inspectable:

    python collect.py --dry-run --source pmc     # exhaust the API
    python resample_pmc.py                       # this script
    python collect.py --source pmc --per-source 60
    python audit_corpus.py
"""

import argparse
import sqlite3

import config


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="pmc")
    ap.add_argument("--yes", action="store_true",
                    help="skip the confirmation prompt")
    args = ap.parse_args()

    conn = sqlite3.connect(config.MANIFEST_DB)
    key = args.source

    n_docs = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE source_key=?", (key,)).fetchone()[0]
    n_disc = conn.execute(
        "SELECT COUNT(*) FROM discovered WHERE source_key=?", (key,)).fetchone()[0]
    n_pending = conn.execute(
        "SELECT COUNT(*) FROM discovered WHERE source_key=? AND status='pending'",
        (key,)).fetchone()[0]

    print(f"source            : {key}")
    print(f"discovered urls   : {n_disc}  ({n_pending} still pending)")
    print(f"sampled documents : {n_docs}  <- these manifest rows will be dropped")
    print("PDF files on disk are kept; content addressing means a redraw reuses them.")

    if not args.yes:
        print("\nRe-run with --yes to apply.")
        return

    # Audit rows are keyed by sha256, so clear them via the documents they
    # belong to before the documents themselves go.
    conn.execute(
        "DELETE FROM audit WHERE sha256 IN "
        "(SELECT sha256 FROM documents WHERE source_key=?)", (key,))
    conn.execute("DELETE FROM documents WHERE source_key=?", (key,))
    conn.execute(
        "UPDATE discovered SET status='pending' WHERE source_key=?", (key,))
    conn.commit()

    left = conn.execute(
        "SELECT COUNT(*) FROM discovered WHERE source_key=? AND status='pending'",
        (key,)).fetchone()[0]
    print(f"\ncleared. {left} urls now pending for a fresh draw.")
    print("next: python collect.py --source pmc --per-source 60")


if __name__ == "__main__":
    main()
