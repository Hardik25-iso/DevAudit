#!/usr/bin/env python3
"""
convert.py — apply a reverse mapping table to legacy-encoded text.

    from convert import convert, load_table
    convert("xÉÉÊ¶ÉEò", table)        -> 'नाशिक'

    python convert.py --family fam-01-dvttdhruvnor --demo
    python convert.py --seed-manual                 # install the hand table

Two stages, and they are separate because they undo two different things:

1. **Substitution.** Longest-first, so `Eò` is tried before `E`. Getting this
   backwards silently produces plausible garbage -- `E` matches, `ò` falls
   through unmapped, and the output looks converted.
2. **Matra reordering.** Legacy fonts store `ि` in *visual* order, before its
   consonant. Unicode stores it logically, after. This pass moves it, and it is
   the same visual-vs-logical mismatch OVERVIEW.md names as the root cause --
   appearing here as the thing that has to be undone.

The applier is written and tested against a HAND-AUTHORED table before the
derivation code exists (design §7). Otherwise a bad conversion is ambiguous
between a bad table and a bad applier, and there is no way to tell which.
"""

import argparse
import re
import sqlite3

import config
import font_audit as fa

# Consonant range from font_audit, so "what is a consonant" has one definition
# in this project rather than two that can drift apart.
CONSONANT = f"[{fa.CONSONANT}]"
VIRAMA = fa.VIRAMA
I_MATRA = "ि"

# Only a `ि` that the table itself emitted may be moved. Text that was already
# correct must survive untouched, and there is no way to tell the two apart
# after the fact: 'नाशिक' (right) and 'नािशक' (wrong) are both just a matra
# next to a consonant. So substitution marks the matras it produces, and
# reordering moves only those.
#
# Found by the negative-control test rather than by reasoning: a blind regex
# turned the correct 'नाशिक' into 'नाशकि', which would mean running the
# converter over clean documents corrupted them.
MARK = ""      # private use; never appears in extracted text

# The cluster is one or more consonants joined by viramas, and the matra
# belongs after the whole thing, not after the first consonant. 'िक्ष' must
# become 'क्षि', never 'किष'.
I_MATRA_CLUSTER = re.compile(
    f"{MARK}{I_MATRA}((?:{CONSONANT}{VIRAMA})*{CONSONANT}{fa.NUKTA}?)")

# Repha: an `र्` that renders as a mark above a LATER consonant, so a legacy
# font stores it after the cluster it belongs to. Logical order puts it first,
# so it moves backwards — the opposite direction to the i-matra, and past any
# vowel signs attached to the cluster.
#
#   गृहिनमाÃण  ->  गृहिनर्माण      (Ã = र्, sitting after 'मा')
#
# Found while hand-seeding fam-02, where the APS encoding uses it heavily.
# Marked like the i-matra, so already-correct text is never touched.
REPHA = "र" + VIRAMA
MATRA_CLASS = f"[{fa.MATRA}{fa.NUKTA}{fa.VIRAMA}]"
REPHA_CLUSTER = re.compile(
    f"({CONSONANT}(?:{VIRAMA}{CONSONANT})*{MATRA_CLASS}*?){MARK}{REPHA}")


# ---------------------------------------------------------------------------
# The hand table. Thirteen entries, derived by hand from two anchor pairs and
# then checked against a third that was not used to build it:
#
#   xÉÉÊ¶ÉEò            -> नािशक        -> नाशिक
#   ¨É½þÉxÉMÉ®ú{ÉÉÊ±ÉEòÉ -> महानगरपािलका -> महानगरपालिका
#   Eò®úhÉä             -> करणे          (held out, reconstructed exactly)
#
# Deliberately partial. Its job is to prove the applier, not to convert the
# corpus -- that is derive_mapping.py's job.
# ---------------------------------------------------------------------------
# fam-02 (APS) is a structurally different encoding and was seeded the same
# way: read off two side-by-side pairs, then checked against six words that
# were not used to build it, of which four reconstructed exactly and the two
# misses were rules deliberately left out.
#
#   ãä½ãß‡ãŠ¦ã          -> मिळकत
#   Ì¾ãÌãÔ©ãã¹ã¶ããÎããè -> व्यवस्थापनाशी
#   ØãðÖãä¶ã½ããÃ¥ã     -> गृहनिर्माण    (held out; needs the repha rule)
#
# Its shape is worth recording: a consonant carries a trailing `ã`, and the
# bare letter is the half form. `Ìã` is व and `Ì` is व्. That is why single
# characters matter here in a way they do not for fam-01.
MANUAL_TABLE = {
    "fam-01-dvttdhruvnor": {
        "xÉ": "न", "¨É": "म", "½þ": "ह", "É": "ा", "MÉ": "ग",
        "®ú": "र", "{É": "प", "±É": "ल", "Eò": "क", "Ê": "ि",
        "¶É": "श", "hÉ": "ण", "ä": "े",
    },
    "fam-02-apscdvpriyan": {
        # vowel signs
        "ãä": "ि", "ãè": "ी", "ã": "ा", "ì": "ु", "ñ": "े", "ð": "ृ",
        "â": "ं", "Ã": "र्",
        # consonants (letter + ã)
        "½ã": "म", "¶ã": "न", "¹ã": "प", "¦ã": "त", "Îã": "श", "Ìã": "व",
        "Ôã": "स", "Øã": "ग", "¥ã": "ण", "¾ã": "य", "‡ãŠ": "क",
        # single-glyph consonants and half forms
        "ß": "ळ", "À": "र", "Ö": "ह", "ª": "द", "¡": "ड", "Œ": "ख्",
    },
}


