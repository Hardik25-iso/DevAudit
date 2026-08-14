#!/usr/bin/env python3
"""
extract_observations.py — populate the Phase 0 tables from the corpus.

One pass over the collected PDFs, writing one `font_observation` row per
(document, font) with every signal stored whether or not it fired, plus the
`excerpt` rows that let annotation happen with the external drive detached.

    python extract_observations.py --dry-run --limit 5   # read, write nothing
    python extract_observations.py                       # documents not yet done
    python extract_observations.py --redo                # all of them again
    python extract_observations.py --verify              # reconcile, read no PDFs

Needs the external drive attached, except for --verify.

Why this is a separate script rather than more of audit_corpus.py: the audit
answers "how many documents are affected", and its row is the document. This
answers "what did each font do", and its row is the font. Same measurements,
different grain, and mixing the two is what flattened the per-font results into
delimited strings in the first place.
"""

import argparse
import random
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import fitz  # PyMuPDF

import config
import font_audit as fa


# Bumped whenever the numbers this script produces would change. Stored on
# every row, so a mixed-version table is visible rather than silently mixed.
SIGNALS_VERSION = "phase0-0.1"

# Excerpt sizing. Long enough to read a sentence of Devanagari and judge it,
# short enough that the annotation set stays quotable research fragments
# rather than a redistribution of documents we have no licence to redistribute.
HEAD_CHARS = 240
WINDOW_CHARS = 200
N_RANDOM = 2
N_VIOLATION = 2
VIOLATION_PAD = 60

# Detector reason prefixes -> the label vocabulary in docs/phase0-schema.md.
REASON_TO_LABEL = {
    "8bit": "LEGACY_8BIT",
    "ascii-remap": "LEGACY_ASCII",
    "symbol-remap": "LEGACY_SYMBOL",
}


def aggregate_fonts(records):
    """
    Collapse per-xref font records into one record per font name.

    The audit table's `all_fonts` is written per xref, so a real row reads
    'Calibri|0;Calibri|0;Calibri|1;...' — the same face repeated with
    contradictory flags. The defect is a property of the face's output, so the
    name is the unit and the xrefs are counted.

    `has_tounicode` is 1 only when EVERY xref for the name carries one,
    because the conjunction that makes an unknown name suspicious is
    'embedded and missing a Unicode mapping'; one mapped copy does not make
    the unmapped copies safe.
    """
    per = {}
    for f in records:
        name = f.get("name")
        if not name:
            continue
        pages = set(f.get("pages") or ([f["first_page"]]
                                       if f.get("first_page") else []))
        cur = per.get(name)
        if cur is None:
            per[name] = {
                "font_name": name,
                "raw_font_name": f.get("raw_name") or "",
                "n_xrefs": 1,
                "embedded": int(bool(f.get("embedded"))),
                "has_tounicode": int(bool(f.get("has_tounicode"))),
                "encoding": f.get("encoding") or "",
                "font_type": f.get("type") or "",
                "_pages": pages,
            }
            continue
        cur["n_xrefs"] += 1
        cur["embedded"] = max(cur["embedded"], int(bool(f.get("embedded"))))
        cur["has_tounicode"] = min(cur["has_tounicode"],
                                   int(bool(f.get("has_tounicode"))))
        cur["_pages"] |= pages

    for rec in per.values():
        # Declared, not necessarily rendered: a font can sit in a page's
        # resource dictionary and draw nothing. Beside `sampled_chars` it is
        # still the cheapest available warning that a font was barely sampled.
        rec["n_pages_declared"] = len(rec["_pages"])
        rec["first_page"] = min(rec["_pages"]) if rec["_pages"] else None
        del rec["_pages"]
    return per


