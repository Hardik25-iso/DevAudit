#!/usr/bin/env python3
"""
evaluate_extractors.py — the four Phase 3 reports (docs/phase3-design.md §3).

    python evaluate_extractors.py --run labelled-20260819 --loss
    python evaluate_extractors.py --run all-20260819 --concordance
    python evaluate_extractors.py --run labelled-20260819 --structural
    python evaluate_extractors.py --run labelled-20260819 --agreement
    python evaluate_extractors.py --run labelled-20260819 --composite
    python evaluate_extractors.py --run labelled-20260819 --sweep replacement

Every report prints the macro average by issuing body beside the pooled figure.
Sampling has produced a wrong answer three times in this project and twice the
tell was the two figures disagreeing, so a report that shows only one of them
is a report that can hide the mistake.

Every report degrades cleanly on empty state, so any of them can be run at any
point during a pass.
"""

import argparse
import sqlite3
from collections import defaultdict

import config
import extractors as ex
import font_audit as fa

# §3.2. The asymmetry is deliberate: the failure this project exists to measure
# is a page that RENDERS Devanagari whose text layer does not, so the mismatch
# rule is the one with the tight bound. script_excess is the reverse and is
# expected to be rare -- it is measured anyway, because a check that can only
# fire in the direction you expect is not a check.
OCR_DEVA_PRESENT = 0.30
ARM_DEVA_ABSENT = 0.10

# §3.3. Phase 1's threshold, unchanged. It survived Phase 2 evaluation at
# 0.943 / 0.953 and sits inside a plateau rather than on an edge, so it enters
# this phase as a known quantity rather than a new guess.
INVALID_MAX = 2.0

ARM_ORDER = ["pdftotext", "pymupdf", "pdfplumber", "pypdf", "ocr"]


