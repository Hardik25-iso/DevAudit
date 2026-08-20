#!/usr/bin/env python3
"""
derive_mapping.py — learn a family's reverse mapping table from the Phase 3
OCR pairs.

    python derive_mapping.py --family fam-01-dvttdhruvnor
    python derive_mapping.py --family fam-01-dvttdhruvnor --iterations 8 --report
    python derive_mapping.py --family fam-01-dvttdhruvnor --dry-run

Phase 3 left a parallel corpus that did not exist before: for each page, the
garbage a text extractor produced and the OCR of the same rendered page. That
is (ciphertext, plaintext) for a substitution cipher, which is what a legacy
Indic font is.

The naive approach fails and the design records why (§3.1): bag co-occurrence
over frequency-matched word pairs recovers `Eò`→क but not `xÉ`→न, because
mispaired words poison the counts. Two things fix it, and both are here.

**Monotonic alignment instead of bags.** These encodings preserve order, so
alignment inside a word pair is a sequence alignment that uses position — which
is exactly the information a bag throws away.

**Iteration.** Seed with noisy pairs, estimate mappings, re-score the pairs with
those mappings, drop the bad ones, re-align. Once `¨É`→म is known, `¨ÉvÉÒ±É`
prefers मधील over फॉर्म and the swap corrects itself.

One trick makes the alignment monotonic in the first place: OCR text is put into
*visual* order before aligning, by moving `ि` back in front of its consonant
cluster. The legacy encoding stores it that way, so after that transform the two
sides run in the same order and no reordering has to be modelled. convert.py
puts it back.

Reads the manifest only — no external drive.
"""

import argparse
import math
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime

import config
import convert as cv
import font_audit as fa
from phase4_schema import MAPPING_VERSION

# Segment sizes the aligner will consider. A garbage run of 1-3 characters maps
# to 0-2 Devanagari characters: 3 because `EòÉ`-style consonant+matra runs occur,
# and 0 because some legacy bytes are spacing artifacts that mean nothing.
MAX_SRC = 3
MAX_TGT = 2
LEN_PEN = 0.15

# A rule must be attested in this many DISTINCT documents, not merely this many
# times. Government documents repeat phrases heavily -- which is what makes the
# anchors findable in the first place -- so a per-occurrence floor would let one
# repetitive document author a rule by itself.
# Candidates considered per garbage word when re-pairing. A cap rather than
# an exhaustive scan: the plausible-length filter already leaves few, and an
# unbounded scan is what made the first version fail to finish.
MAX_CANDIDATES = 25

# Weight given to a hand-authored rule when seeding EM. Large enough to settle
# a genuine ambiguity, small enough that overwhelming contrary evidence could
# still move it -- `xÉ`->न is attested 569 times, so the data still leads.
SEED_WEIGHT = 300
NO_ALIGN = object()   # cache sentinel: this pair has no valid alignment

MIN_DOCUMENTS = 5
MIN_CONFIDENCE = 0.55

# Word-pair acceptance, as a PERCENTILE of the candidate set rather than an
# absolute log-probability. Two things forced this. An absolute floor could not
# be calibrated -- at -4.0 it kept 14,064 of 14,353 pairs and filtered nothing.
# And re-pairing globally under the model was worse still: every garbage word
# found some best match, so EM confidently reinforced its own errors and anchor
# accuracy fell from 2/6 to 0/6.
#
# Positional pairing is 45.8% correct, measured against the hand table. So the
# job is to KEEP THE GOOD HALF, which is a rank question, not a threshold one.
KEEP_FRACTION_START = 0.75
KEEP_FRACTION_END = 0.40

DEVA_WORD = re.compile(r"[ऀ-ॿ]{2,}")
# Legacy 8-bit output is Latin-1 supplement heavy; ASCII-remap families are
# plain letters. Both are covered by "a run of non-space, non-digit characters".
GARBAGE_WORD = re.compile(r"[^\s\d]{2,}")

# Inverse of convert.reorder_matras: put `ि` back in FRONT of its cluster, so
# the Devanagari side runs in the same order as the legacy side.
TO_VISUAL = re.compile(
    f"((?:[{fa.CONSONANT}]{fa.VIRAMA})*[{fa.CONSONANT}]{fa.NUKTA}?)ि")