def detector_label(m, reason):
    """
    What the shipped detector says about one font, in the label vocabulary.

    `NO_EVIDENCE` rather than `CORRECT`, deliberately. The detector's silence
    has always meant "nothing fired", never "this is fine" — that distinction
    is the reason UNCLASSIFIED is a bucket instead of an error, and collapsing
    it here would let the instrument's documented failure mode disappear into
    a label that looks like a positive finding.

    The CMAP_INVALID branch is NEW and UNVALIDATED. It applies the existing
    document-level SUSPECT rule per font, which the shipped classifier never
    does — it returns None as soon as real Devanagari appears and defers to
    the document-level check. That is why the SUSPECT class has no per-font
    evidence today. This gives it some, and the whole point of Phase 2 is to
    find out whether the per-font thresholds should be these. It feeds no
    verdict: decide_verdict() is untouched and the Phase 1 figure does not
    move.
    """
    if reason:
        return REASON_TO_LABEL.get(reason.split("(", 1)[0], "UNDECIDABLE"), reason
    if m["sampled_chars"] < fa.PERFONT_MIN_CHARS:
        return "UNDECIDABLE", "too-little-text"
    if (m["dev_chars"] >= fa.MIN_DEV_CHARS
            and m["invalid_rate_per_1k"] >= fa.SUSPECT_RATE_PER_1K
            and m["invalid_matras"] >= fa.SUSPECT_MIN_ABS):
        return ("CMAP_INVALID",
                f"invalid-matras({m['invalid_rate_per_1k']:.1f}/1k)[unvalidated]")
    return "NO_EVIDENCE", ""


def choose_excerpts(text, chunks, seed_key):
    """
    Pick the text an annotator will actually read.

    Three kinds, and the middle one carries the argument. `random` windows are
    drawn with a seed derived from the document and font rather than from run
    order, so the excerpt set is reproducible and — more importantly — is not
    chosen by the detector. Show only what the detector found and the
    annotator can only ever confirm it.

    `violation` windows exist because the wrong-CMap class is invisible unless
    you are shown where it broke: a page of Devanagari that reads almost right
    hides its two impossible clusters from anyone skimming.
    """
    out = []
    if not text.strip():
        return out

    def add(kind, start, end):
        start = max(0, start)
        end = min(len(text), end)
        frag = text[start:end].strip()
        if frag:
            out.append({"kind": kind, "char_start": start, "text": frag,
                        "page": fa.page_of_offset(chunks, start)})

    add("head", 0, HEAD_CHARS)

    rng = random.Random(seed_key)
    if len(text) > HEAD_CHARS + WINDOW_CHARS:
        for _ in range(N_RANDOM):
            start = rng.randrange(HEAD_CHARS, len(text) - WINDOW_CHARS + 1)
            add("random", start, start + WINDOW_CHARS)

    for match in list(fa.INVALID_MATRA.finditer(text))[:N_VIOLATION]:
        add("violation", match.start() - VIOLATION_PAD,
            match.start() + VIOLATION_PAD)

    # Same (kind, page, char_start) twice would violate the table's UNIQUE
    # constraint; two random draws can collide on a short sample.
    seen, unique = set(), []
    for e in out:
        key = (e["kind"], e["page"], e["char_start"])
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


def observe(path, sha256, max_pages=fa.PERFONT_MAX_PAGES):
    """
    Extract every font observation from one PDF. Returns a list of rows.

    `max_pages=None` reads the whole document. That is expensive — a
    get_text("dict") per page is what the 8-page cap exists to avoid — so it
    is used only for the documents where the cap demonstrably starves a font.
    See --deep.
    """
    doc = fitz.open(path)
    try:
        if doc.needs_pass:
            return []
        inventory = aggregate_fonts(fa.collect_fonts(doc))
        spans = fa.collect_font_spans(
            doc, max_pages=doc.page_count if max_pages is None else max_pages)
    finally:
        doc.close()

    rows = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # Union of both views: a font can be declared in the page resources but
    # render nothing on the sampled pages, and a span can be tagged with a font
    # the resource walk missed. Dropping either side would silently shrink the
    # denominator, and a denominator that moves without being noticed is how a
    # rate ends up wrong.
    for name in sorted(set(inventory) | set(spans)):
        rec = spans.get(name, {"text": "", "pages": [], "chunks": []})
        text = rec["text"]
        m = fa.measure_font_text(text)
        reason = fa.classify_font_output(text, m)
        label, detail = detector_label(m, reason)
        meta = inventory.get(name, {})
        rows.append({
            "sha256": sha256,
            "font_name": name,
            "raw_font_name": meta.get("raw_font_name", ""),
            "n_xrefs": meta.get("n_xrefs", 0),
            "embedded": meta.get("embedded"),
            "has_tounicode": meta.get("has_tounicode"),
            "encoding": meta.get("encoding", ""),
            "font_type": meta.get("font_type", ""),
            "first_page": meta.get("first_page") or (
                rec["pages"][0] if rec["pages"] else None),
            "n_pages_seen": len(rec["pages"]),
            "n_pages_declared": meta.get("n_pages_declared", 0),
            "detector_label": label,
            "detector_reason": detail,
            # The cap is part of what produced these numbers, so it travels
            # with them. A table holding rows measured over 8 pages and rows
            # measured over 200 is fine; a table that cannot tell you which is
            # which is not.
            "signals_version": (SIGNALS_VERSION if max_pages is not None
                                else SIGNALS_VERSION + "+deep"),
            "extracted_at": now,
            "_excerpts": choose_excerpts(text, rec["chunks"],
                                         f"{sha256}:{name}"),
            **{k: m[k] for k in OBS_SIGNALS},
        })
    return rows


