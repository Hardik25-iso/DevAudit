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
MANUAL_TABLE = {
    "fam-01-dvttdhruvnor": {
        "xÉ": "न", "¨É": "म", "½þ": "ह", "É": "ा", "MÉ": "ग",
        "®ú": "र", "{É": "प", "±É": "ल", "Eò": "क", "Ê": "ि",
        "¶É": "श", "hÉ": "ण", "ä": "े",
    },
}


def substitute(text, table):
    """
    Longest-first replacement. Returns (converted, n_chars_matched).

    A plain str.replace loop over the table would re-substitute its own output
    -- a rule whose target contains another rule's source would fire twice. So
    this walks the string once and never revisits what it has emitted.
    """
    keys = sorted(table, key=len, reverse=True)
    out, matched, i = [], 0, 0
    while i < len(text):
        for k in keys:
            if text.startswith(k, i):
                target = table[k]
                # Mark matras this table produced, so reordering can move them
                # without touching matras that were already correct.
                out.append(MARK + target if target == I_MATRA else target)
                matched += len(k)
                i += len(k)
                break
        else:
            out.append(text[i])
            i += 1
    return "".join(out), matched


def reorder_matras(text):
    """
    Move each *marked* `ि` from before its consonant cluster to after it, then
    drop the marks.

    Unmarked matras are left alone. Passing unmarked text through this is
    therefore a no-op, which is what makes convert() safe to run over
    already-correct Devanagari.
    """
    return I_MATRA_CLUSTER.sub(r"\1" + I_MATRA, text).replace(MARK, "")


def convert(text, table):
    """Full conversion. Returns (text, coverage) — coverage is the share of
    characters the table actually matched.

    Coverage is returned rather than computed later because a table that
    rewrites 3% of a page is not converting it, and every other measure in
    design §4 would score that non-conversion well by default.
    """
    subbed, matched = substitute(text, table)
    return reorder_matras(subbed), (matched / len(text) if text else 0.0)


def load_table(conn, family_id):
    rows = conn.execute(
        "SELECT source, target FROM mapping_entry WHERE family_id = ?",
        (family_id,)).fetchall()
    return {r[0]: r[1] for r in rows}


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
