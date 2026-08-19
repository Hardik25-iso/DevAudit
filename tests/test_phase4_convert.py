"""
Tests for the Phase 4 converter.

The applier is tested against a HAND-AUTHORED table, deliberately, and before
any derivation code exists. If the applier were only ever exercised with a
derived table, a bad conversion would be ambiguous between a bad table and a
bad applier and there would be no way to tell which.

The three anchor conversions below are the ones the hand table was checked
against in design §3.1, and `Eò®úhÉä` was held out of its construction.

Pure string work — no corpus, no drive, no database.
Run:  python -m pytest tests/ -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import convert as cv  # noqa: E402
import legacy_families as lf  # noqa: E402

TABLE = cv.MANUAL_TABLE["fam-01-dvttdhruvnor"]


# ---------------------------------------------------------------------------
# 1. the anchors
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("garbage,expected", [
    ("xÉÉÊ¶ÉEò", "नाशिक"),
    ("¨É½þÉxÉMÉ®ú{ÉÉÊ±ÉEòÉ", "महानगरपालिका"),
    ("Eò®úhÉä", "करणे"),                       # held out of the table's construction
    ("xÉÉÊ¶ÉEò ¨É½þÉxÉMÉ®ú{ÉÉÊ±ÉEòÉ", "नाशिक महानगरपालिका"),
])
def test_anchor_conversions(garbage, expected):
    got, _ = cv.convert(garbage, TABLE)
    assert got == expected


def test_the_overview_example_round_trips():
    """
    OVERVIEW.md opens with this exact pair as the statement of the problem.
    If this test fails the project's headline example is wrong.
    """
    got, _ = cv.convert("xÉÉÊ¶ÉEò ¨É½þÉxÉMÉ®ú{ÉÉÊ±ÉEòÉ", TABLE)
    assert got == "नाशिक महानगरपालिका"


# ---------------------------------------------------------------------------
# 2. longest-first substitution
# ---------------------------------------------------------------------------

def test_longest_match_wins():
    """
    'Eò' must be tried before 'E'. Getting this backwards is the failure mode
    that looks like success: 'E' matches, 'ò' falls through unmapped, and the
    output is plausible garbage rather than an obvious error.
    """
    table = {"E": "X", "Eò": "क"}
    out, _ = cv.substitute("Eò", table)
    assert out == "क"


def test_substitution_does_not_reprocess_its_own_output():
    """A rule whose target contains another rule's source must not fire twice."""
    table = {"a": "b", "b": "c"}
    out, _ = cv.substitute("a", table)
    assert out == "b"


def test_unmapped_characters_pass_through():
    """
    Digits, punctuation and English survive unchanged. These documents mix
    scripts constantly — tender numbers and dates sit inside Marathi text —
    so a converter that mangles them is unusable.
    """
    out, _ = cv.substitute("Eò-2024/A", TABLE)
    assert out == "क-2024/A"


def test_coverage_reports_what_was_actually_matched():
    _, cov = cv.convert("Eò", TABLE)
    assert cov == 1.0
    _, cov = cv.convert("XYZW", TABLE)
    assert cov == 0.0


def test_coverage_of_empty_text_is_zero_not_an_error():
    assert cv.convert("", TABLE) == ("", 0.0)


# ---------------------------------------------------------------------------
# 3. matra reordering — the visual-to-logical fix
# ---------------------------------------------------------------------------

def test_i_matra_moves_after_its_consonant():
    assert cv.reorder_matras("ना" + cv.MARK + "िशक") == "नाशिक"


def test_i_matra_moves_after_a_whole_conjunct_cluster():
    """
    'िक्ष' must become 'क्षि', never 'किष'. Moving the matra after only the
    first consonant splits the conjunct and produces structurally invalid
    Devanagari — which the Phase 1 checker would then flag, correctly, as a
    failure of this converter.
    """
    assert cv.reorder_matras(cv.MARK + "िक्ष") == "क्षि"


def test_reordering_leaves_unmarked_text_alone():
    """
    The negative control, in miniature — and the test that caught a real bug.

    A blind regex over any `ि` next to a consonant turned the CORRECT 'नाशिक'
    into 'नाशकि', which would mean running the converter over clean documents
    corrupted them. Only matras the table itself emitted carry a mark, so
    already-correct text passes through untouched.
    """
    for good in ["नाशिक", "महानगरपालिका", "करणे", "क्षि"]:
        assert cv.reorder_matras(good) == good


def test_converting_clean_devanagari_is_a_no_op():
    """The same control at the level callers actually use."""
    for good in ["नाशिक महानगरपालिका", "क्षि", "पाणीपुरवठा विभाग"]:
        got, cov = cv.convert(good, TABLE)
        assert got == good
        assert cov == 0.0


def test_reordering_is_idempotent():
    once = cv.reorder_matras("ना" + cv.MARK + "िशक")
    assert cv.reorder_matras(once) == once


def test_marker_never_survives_conversion():
    """A leaked private-use codepoint would corrupt every downstream measure."""
    got, _ = cv.convert("xÉÉÊ¶ÉEò ¨É½þÉxÉMÉ®ú{ÉÉÊ±ÉEòÉ", TABLE)
    assert cv.MARK not in got


def test_other_matras_are_untouched():
    """Only `ि` is stored in visual order. Moving any other matra would
    corrupt text that was correct."""
    for s in ["को", "के", "का", "कु"]:
        assert cv.reorder_matras(s) == s


# ---------------------------------------------------------------------------
# 4. family signatures
# ---------------------------------------------------------------------------

def test_signature_ignores_digits_and_whitespace():
    a = lf.signature("Eò®úhÉä" * 20)
    b = lf.signature("Eò®úhÉä 123 \n " * 20)
    assert a == b


def test_signature_abstains_on_short_text():
    """Below the floor a frequency profile is noise, and a family assignment
    made from noise would key a mapping table to the wrong encoding."""
    assert lf.signature("Eò") is None


def test_same_encoding_scores_higher_than_different_encoding():
    same = lf.cosine(lf.signature("xÉÉÊ¶ÉEò¨É½þÉxÉMÉ®ú" * 12),
                     lf.signature("Eò®úhÉä{ÉÉÊ±ÉEòÉ" * 12))
    diff = lf.cosine(lf.signature("xÉÉÊ¶ÉEò¨É½þÉxÉMÉ®ú" * 12),
                     lf.signature("i'kq dY;k.k foHkkx" * 12))
    assert same > diff


def test_cosine_of_identical_signatures_is_one():
    s = lf.signature("Eò®úhÉä" * 20)
    assert lf.cosine(s, s) == pytest.approx(1.0)


def test_cmap_invalid_is_not_convertible():
    """
    Those documents emit real Devanagari in impossible order — a reordering
    problem, not an encoding one. Including them would key a substitution table
    to text that needs no substitution (design §5).
    """
    assert "CMAP_INVALID" not in lf.CONVERTIBLE