OBS_SIGNALS = [
    "sampled_chars", "dev_chars", "latin_letters", "n_tokens",
    "mojibake_ratio", "ascii_k_ratio", "ascii_k_eligible", "symbol_per_1k",
    "english_ratio", "invalid_matras", "invalid_rate_per_1k",
    "word_initial_matras", "adjacent_matras", "virama_then_matra",
    "detached_matras", "invalid_matras_nospace",
]

OBS_COLUMNS = [
    "sha256", "font_name", "raw_font_name", "n_xrefs", "embedded",
    "has_tounicode", "encoding", "font_type", "first_page", "n_pages_seen",
    "n_pages_declared",
] + OBS_SIGNALS + [
    "detector_label", "detector_reason", "signals_version", "extracted_at",
]


def write_rows(conn, rows):
    """Write one document's observations and their excerpts, replacing any
    previous extraction of the same document."""
    for r in rows:
        cols = ", ".join(OBS_COLUMNS)
        conn.execute(
            f"INSERT OR REPLACE INTO font_observation ({cols}) "
            f"VALUES ({', '.join('?' * len(OBS_COLUMNS))})",
            [int(r[c]) if isinstance(r[c], bool) else r[c]
             for c in OBS_COLUMNS])
        obs_id = conn.execute(
            "SELECT obs_id FROM font_observation WHERE sha256=? AND font_name=?",
            (r["sha256"], r["font_name"])).fetchone()[0]
        # Excerpts are rewritten wholesale: INSERT OR REPLACE alone would leave
        # excerpts from a previous run whose offsets no longer mean anything.
        conn.execute("DELETE FROM excerpt WHERE obs_id=?", (obs_id,))
        for e in r["_excerpts"]:
            conn.execute(
                "INSERT INTO excerpt (obs_id, page, kind, char_start, text) "
                "VALUES (?,?,?,?,?)",
                (obs_id, e["page"], e["kind"], e["char_start"], e["text"]))


def run(conn, redo=False, limit=None, dry_run=False, deep=False):
    q = ("SELECT d.sha256, d.stored_path FROM documents d "
         "JOIN audit a ON a.sha256 = d.sha256 "
         "WHERE a.verdict != 'ERROR'")
    if deep:
        # Only the documents where the 8-page cap demonstrably starves a font:
        # declared on 5+ pages, yet contributing too little text to judge. A
        # 40-document probe found 53% of these cross the floor once the whole
        # document is read, and 14% of those are then convicted by output.
        q += (" AND d.sha256 IN (SELECT DISTINCT sha256 FROM font_observation"
              "  WHERE sampled_chars < ? AND n_pages_declared >= 5)")
    elif not redo:
        q += (" AND d.sha256 NOT IN (SELECT DISTINCT sha256 FROM font_observation)")
    q += " ORDER BY d.sha256"
    if limit:
        q += f" LIMIT {int(limit)}"
    docs = conn.execute(q, (fa.PERFONT_MIN_CHARS,) if deep else ()).fetchall()
    if not docs:
        print("nothing to extract")
        return 0

    print(f"extracting from {len(docs)} documents"
          f"{' (dry run, nothing written)' if dry_run else ''}\n")
    n_obs = missing = failed = 0
    for i, (sha, path) in enumerate(docs, 1):
        p = Path(path)
        if not p.exists():
            missing += 1
            continue
        try:
            rows = observe(p, sha, max_pages=None if deep else fa.PERFONT_MAX_PAGES)
        except Exception as e:
            failed += 1
            print(f"  ! {sha[:12]} {type(e).__name__}: {e}"[:100])
            continue
        n_obs += len(rows)
        if not dry_run:
            write_rows(conn, rows)
        if i % 25 == 0:
            if not dry_run:
                conn.commit()
            print(f"  {i}/{len(docs)}  {n_obs} observations")
    if not dry_run:
        conn.commit()

    print(f"\n{n_obs} observations from {len(docs) - missing - failed} documents")
    if missing:
        # Same failure mode audit_corpus.py guards: silence here would look
        # like a clean run over a corpus that was not there.
        print(f"{missing} files missing — is the external drive attached?")
    if failed:
        print(f"{failed} documents failed to open")
    return n_obs


