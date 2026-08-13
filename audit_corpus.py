#!/usr/bin/env python3
"""
audit_corpus.py — run the font audit over the collected corpus and write the
results back into manifest.sqlite, joined to provenance.

The point of keeping audit results in the same database as provenance is
traceability: every percentage this prints can be followed back to a SHA-256,
a source URL, and a retrieval timestamp. A statistic you cannot trace to the
file it came from is not evidence.

Usage:
    python audit_corpus.py                 # audit anything not yet audited
    python audit_corpus.py --reaudit       # re-run over everything
    python audit_corpus.py --report        # print breakdowns, audit nothing
    python audit_corpus.py --rebucket      # re-apply thresholds, read no PDFs
"""

import argparse
import collections
import sqlite3
import sys
from pathlib import Path

import config
import font_audit


AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    sha256              TEXT PRIMARY KEY,
    verdict             TEXT,
    pages               INTEGER,
    n_fonts             INTEGER,
    fonts_no_unicode    INTEGER,
    legacy_fonts        TEXT,
    unknown_fonts       TEXT,
    chars               INTEGER,
    dev_chars           INTEGER,
    invalid_matras      INTEGER,
    invalid_rate_per_1k REAL,
    detached_matras     INTEGER,
    producer            TEXT,
    creator             TEXT,
    legacy_by_output    TEXT,
    n_legacy_by_output  INTEGER,
    all_fonts           TEXT,
    audited_at          TEXT,
    error               TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_verdict ON audit(verdict);