# Scoring for the segmentation search below.
#
# A matched rule earns its source length, scaled by confidence; an unmatched
# character costs half a character. So covering the string with confident rules
# beats leaving gaps, and between two segmentations that both cover it fully,
# the more confident one wins.
UNMATCHED_COST = 0.5
MIN_CONF_WEIGHT = 0.5      # even a low-confidence rule beats leaving a gap


def normalize_table(table):
    """Accept {source: target} or {source: (target, confidence)}."""
    out = {}
    for source, value in table.items():
        if isinstance(value, tuple):
            out[source] = (value[0], float(value[1]))
        else:
            out[source] = (value, 1.0)
    return out


def substitute(text, table):
    """
    Choose the best whole-string segmentation, not the longest match at each
    position. Returns (converted, n_chars_matched).

    Greedy longest-first was the first version and it is wrong for a *derived*
    table. The deriver learns segmentations in context, so a rule like `ÉEò`->शक
    is only valid after `Ê¶`; applied greedily it fires anywhere and the rest of
    the word is left as gaps. Measured: greedy converted 2 of 6 anchor words,
    and the failures all looked like `xÉÉÊ¶ÉEò` -> `नाांक` -- full coverage,
    wrong rules.

    Scoring the whole segmentation fixes that, because a wrong long rule that
    strands the remainder of the word loses to one that covers it cleanly. It
    is also the same criterion derive_mapping.align() uses, so the applier and
    the deriver no longer disagree about what a good segmentation is.
    """
    tbl = normalize_table(table)
    if not tbl:
        return text, 0
    by_start = {}
    for source in tbl:
        by_start.setdefault(source[0], []).append(source)

    n = len(text)
    # best[i] = (score, back_index, source_or_None) for text[:i]
    best = [None] * (n + 1)
    best[0] = (0.0, -1, None)
    for i in range(n):
        if best[i] is None:
            continue
        score = best[i][0]
        # option 1: leave this character alone
        cand = score - UNMATCHED_COST
        if best[i + 1] is None or cand > best[i + 1][0]:
            best[i + 1] = (cand, i, None)
        # option 2: apply any rule that matches here
        for source in by_start.get(text[i], ()):
            if not text.startswith(source, i):
                continue
            _, conf = tbl[source]
            gain = len(source) * (MIN_CONF_WEIGHT + (1 - MIN_CONF_WEIGHT) * conf)
            j = i + len(source)
            cand = score + gain
            if best[j] is None or cand > best[j][0]:
                best[j] = (cand, i, source)

    # walk the chosen path back
    pieces, matched, i = [], 0, n
    while i > 0:
        _, prev, source = best[i]
        if source is None:
            pieces.append(text[prev:i])
        else:
            target = tbl[source][0]
            # Mark EVERY matra this table produced, including ones inside a
            # multi-character target. A derived rule like `Ê´É`->`िव` emits the
            # matra mid-target; marking only whole-target matras left it
            # unmarked, so reordering skipped it and `Ê´É¦ÉÉMÉÉiÉÒ±É` came out
            # as `िवभागातील` instead of `विभागातील`.
            #
            # Safe because the deriver learns against VISUAL-order Devanagari
            # (derive_mapping.to_visual_order), so every matra in a learned
            # target is one that still needs moving.
            marked = target.replace(I_MATRA, MARK + I_MATRA)
            marked = marked.replace(REPHA, MARK + REPHA)
            pieces.append(marked)
            matched += len(source)
        i = prev
    return "".join(reversed(pieces)), matched