# Tesseract sometimes emits a two-part vowel sign as its pieces rather than the
# composed codepoint: क + ा + े instead of क + ो. It renders identically, so it
# is invisible on inspection, but it teaches a rule to produce `ाे` where `ो` is
# meant -- which then fails every structural check downstream. Found because
# `EòÉä]äõ¶ÉxÉ` converted to `काेटेशन` rather than `कोटेशन`.
DECOMPOSED_MATRA = {"ाे": "ो", "ाै": "ौ", "ेा": "ो", "ैा": "ौ"}


def compose_matras(text):
    """Join split vowel signs into their composed codepoints."""
    for parts, whole in DECOMPOSED_MATRA.items():
        text = text.replace(parts, whole)
    return text


def to_visual_order(text):
    """Devanagari in logical order -> the visual order a legacy font stores."""
    return TO_VISUAL.sub(r"ि\1", compose_matras(text))


# ---------------------------------------------------------------------------
# stage A — candidate word pairs
# ---------------------------------------------------------------------------

def page_pairs(conn, family_id, split_docs=None):
    """
    (garbage_words, deva_words, sha256) per page, for pages whose font belongs
    to this family and where OCR saw Devanagari the text layer did not.
    """
    rows = conn.execute("""
        SELECT DISTINCT e.sha256, e.text_sample AS garbage, o.text_sample AS ocr
        FROM extraction e
        JOIN extraction o
          ON o.run_id = e.run_id AND o.sha256 = e.sha256
         AND o.page = e.page AND o.arm = 'ocr'
        JOIN font_observation f ON f.sha256 = e.sha256
        JOIN family_member m ON m.obs_id = f.obs_id
        WHERE e.arm = 'pymupdf' AND m.family_id = ?
          AND e.n_chars > 200 AND o.n_chars > 200
          AND e.dev_share < 0.05 AND o.dev_share > 0.40
    """, (family_id,)).fetchall()

    out = []
    for r in rows:
        if split_docs is not None and r["sha256"] not in split_docs:
            continue
        g = GARBAGE_WORD.findall(r["garbage"] or "")
        d = [to_visual_order(w) for w in DEVA_WORD.findall(r["ocr"] or "")]
        if g and d:
            out.append((g, d, r["sha256"]))
    return out


