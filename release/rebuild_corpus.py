#!/usr/bin/env python3
"""
rebuild_corpus.py — reconstruct the corpus from the released manifest.

Reads manifest.csv, re-fetches each document from its recorded source URL,
and verifies the SHA-256. Files that no longer match are reported rather than
silently accepted, so a partial reproduction is visible instead of quiet.

    python rebuild_corpus.py --out ./corpus

Please keep the delay: these are public servers run by municipal bodies.
"""
import argparse, csv, hashlib, time
from pathlib import Path
import requests

UA = "DevAudit-rebuild/0.1 (academic research)"
DELAY = 2.0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="manifest.csv")
    ap.add_argument("--out", default="./corpus")
    args = ap.parse_args()

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    s = requests.Session(); s.headers["User-Agent"] = UA
    ok = mismatch = missing = 0

    with open(args.manifest, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    for i, row in enumerate(rows, 1):
        dest = out / (row["sha256"] + ".pdf")
        if dest.exists():
            ok += 1
            continue
        time.sleep(DELAY)
        try:
            r = s.get(row["source_url"], timeout=60)
        except Exception:
            missing += 1
            print(f"  [{i}/{len(rows)}] unreachable  {row['source_url'][:70]}")
            continue
        if r.status_code != 200:
            missing += 1
            print(f"  [{i}/{len(rows)}] HTTP {r.status_code}  {row['source_url'][:70]}")
            continue
        digest = hashlib.sha256(r.content).hexdigest()
        if digest != row["sha256"]:
            mismatch += 1
            print(f"  [{i}/{len(rows)}] CHANGED since collection  {row['filename'][:50]}")
            continue
        dest.write_bytes(r.content)
        ok += 1

    print(f"\nverified {ok}, changed {mismatch}, unreachable {missing}, of {len(rows)}")
    if mismatch or missing:
        print("Partial reproduction. Documents move and change on these sites;")
        print("report the counts above alongside any figures you derive.")

if __name__ == "__main__":
    main()