def connect(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def load(conn, run_id):
    rows = conn.execute("""
        SELECT e.*, COALESCE(s.issuing_body, 'unknown') AS body
        FROM extraction e
        LEFT JOIN extraction_sample s
               ON s.run_id = e.run_id AND s.sha256 = e.sha256
        WHERE e.run_id = ? AND e.page > 0
    """, (run_id,)).fetchall()
    return rows


def arms_present(rows):
    return [a for a in ARM_ORDER if any(r["arm"] == a for r in rows)] + \
           sorted({r["arm"] for r in rows} - set(ARM_ORDER))


def macro_pooled(per_body_num, per_body_den):
    """
    Returns (macro, pooled). Macro weights each body equally; pooled weights
    each page equally. CLAUDE.md: report both, distrust pooled when they
    disagree -- the corpus is 152 Nashik documents against 5 from Pune MC.
    """
    bodies = [b for b in per_body_den if per_body_den[b]]
    if not bodies:
        return None, None
    macro = sum(per_body_num[b] / per_body_den[b] for b in bodies) / len(bodies)
    pooled = sum(per_body_num.values()) / sum(per_body_den[b] for b in bodies)
    return macro, pooled


def fmt(x, w=7):
    return f"{'--':>{w}}" if x is None else f"{x:>{w}.3f}"


# ---------------------------------------------------------------------------
# §3.1 loss
# ---------------------------------------------------------------------------

def report_loss(rows):
    print("\n=== §3.1 loss — is there text, and is it made of real characters?")
    print("Everything below this report is a rate, and a rate flatters an empty")
    print("numerator. This is why loss is measured first and gates the rest.\n")
    print(f"{'arm':12}{'pages':>7}{'chars/pg':>10}{'empty':>8}{'U+FFFD':>8}"
          f"{'ctrl':>8}{'fail%':>8}{'macro':>8}{'errors':>8}")

    for arm in arms_present(rows):
        a = [r for r in rows if r["arm"] == arm]
        n = len(a)
        if not n:
            continue
        num, den = defaultdict(int), defaultdict(int)
        fails = empty = 0
        chars = repl = ctrl = 0
        for r in a:
            v = ex.loss_verdict(r)
            den[r["body"]] += 1
            if v:
                fails += 1
                num[r["body"]] += 1
                if v.startswith("empty"):
                    empty += 1
            chars += r["n_chars"] or 0
            repl += r["replacement_chars"] or 0
            ctrl += r["control_chars"] or 0
        macro, _ = macro_pooled(num, den)
        errs = sum(1 for r in rows if r["arm"] == arm and r["error"])
        tot = max(chars, 1)
        print(f"{arm:12}{n:>7}{chars/n:>10.0f}{empty/n:>8.3f}{repl/tot:>8.3f}"
              f"{ctrl/tot:>8.3f}{fails/n:>8.3f}{fmt(macro, 8)}{errs:>8}")

    print(f"\ngate: n_chars < {ex.LOSS_MIN_CHARS} | replacement >= "
          f"{ex.LOSS_MAX_REPLACEMENT} | control >= {ex.LOSS_MAX_CONTROL}")


# ---------------------------------------------------------------------------
# §3.2 script concordance
# ---------------------------------------------------------------------------

def classify_concordance(arm_row, ocr_row):
    if ex.loss_verdict(arm_row):
        return "loss"
    if ocr_row is None or ex.loss_verdict(ocr_row):
        return "no_reference"
    od, ad = ocr_row["dev_share"] or 0.0, arm_row["dev_share"] or 0.0
    if od >= OCR_DEVA_PRESENT and ad < ARM_DEVA_ABSENT:
        return "script_mismatch"
    if od < ARM_DEVA_ABSENT and ad >= OCR_DEVA_PRESENT:
        return "script_excess"
    return "match"


def build_pairs(conn, rows, run_id):
    ocr = {(r["sha256"], r["page"]): r for r in rows if r["arm"] == "ocr"}
    out = []
    for r in rows:
        if r["arm"] == "ocr":
            continue
        o = ocr.get((r["sha256"], r["page"]))
        out.append((run_id, r["sha256"], r["page"], r["arm"],
                    r["dev_share"], o["dev_share"] if o else None,
                    classify_concordance(r, o), run_id))
    conn.execute("DELETE FROM page_pair WHERE run_id = ?", (run_id,))
    conn.executemany("""INSERT OR REPLACE INTO page_pair
        (run_id, sha256, page, arm, dev_share_arm, dev_share_ocr,
         concordance, built_from_run) VALUES (?,?,?,?,?,?,?,?)""", out)
    conn.commit()
    return out


def report_concordance(conn, rows, run_id):
    print("\n=== §3.2 script concordance — is the output even the right script?")
    if not any(r["arm"] == "ocr" for r in rows):
        print("no OCR arm in this run — script concordance needs the reference.")
        print("rerun with --arms all, or ignore this report for a text-only run.")
        return

    build_pairs(conn, rows, run_id)
    body_of = {r["sha256"]: r["body"] for r in rows}

    print("\nOCR is the reference only for SCRIPT, never for characters. The")
    print("question asked of it is 'what script is on this page', which survives")
    print("a large character error rate intact.\n")
    print(f"{'arm':12}{'scored':>8}{'match':>8}{'MISMATCH':>10}{'macro':>8}"
          f"{'excess':>8}{'no_ref':>8}{'loss':>7}")

    pairs = conn.execute(
        "SELECT * FROM page_pair WHERE run_id = ?", (run_id,)).fetchall()
    for arm in [a for a in arms_present(rows) if a != "ocr"]:
        a = [p for p in pairs if p["arm"] == arm]
        if not a:
            continue
        c = defaultdict(int)
        num, den = defaultdict(int), defaultdict(int)
        for p in a:
            c[p["concordance"]] += 1
            if p["concordance"] in ("match", "script_mismatch", "script_excess"):
                body = body_of.get(p["sha256"], "unknown")
                den[body] += 1
                if p["concordance"] == "script_mismatch":
                    num[body] += 1
        scored = c["match"] + c["script_mismatch"] + c["script_excess"]
        macro, _ = macro_pooled(num, den)
        s = max(scored, 1)
        print(f"{arm:12}{scored:>8}{c['match']/s:>8.3f}"
              f"{c['script_mismatch']/s:>10.3f}{fmt(macro, 8)}"
              f"{c['script_excess']/s:>8.3f}{c['no_reference']:>8}{c['loss']:>7}")

    print(f"\nmismatch: ocr dev_share >= {OCR_DEVA_PRESENT} and arm < {ARM_DEVA_ABSENT}")

    # The Phase 1 cross-tab. This is the report that says whether concordance
    # sees corruption the shipped detector does not.
    verdicts = dict(conn.execute("SELECT sha256, verdict FROM audit").fetchall())
    print("\n--- against the Phase 1 verdict (pymupdf arm) ---")
    print(f"{'phase 1 verdict':18}{'pages':>7}{'mismatch':>10}{'rate':>8}")
    grid = defaultdict(lambda: [0, 0])
    for p in pairs:
        if p["arm"] != "pymupdf" or p["concordance"] not in (
                "match", "script_mismatch", "script_excess"):
            continue
        g = grid[verdicts.get(p["sha256"], "?")]
        g[0] += 1
        g[1] += p["concordance"] == "script_mismatch"
    for v in ("CLEAN", "SUSPECT", "LEGACY", "UNCLASSIFIED", "SCAN", "?"):
        if v in grid:
            n, m = grid[v]
            flag = "   <-- corruption Phase 1 did not see" if v == "CLEAN" and m else ""
            print(f"{v:18}{n:>7}{m:>10}{m/max(n,1):>8.3f}{flag}")


# ---------------------------------------------------------------------------
# §3.3 structural validity
# ---------------------------------------------------------------------------

def report_structural(rows):
    print("\n=== §3.3 structural validity — right script, possible sequences?")
    print("Phase 1's invalid_rate_per_1k, unchanged, applied per page. Scored")
    print("only on pages that pass loss and carry enough Devanagari to judge.\n")
    print(f"{'arm':12}{'scored':>8}{'invalid':>9}{'macro':>8}{'rate/1k':>9}"
          f"{'spurious-sp':>13}")

    for arm in arms_present(rows):
        num, den = defaultdict(int), defaultdict(int)
        scored = bad = 0
        rate_sum = 0.0
        spurious = 0
        for r in rows:
            if r["arm"] != arm or ex.loss_verdict(r):
                continue
            if (r["dev_chars"] or 0) < fa.MIN_DEV_CHARS:
                continue
            scored += 1
            den[r["body"]] += 1
            rate_sum += r["invalid_rate_per_1k"] or 0.0
            if (r["invalid_rate_per_1k"] or 0.0) >= INVALID_MAX:
                bad += 1
                num[r["body"]] += 1
            # A violation count that collapses once whitespace is removed is a
            # space the extractor inserted mid-word, not a reordered glyph
            # stream. phase0-schema.md §4.4 reserved this for Phase 3.
            if (r["invalid_matras"] or 0) > (r["invalid_matras_nospace"] or 0):
                spurious += 1
        if not scored:
            continue
        macro, _ = macro_pooled(num, den)
        print(f"{arm:12}{scored:>8}{bad/scored:>9.3f}{fmt(macro, 8)}"
              f"{rate_sum/scored:>9.2f}{spurious/scored:>13.3f}")

    print(f"\ninvalid at rate >= {INVALID_MAX}/1k; needs >= {fa.MIN_DEV_CHARS} "
          f"Devanagari characters to score")
    print("spurious-sp: share of pages where removing whitespace removes a")
    print("             violation — the extractor's defect, not the font's")


# ---------------------------------------------------------------------------
# §3.4 cross-arm agreement
# ---------------------------------------------------------------------------

def report_agreement(rows):
    print("\n=== §3.4 cross-arm agreement — do the tools even agree?")
    print("No reference needed. Where two arms disagree, at least one is wrong,")
    print("which is a lower bound on error that costs no ground truth at all.\n")

    by_page = defaultdict(dict)
    for r in rows:
        if r["arm"] != "ocr" and not ex.loss_verdict(r):
            by_page[(r["sha256"], r["page"])][r["arm"]] = r

    arms = [a for a in arms_present(rows) if a != "ocr"]
    print(f"{'pair':26}{'pages':>7}{'identical':>11}{'same chars':>12}"
          f"{'differ':>8}")
    for i, a1 in enumerate(arms):
        for a2 in arms[i + 1:]:
            n = ident = bag = 0
            for page in by_page.values():
                if a1 not in page or a2 not in page:
                    continue
                n += 1
                if page[a1]["text_hash"] == page[a2]["text_hash"]:
                    ident += 1
                elif page[a1]["bag_hash"] == page[a2]["bag_hash"]:
                    bag += 1
            if n:
                print(f"{a1 + ' vs ' + a2:26}{n:>7}{ident/n:>11.3f}"
                      f"{bag/n:>12.3f}{(n-ident-bag)/n:>8.3f}")

    print("\nidentical : same characters, same order (whitespace ignored)")
    print("same chars: same characters, DIFFERENT order — a reading-order")
    print("            defect, and it belongs to the extractor, not the font")
    print("differ    : the arms do not agree on what the text says at all")

    full = sum(1 for p in by_page.values()
               if len(p) >= 2 and len({r["text_hash"] for r in p.values()}) == 1)
    scored = sum(1 for p in by_page.values() if len(p) >= 2)
    if scored:
        print(f"\nall arms identical on {full}/{scored} = {full/scored:.1%} of pages")
        print(f"so at least one arm is wrong on >= {1-full/scored:.1%} of pages")


# ---------------------------------------------------------------------------
# §3.5 composite
# ---------------------------------------------------------------------------

def report_composite(conn, rows, run_id):
    print("\n=== §3.5 usable-page rate — the composite")
    print("A page is usable if it passes loss AND script concordance AND")
    print("structural validity. Loss gates: a page failing it is scored failed")
    print("and never reaches the other two.\n")

    has_ocr = any(r["arm"] == "ocr" for r in rows)
    if has_ocr:
        build_pairs(conn, rows, run_id)
        conc = {(p["sha256"], p["page"], p["arm"]): p["concordance"]
                for p in conn.execute(
                    "SELECT * FROM page_pair WHERE run_id=?", (run_id,))}
    else:
        conc = {}
        print("no OCR arm: the concordance term is skipped, so this composite")
        print("is loss AND structural only. It is an UPPER bound on usability.\n")

    print(f"{'arm':12}{'pages':>7}{'usable':>8}{'macro':>8}   {'failed by':>9}")
    for arm in [a for a in arms_present(rows) if a != "ocr"]:
        num, den = defaultdict(int), defaultdict(int)
        n = ok = 0
        why = defaultdict(int)
        for r in rows:
            if r["arm"] != arm:
                continue
            n += 1
            den[r["body"]] += 1
            if ex.loss_verdict(r):
                why["loss"] += 1
                continue
            c = conc.get((r["sha256"], r["page"], arm))
            if c in ("script_mismatch", "script_excess"):
                why["script"] += 1
                continue
            if ((r["dev_chars"] or 0) >= fa.MIN_DEV_CHARS
                    and (r["invalid_rate_per_1k"] or 0.0) >= INVALID_MAX):
                why["structural"] += 1
                continue
            ok += 1
            num[r["body"]] += 1
        if not n:
            continue
        macro, _ = macro_pooled(num, den)
        detail = " ".join(f"{k}={v}" for k, v in sorted(why.items()))
        print(f"{arm:12}{n:>7}{ok/n:>8.3f}{fmt(macro, 8)}   {detail}")


# ---------------------------------------------------------------------------
# threshold sweep
# ---------------------------------------------------------------------------

def report_sweep(rows, which):
    print(f"\n=== sweep: {which}")
    print("The loss gate was chosen from a 40-document pilot, which asserted a")
    print("bimodal distribution rather than establishing one. If the counts")
    print("below fall away smoothly instead of stepping, the gate is wrong.\n")

    col = {"replacement": "replacement_ratio", "control": "control_ratio",
           "chars": "n_chars"}[which]
    grid = ([0.0, .001, .005, .01, .02, .05, .10, .20, .30, .50]
            if which != "chars" else [0, 10, 25, 50, 100, 200, 400, 800])

    arms = [a for a in arms_present(rows) if a != "ocr"]
    print(f"{'threshold':>10}" + "".join(f"{a:>12}" for a in arms))
    for t in grid:
        cells = []
        for arm in arms:
            a = [r for r in rows if r["arm"] == arm]
            if not a:
                cells.append(f"{'--':>12}")
                continue
            # 'chars' fails BELOW its threshold; the ratios fail above theirs.
            hit = sum(1 for r in a
                      if ((r[col] or 0) < t if which == "chars"
                          else (r[col] or 0) >= t))
            cells.append(f"{hit/len(a):>12.3f}")
        print(f"{t:>10}" + "".join(cells))
    print("\nshare of pages the gate would fail at each threshold")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--run", required=True)
    ap.add_argument("--db", default=str(config.MANIFEST_DB))
    ap.add_argument("--loss", action="store_true")
    ap.add_argument("--concordance", action="store_true")
    ap.add_argument("--structural", action="store_true")
    ap.add_argument("--agreement", action="store_true")
    ap.add_argument("--composite", action="store_true")
    ap.add_argument("--sweep", choices=["replacement", "control", "chars"])
    ap.add_argument("--all", action="store_true", help="every report, in order")
    args = ap.parse_args()

    conn = connect(args.db)
    meta = conn.execute("SELECT * FROM extraction_run WHERE run_id=?",
                        (args.run,)).fetchone()
    rows = load(conn, args.run)

    print(f"run    : {args.run}")
    if meta:
        print(f"tier   : {meta['tier']}   documents: {meta['n_documents']}   "
              f"pages: 1..{meta['max_pages']}")
        print(f"arms   : {meta['arms']}")
    print(f"rows   : {len(rows)}")
    if not rows:
        print("\nno extraction rows for this run — nothing to report.")
        return

    want = args.all or not any([args.loss, args.concordance, args.structural,
                                args.agreement, args.composite, args.sweep])
    if want or args.loss:
        report_loss(rows)
    if want or args.concordance:
        report_concordance(conn, rows, args.run)
    if want or args.structural:
        report_structural(rows)
    if want or args.agreement:
        report_agreement(rows)
    if want or args.composite:
        report_composite(conn, rows, args.run)
    if args.sweep:
        report_sweep(rows, args.sweep)
    conn.close()


if __name__ == "__main__":
    main()