def reorder_matras(text):
    """
    Move each *marked* `ि` from before its consonant cluster to after it, then
    drop the marks.

    Unmarked matras are left alone. Passing unmarked text through this is
    therefore a no-op, which is what makes convert() safe to run over
    already-correct Devanagari.

    Two moves, in opposite directions: `ि` goes forward past its cluster, and
    repha `र्` goes backward past the cluster it sits on.
    """
    text = I_MATRA_CLUSTER.sub(r"\1" + I_MATRA, text)
    text = REPHA_CLUSTER.sub(REPHA + r"\1", text)
    return text.replace(MARK, "")


# A page whose text layer already contains this share of Devanagari is not
# legacy-encoded -- that near-total ABSENCE of Devanagari is the defining
# property of the failure, and the signal the whole project detects on. So
# above this threshold the converter refuses.
#
# Added because the negative control failed without it: applied blind to clean
# Devanagari pages, the derived table changed 11 of 11 and made 5 structurally
# worse. Single-character rules like `x`->क and `V`->व fire on the ordinary
# Latin text that sits inside Marathi documents. A converter that corrupts
# working documents is worse than one that does nothing.
MAX_DEV_SHARE = 0.05


def is_legacy_encoded(text):
    """Cheap gate: does this text look like it needs converting at all?"""
    body = re.sub(r"\s+", "", text)
    if not body:
        return False
    return len(fa.DEV_RANGE.findall(body)) / len(body) <= MAX_DEV_SHARE


def convert(text, table):
    """Full conversion. Returns (text, coverage) — coverage is the share of
    characters the table actually matched.

    Coverage is returned rather than computed later because a table that
    rewrites 3% of a page is not converting it, and every other measure in
    design §4 would score that non-conversion well by default.

    Text that is already Devanagari is returned untouched (see MAX_DEV_SHARE).
    Refusing is the correct answer there, not a limitation: the converter has
    nothing to fix and every edit it makes can only do harm.
    """
    if not is_legacy_encoded(text):
        return text, 0.0
    subbed, matched = substitute(text, table)
    return reorder_matras(subbed), (matched / len(text) if text else 0.0)


def load_table(conn, family_id):
    """
    Load a family's rules WITH their confidences.

    Confidence is not decoration -- it is what the segmentation search ranks
    on. Measured on the dominant family's derived table, confidence-weighted
    decoding converts 5 of 6 anchor words and unweighted decoding converts 2,
    because the derived table contains both reliable atomic rules and unreliable
    long ones and only the confidence separates them.
    """
    rows = conn.execute(
        "SELECT source, target, COALESCE(confidence, 1.0) "
        "FROM mapping_entry WHERE family_id = ?", (family_id,)).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def seed_manual(conn):
    """Install the hand table, so the applier can be exercised end to end."""
    from phase4_schema import MAPPING_VERSION
    n = 0
    for family_id, table in MANUAL_TABLE.items():
        for source, target in table.items():
            conn.execute("""INSERT OR REPLACE INTO mapping_entry
                (family_id, source, target, origin, n_attested, n_documents,
                 confidence, mapping_version, note)
                VALUES (?,?,?,'manual',NULL,NULL,1.0,?,
                        'hand-derived from anchor pairs; proves the applier')""",
                (family_id, source, target, MAPPING_VERSION))
            n += 1
    conn.commit()
    print(f"seeded {n} manual rules across {len(MANUAL_TABLE)} families")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--family", default="fam-01-dvttdhruvnor")
    ap.add_argument("--seed-manual", action="store_true")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--db", default=str(config.MANIFEST_DB))
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    if args.seed_manual:
        seed_manual(conn)

    if args.demo or not args.seed_manual:
        table = load_table(conn, args.family) or MANUAL_TABLE.get(args.family, {})
        print(f"family : {args.family}   rules: {len(table)}\n")
        for sample in ["xÉÉÊ¶ÉEò", "¨É½þÉxÉMÉ®ú{ÉÉÊ±ÉEòÉ", "Eò®úhÉä",
                       "xÉÉÊ¶ÉEò ¨É½þÉxÉMÉ®ú{ÉÉÊ±ÉEòÉ"]:
            got, cov = convert(sample, table)
            print(f"  {sample:26} -> {got:22} coverage {cov:.2f}")
    conn.close()


if __name__ == "__main__":
    main()