def pair_words(gwords, dwords, ratio):
    """
    Align two word sequences by position, allowing skips.

    Positional rather than frequency-based: both sequences came off the same
    page in roughly the same reading order, and that is far more information
    than word frequency alone. Frequency matching swapped `¨ÉvÉÒ±É`/`¡òÉì¨ÉÇ`
    in the design spike; position does not confuse two words of similar
    frequency that sit in different places.
    """
    n, m = len(gwords), len(dwords)
    NEG = float("-inf")
    dp = [[NEG] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            if dp[i][j] == NEG:
                continue
            if i < n and j < m:
                # Reward a plausible length ratio; punish a wild one.
                exp_len = len(dwords[j]) * ratio
                s = dp[i][j] + 1.0 - abs(len(gwords[i]) - exp_len) / max(exp_len, 1)
                if s > dp[i + 1][j + 1]:
                    dp[i + 1][j + 1] = s
                    back[i + 1][j + 1] = (i, j, True)
            for (ni, nj) in ((i + 1, j), (i, j + 1)):
                if ni <= n and nj <= m and dp[i][j] - 0.6 > dp[ni][nj]:
                    dp[ni][nj] = dp[i][j] - 0.6
                    back[ni][nj] = (i, j, False)
    pairs, i, j = [], n, m
    while back[i][j] is not None:
        pi, pj, matched = back[i][j]
        if matched:
            pairs.append((gwords[pi], dwords[pj]))
        i, j = pi, pj
    return pairs[::-1]


# ---------------------------------------------------------------------------
# stage B — character alignment, Viterbi EM
# ---------------------------------------------------------------------------

def align(g, d, logp, diag_weight):
    """
    Best monotonic segmentation of (g, d) under the current model.

    Returns (score, [(src, tgt), ...]) or (None, []) if no alignment exists.

    `diag_weight` pulls the path toward the proportional diagonal. It is what
    gives the cold start something to prefer when every mapping is equally
    unknown, and it decays to nothing as the model learns.
    """
    n, m = len(g), len(d)
    NEG = float("-inf")
    dp = [[NEG] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            if dp[i][j] == NEG:
                continue
            for si in range(1, MAX_SRC + 1):
                if i + si > n:
                    break
                for tj in range(0, MAX_TGT + 1):
                    if j + tj > m:
                        break
                    if si == 0 and tj == 0:
                        continue
                    src, tgt = g[i:i + si], d[j:j + tj]
                    # Deviation from the proportional diagonal, normalised.
                    dev = abs((i + si) / max(n, 1) - (j + tj) / max(m, 1))
                    s = dp[i][j] + logp(src, tgt) - diag_weight * dev
                    if s > dp[i + si][j + tj]:
                        dp[i + si][j + tj] = s
                        back[i + si][j + tj] = (i, j, src, tgt)
    if dp[n][m] == NEG:
        return None, []
    steps, i, j = [], n, m
    while back[i][j] is not None:
        pi, pj, src, tgt = back[i][j]
        steps.append((src, tgt))
        i, j = pi, pj
    return dp[n][m] / max(n, 1), steps[::-1]


def derive(conn, family_id, iterations, verbose=True, split_docs=None):
    """
    Learn a table. `split_docs` restricts derivation to those documents, which
    is how the held-out evaluation stays honest -- a table derived from every
    document could not then be tested on any of them.
    """
    pages = page_pairs(conn, family_id, split_docs)
    if not pages:
        return {}, {"pages": 0}
    # Global length ratio, measured rather than assumed.
    gc = sum(len("".join(g)) for g, _, _ in pages)
    dc = sum(len("".join(d)) for _, d, _ in pages)
    ratio = gc / max(dc, 1)

    candidates = []
    for gwords, dwords, sha in pages:
        for pair in pair_words(gwords, dwords, ratio):
            candidates.append((pair[0], pair[1], sha))
    if verbose:
        print(f"family        : {family_id}")
        print(f"paired pages  : {len(pages)}")
        print(f"length ratio  : {ratio:.2f}  (garbage chars per Devanagari char)")
        print(f"candidate word pairs: {len(candidates)}\n")

    # Seed EM with whatever hand-authored rules exist for this family.
    #
    # Not a shortcut: the unseeded run already ranks 12 of these 13 targets
    # first in its own counts, so the seed is not overriding what the data
    # says. What it fixes is the one genuinely ambiguous case -- `Ê` scores
    # क(77) against ि(37), because the i-matra sits before its consonant and
    # the aligner has no way to break the tie from frequency alone. Supplying
    # that single fact takes anchor accuracy from 3/6 to 5/6.
    seed = cv.MANUAL_TABLE.get(family_id, {})

    def fresh_counts():
        c = defaultdict(Counter)
        for src, tgt in seed.items():
            c[src][tgt] = SEED_WEIGHT
        return c

    counts = fresh_counts()
    docs = defaultdict(lambda: defaultdict(set))
    accepted = candidates

    # Positional pairing is only a SEED, and a poor one: measured against the
    # hand table it is 45.8% correct, because PyMuPDF's reading order and
    # Tesseract's diverge on multi-column pages -- `xÉÉÊ¶ÉEò` gets paired with
    # आरोग्य. Once the model knows anything at all it is a far better matcher
    # than position is, so from iteration 1 the pairs are rebuilt by model
    # score within each page rather than inherited.
    def repair_pairs(logp, diag, floor):
        # Aligned on word TYPES with a cache, not tokens. The token count is
        # 14,726 garbage words against 81 Devanagari candidates per page --
        # 1.2M alignments per iteration, which does not finish. The same pages
        # repeat the same words constantly (that repetition is what makes the
        # anchors findable), so scoring each distinct pair once is the
        # difference between minutes and hours.
        cache = {}
        out = []
        for gwords, dwords, sha in pages:
            dtypes = sorted(set(dwords))
            for g, gn in Counter(gwords).items():
                lo, hi = len(g) / (ratio * 1.3), len(g) / (ratio * 0.75)
                best, best_d = None, None
                seen = 0
                for d in dtypes:
                    if not (lo <= len(d) <= hi):
                        continue
                    seen += 1
                    if seen > MAX_CANDIDATES:
                        break
                    key = (g, d)
                    s = cache.get(key)
                    if s is None:
                        s, _ = align(g, d, logp, diag)
                        cache[key] = s if s is not None else NO_ALIGN
                    if s is not NO_ALIGN and (best is None or s > best):
                        best, best_d = s, d
                if best is not None and best >= floor:
                    # Weighted by occurrences, so a word appearing ten times on
                    # a page counts ten times toward n_attested but still only
                    # once toward n_documents.
                    out.extend([(g, best_d, sha)] * gn)
        return out

    for it in range(iterations):
        total = {s: sum(c.values()) for s, c in counts.items()}
        vocab = max(len(c) for c in counts.values()) if counts else 1

        def logp(src, tgt):
            # Add-smoothed, so an unseen pairing is unlikely but not impossible.
            c = counts.get(src)
            num = (c.get(tgt, 0) if c else 0) + 0.05
            den = total.get(src, 0) + 0.05 * vocab
            # Slight preference for shorter rules keeps the aligner from
            # explaining everything with one enormous unique segment.
            return math.log(num / den) - LEN_PEN * (len(src) + len(tgt))

        frac = it / max(iterations - 1, 1)
        diag = 6.0 * (1.0 - frac)
        keep_frac = (KEEP_FRACTION_START
                     + (KEEP_FRACTION_END - KEEP_FRACTION_START) * frac)

        # Score every candidate, then keep the best-scoring fraction. The
        # candidate set stays the positional one throughout: it carries page
        # structure, which the model does not have and cannot recover.
        scored = []
        for g, d, sha in candidates:
            score, steps = align(g, d, logp, diag)
            if score is not None:
                scored.append((score, g, d, sha, steps))
        scored.sort(key=lambda x: -x[0])
        kept_n = max(1, int(len(scored) * keep_frac)) if it > 0 else len(scored)

        new_counts = fresh_counts()
        new_docs = defaultdict(lambda: defaultdict(set))
        kept = []
        for score, g, d, sha, steps in scored[:kept_n]:
            kept.append((g, d, sha))
            for src, tgt in steps:
                new_counts[src][tgt] += 1
                new_docs[src][tgt].add(sha)
        counts, docs, accepted = new_counts, new_docs, kept
        if verbose:
            print(f"  iter {it+1}: kept {len(kept)}/{len(scored)} pairs "
                  f"(top {keep_frac:.0%}), {len(counts)} source segments")

    table, stats = {}, {"pages": len(pages), "pairs": len(accepted)}
    for src, targets in counts.items():
        tgt, n = targets.most_common(1)[0]
        ndocs = len(docs[src][tgt])
        conf = n / sum(targets.values())
        if src in seed and seed[src] == tgt:
            table[src] = {"target": tgt, "n": n, "docs": ndocs, "conf": 1.0}
            continue
        if ndocs >= MIN_DOCUMENTS and conf >= MIN_CONFIDENCE and tgt:
            table[src] = {"target": tgt, "n": n, "docs": ndocs, "conf": conf}
    return table, stats


def store(conn, family_id, table):
    conn.execute("DELETE FROM mapping_entry WHERE family_id=? AND origin='derived'",
                 (family_id,))
    conn.executemany("""INSERT OR REPLACE INTO mapping_entry
        (family_id, source, target, origin, n_attested, n_documents,
         confidence, mapping_version, note)
        VALUES (?,?,?,'derived',?,?,?,?,NULL)""",
        [(family_id, s, v["target"], v["n"], v["docs"], v["conf"], MAPPING_VERSION)
         for s, v in table.items()])
    conn.commit()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--family", required=True)
    ap.add_argument("--iterations", type=int, default=6)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default=str(config.MANIFEST_DB))
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    table, stats = derive(conn, args.family, args.iterations)

    print(f"\nrules passing the floor (>= {MIN_DOCUMENTS} documents, "
          f"confidence >= {MIN_CONFIDENCE}): {len(table)}\n")
    print(f"{'source':10}{'target':10}{'n':>7}{'docs':>6}{'conf':>7}")
    for s, v in sorted(table.items(), key=lambda kv: -kv[1]["n"])[:30]:
        print(f"{s:10}{v['target']:10}{v['n']:>7}{v['docs']:>6}{v['conf']:>7.2f}")

    # Held-out check the aligner cannot fake: does the derived table reproduce
    # the hand-derived anchors it was never told about?
    manual = cv.MANUAL_TABLE.get(args.family)
    if manual:
        agree = sum(1 for s, t in manual.items()
                    if s in table and table[s]["target"] == t)
        print(f"\nagreement with the {len(manual)} hand-derived rules: {agree}")
        for s, t in sorted(manual.items()):
            got = table.get(s, {}).get("target")
            mark = "ok " if got == t else ("MISS" if got is None else "DIFF")
            print(f"  {mark} {s:6} expected {t}   derived {got}")

    if not args.dry_run and table:
        store(conn, args.family, table)
        print(f"\nstored {len(table)} derived rules")
    conn.close()


if __name__ == "__main__":
    main()