"""

# Columns added after the table already existed in the wild. CREATE TABLE IF
# NOT EXISTS will not add them to an existing database, so they are applied
# explicitly. Without this the per-font detector's evidence is lost: the
# verdict gets stored but not the reason for it, which defeats the point of
# keeping audit results beside provenance.
MIGRATIONS = [
    ("legacy_by_output", "TEXT"),
    ("n_legacy_by_output", "INTEGER"),
    ("all_fonts", "TEXT"),
]

AUDIT_FIELDS = [
    "verdict", "pages", "n_fonts", "fonts_no_unicode", "legacy_fonts",
    "unknown_fonts", "chars", "dev_chars", "invalid_matras",
    "invalid_rate_per_1k", "detached_matras", "producer", "creator",
    "legacy_by_output", "n_legacy_by_output", "all_fonts",
]


def open_db():
    conn = sqlite3.connect(config.MANIFEST_DB)
    conn.executescript(AUDIT_SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(audit)")}
    for col, coltype in MIGRATIONS:
        if col not in have:
            conn.execute(f"ALTER TABLE audit ADD COLUMN {col} {coltype}")
    conn.commit()
    return conn


def run_audit(conn, reaudit=False):
    q = "SELECT sha256, stored_path FROM documents"
    if not reaudit:
        # Retry previous failures as well as new documents. Errors here are
        # usually transient - detached external storage, a locked file - and a
        # document that failed once must not be excluded from the corpus
        # permanently, because that silently biases the sample toward whatever
        # was readable at the time.
        q += (" WHERE sha256 NOT IN "
              "(SELECT sha256 FROM audit WHERE verdict != 'ERROR')")
    rows = conn.execute(q).fetchall()
    if not rows:
        print("nothing new to audit")
        return 0

    print(f"auditing {len(rows)} documents\n")
    from datetime import datetime, timezone
    done = 0
    for i, (sha, path) in enumerate(rows, 1):
        p = Path(path)
        if not p.exists():
            # The file is on the external drive; if it is unplugged we want a
            # clear message rather than every document silently erroring.
            conn.execute(
                "INSERT OR REPLACE INTO audit (sha256, verdict, error, audited_at)"
                " VALUES (?,?,?,?)",
                (sha, "ERROR", "file missing (is the drive attached?)",
                 datetime.now(timezone.utc).isoformat(timespec="seconds")))
            continue
        r = font_audit.audit(p)
        vals = [r.get(k) for k in AUDIT_FIELDS]
        conn.execute(
            f"INSERT OR REPLACE INTO audit "
            f"(sha256, {', '.join(AUDIT_FIELDS)}, audited_at, error) "
            f"VALUES (?{', ?' * len(AUDIT_FIELDS)}, ?, ?)",
            [sha] + vals
            + [datetime.now(timezone.utc).isoformat(timespec="seconds"),
               r.get("error")])
        done += 1
        if i % 25 == 0:
            conn.commit()
            print(f"  {i}/{len(rows)}")
    conn.commit()
    print(f"\naudited {done} documents")
    return done


def rebucket(conn):
    """
    Recompute verdicts from stored measurements, touching no PDFs.

    Threshold and pattern changes used to require re-opening every file to
    recompute numbers that had not changed -- slow, and dependent on the
    external drive being attached. Everything the bucket decision needs is
    already in the audit table, and `all_fonts` preserves the font names so
    a new LEGACY_PATTERNS entry can be re-applied too.

    Turns "re-audit 1,602 documents" into a query. Use it after editing a
    threshold or a font list; use --reaudit only when the *extraction* logic
    itself changes, since that genuinely needs the files.
    """
    cols = ("sha256, verdict, pages, n_fonts, chars, invalid_matras, "
            "invalid_rate_per_1k, legacy_fonts, unknown_fonts, "
            "n_legacy_by_output, all_fonts")
    rows = conn.execute(
        f"SELECT {cols} FROM audit WHERE verdict != 'ERROR'").fetchall()
    if not rows:
        print("nothing to rebucket")
        return 0

    keys = [c.strip() for c in cols.split(",")]
    changed = []
    for values in rows:
        row = dict(zip(keys, values))
        # Re-apply the current pattern lists where font names were recorded.
        # Rows audited before all_fonts existed keep their stored matches.
        if row.get("all_fonts"):
            legacy, unknown = font_audit.rematch_fonts(row["all_fonts"])
            row["legacy_fonts"], row["unknown_fonts"] = legacy, unknown
        new = font_audit.decide_verdict(row)
        if new != row["verdict"]:
            changed.append((row["sha256"], row["verdict"], new))
        conn.execute(
            "UPDATE audit SET verdict=?, legacy_fonts=?, unknown_fonts=? "
            "WHERE sha256=?",
            (new, row["legacy_fonts"], row["unknown_fonts"], row["sha256"]))
    conn.commit()

    print(f"rebucketed {len(rows)} documents from stored signals, "
          f"{len(changed)} verdict change(s)")
    moves = collections.Counter(f"{o} -> {n}" for _, o, n in changed)
    for move, c in moves.most_common(10):
        print(f"    {move:34} {c}")
    return len(changed)


def _pct_table(conn, group_col, label, min_n=1):
    """Print bucket percentages broken down by one provenance column."""
    rows = conn.execute(f"""
        SELECT d.{group_col} AS grp,
               COUNT(*) AS n,
               SUM(a.verdict='SCAN')         AS scan,
               SUM(a.verdict='LEGACY')       AS legacy,
               SUM(a.verdict='SUSPECT')      AS suspect,
               SUM(a.verdict='UNCLASSIFIED') AS unclass,
               SUM(a.verdict='CLEAN')        AS clean
        FROM documents d JOIN audit a ON a.sha256 = d.sha256
        GROUP BY d.{group_col}
        HAVING n >= ?
        ORDER BY n DESC
    """, (min_n,)).fetchall()
    if not rows:
        return
    print(f"\n--- by {label} ---")
    print(f"{'':38} {'n':>4} {'scan':>6} {'legacy':>7} {'susp':>6} "
          f"{'uncl':>6} {'clean':>6}")
    for grp, n, scan, legacy, susp, unc, clean in rows:
        name = (grp or "(unknown)")[:36]
        print(f"{name:38} {n:4} "
              f"{100*scan/n:5.0f}% {100*legacy/n:6.0f}% {100*susp/n:5.0f}% "
              f"{100*unc/n:5.0f}% {100*clean/n:5.0f}%")


def report(conn):
    total = conn.execute(
        "SELECT COUNT(*) FROM documents d JOIN audit a ON a.sha256=d.sha256"
    ).fetchone()[0]
    if not total:
        print("no audited documents yet")
        return

    bodies = conn.execute(
        "SELECT COUNT(DISTINCT issuing_body) FROM documents d "
        "JOIN audit a ON a.sha256=d.sha256").fetchone()[0]

    counts = dict(conn.execute(
        "SELECT a.verdict, COUNT(*) FROM documents d "
        "JOIN audit a ON a.sha256=d.sha256 GROUP BY a.verdict").fetchall())

    print("=" * 70)
    print(f"{total} documents, {bodies} issuing bodies")
    for b, label in [("SCAN", "no text layer (scan)"),
                     ("LEGACY", "legacy non-Unicode fonts"),
                     ("SUSPECT", "suspect (invalid Devanagari)"),
                     ("UNCLASSIFIED", "unclassified font"),
                     ("CLEAN", "clean Unicode")]:
        c = counts.get(b, 0)
        print(f"  {label:32} {c:4}  ({100.0*c/total:5.1f}%)")
    if counts.get("ERROR"):
        print(f"  {'errors':32} {counts['ERROR']:4}")
    print("=" * 70)

    ls = 100.0 * (counts.get("LEGACY", 0) + counts.get("SUSPECT", 0)) / total
    un = 100.0 * counts.get("UNCLASSIFIED", 0) / total
    print(f"LEGACY+SUSPECT = {ls:.1f}%   UNCLASSIFIED = {un:.1f}%")

    # Pooling every document treats the corpus as one population, which it is
    # not: repeated collection passes left the bodies very unequally sized, and
    # the largest happens to be among the worst affected. That lets one portal
    # steer the headline. The macro average weights each issuing body equally,
    # answering "how bad is a typical body" rather than "how bad is a typical
    # document in a sample we did not balance". Report both; if they disagree,
    # the pooled figure is the one to distrust.
    per_body = conn.execute("""
        SELECT d.issuing_body, COUNT(*),
               SUM(a.verdict IN ('LEGACY','SUSPECT'))
        FROM documents d JOIN audit a ON a.sha256 = d.sha256
        GROUP BY d.issuing_body""").fetchall()
    if len(per_body) > 1:
        rates = [100.0 * bad / n for _, n, bad in per_body if n]
        macro = sum(rates) / len(rates)
        biggest = max(per_body, key=lambda r: r[1])
        print(f"  pooled (every document equal)     {ls:.1f}%")
        print(f"  macro  (every body equal, n={len(rates)})   {macro:.1f}%")
        print(f"  largest body is {100.0*biggest[1]/total:.0f}% of the corpus "
              f"({biggest[0][:34]}, n={biggest[1]})")
    if ls >= 15 or (ls >= 5 and un >= 15):
        print("DECISION: GO - the corrupted-text-layer problem is real.")
    elif ls + un < 10:
        print("DECISION: PIVOT - not prevalent enough to build on.")
    else:
        print("DECISION: INSPECT - hand-check 30 UNCLASSIFIED, or if too few "
              "exist, 30 drawn from LEGACY+SUSPECT.")

    _pct_table(conn, "issuing_body", "issuing body")
    _pct_table(conn, "doc_type", "document type")

    # Distinct legacy fonts observed. The size of this list is itself a
    # finding: it measures how incomplete the starting font list was.
    fonts = set()
    for (s,) in conn.execute(
            "SELECT legacy_fonts FROM audit WHERE legacy_fonts != ''"):
        fonts.update(x for x in s.split(";") if x)
    unknown = set()
    for (s,) in conn.execute(
            "SELECT unknown_fonts FROM audit WHERE unknown_fonts != ''"):
        unknown.update(x for x in s.split(";") if x)

    print(f"\n--- {len(fonts)} distinct legacy font variants observed ---")
    for f in sorted(fonts):
        print(f"    {f}")
    if unknown:
        print(f"\n--- {len(unknown)} unidentified fonts (need classification) ---")
        for f in sorted(unknown):
            print(f"    {f}")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--reaudit", action="store_true",
                    help="re-audit documents already in the audit table")
    ap.add_argument("--report", action="store_true",
                    help="print breakdowns only, audit nothing")
    ap.add_argument("--rebucket", action="store_true",
                    help="recompute verdicts from stored signals, no PDFs "
                         "read; use after a threshold or font-list change")
    args = ap.parse_args()

    conn = open_db()
    if args.rebucket:
        rebucket(conn)
    elif not args.report:
        run_audit(conn, reaudit=args.reaudit)
    print()
    report(conn)


if __name__ == "__main__":
    main()