def verify(conn):
    """
    Reconcile per-font extraction against the per-document audit. Reads no PDFs.

    The check that matters: the number of fonts convicted by output per
    document must equal the audit's `n_legacy_by_output`. If the two disagree,
    the extractor and the auditor are not measuring the same thing, and no
    label written against these rows would mean anything.
    """
    total, docs = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT sha256) FROM font_observation"
    ).fetchone()
    if not total:
        print("no observations extracted yet")
        return 1

    print(f"{total} observations across {docs} documents")
    print("\ndetector label distribution")
    for label, n in conn.execute(
            "SELECT detector_label, COUNT(*) FROM font_observation "
            "GROUP BY detector_label ORDER BY COUNT(*) DESC"):
        print(f"  {label:20} {n:6}  ({100.0 * n / total:5.1f}%)")

    # Only the three output-convicted labels correspond to n_legacy_by_output;
    # CMAP_INVALID is the new per-font rule and has no counterpart in `audit`.
    #
    # --deep rows are excluded on purpose. They are measured over every page
    # while the audit measured eight, so finding MORE convicted fonts is the
    # intended result rather than a disagreement. Counting them as mismatches
    # would train the check to be ignored, which is worse than not having it.
    counts = conn.execute("""
        SELECT MAX(o.signals_version LIKE '%+deep') AS is_deep,
               a.sha256, a.n_legacy_by_output,
               SUM(o.detector_label IN
                   ('LEGACY_8BIT','LEGACY_ASCII','LEGACY_SYMBOL'))
        FROM audit a
        JOIN font_observation o ON o.sha256 = a.sha256
        GROUP BY a.sha256
    """).fetchall()
    mismatches = [(s, want, got) for deep, s, want, got in counts
                  if not deep and got != want]
    extra = [(s, want, got) for deep, s, want, got in counts
             if deep and got > want]
    shortfall = [(s, want, got) for deep, s, want, got in counts
                 if deep and got < want]

    print(f"\nreconciliation against audit.n_legacy_by_output: "
          f"{len(mismatches)} mismatch(es)")
    for sha, expected, got in mismatches[:10]:
        print(f"  {sha[:16]}  audit={expected}  observations={got}")
    if mismatches:
        print("\nThe extractor and the auditor disagree. Resolve before "
              "annotating — labels written against these rows would be "
              "measuring something the audit does not.")
    if extra:
        print(f"\n{len(extra)} deep-extracted documents convict more fonts "
              f"than the 8-page audit did (+{sum(g - w for _, w, g in extra)} "
              f"fonts). Expected: the cap was hiding them.")
    if shortfall:
        # Deep reads strictly more text, so finding FEWER convictions means a
        # font crossed a threshold in the wrong direction — worth a look.
        print(f"{len(shortfall)} deep documents convict FEWER fonts than the "
              f"audit did — investigate, deep should be a superset.")
    return len(mismatches) + len(shortfall)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--redo", action="store_true",
                    help="re-extract documents already observed")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="read PDFs, write nothing")
    ap.add_argument("--verify", action="store_true",
                    help="reconcile against the audit table, read no PDFs")
    ap.add_argument("--deep", action="store_true",
                    help="re-extract only the documents where the page cap "
                         "starves a font, reading every page of them")
    args = ap.parse_args()

    conn = sqlite3.connect(config.MANIFEST_DB)
    if args.verify:
        sys.exit(1 if verify(conn) else 0)
    run(conn, redo=args.redo, limit=args.limit, dry_run=args.dry_run,
        deep=args.deep)
    verify(conn)


if __name__ == "__main__":
    main()
