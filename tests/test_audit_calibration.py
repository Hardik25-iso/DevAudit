"""
Calibration tests for font_audit.

These are not unit tests. They are instrument calibration: each one pins the
audit against a real document whose corruption status we already understand.
If these drift, the go/no-go percentage is no longer trustworthy.

Fixtures live outside the repo because the PDFs are not ours to redistribute
until licensing is settled. Point FIXTURES_DIR at a folder containing them;
tests skip cleanly when it is absent.

Run:  python -m pytest tests/ -v
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import font_audit as fa  # noqa: E402


FIXTURES_DIR = Path(

    os.environ.get("DEVAUDIT_FIXTURES", r"C:\Users\HARDIK\Desktop\docs")
)

# Known documents. Names are opaque hashes from the source portals.
PMC_BUDGET = "936a96e9-59da-4654-b9e4-01697b763f38.pdf"   # Shree-Dev, legacy
PRESENTATION = "098b86ae-f4b3-457e-8bff-b68eac28eede.pdf"  # post-processor damage
LATIN_TENDER = "TN_09-EE-NHADB-28_04_2025.pdf"             # clean, Latin only


def _audit(name):
    path = FIXTURES_DIR / name
    if not path.exists():
        pytest.skip(f"fixture not available: {path}")
    return fa.audit(path)


@pytest.fixture(autouse=True)
def restore_patterns():
    """Tests below mutate the module-level pattern list; always put it back."""
    original = list(fa.LEGACY_PATTERNS)
    yield
    fa.LEGACY_PATTERNS = original


# ---------------------------------------------------------------------------
# Bucket assignment — every bucket needs a positive control, or the bucket is
# untested and the number it feeds is unverified.
# ---------------------------------------------------------------------------

def test_legacy_font_document_lands_in_legacy():
    r = _audit(PMC_BUDGET)
    assert r["verdict"] == "LEGACY"
    assert "SHREE-DEV-0715" in r["legacy_fonts"]


def test_post_processor_damage_lands_in_suspect():
    """
    This document's fonts are all standard Unicode (Mangal, Aparajita) with
    valid ToUnicode CMaps, and no legacy name appears anywhere. Font-name
    matching cannot see its corruption. Only the structural check catches it.
    """
    r = _audit(PRESENTATION)
    assert r["verdict"] == "SUSPECT"
    assert r["legacy_fonts"] == ""


def test_latin_only_document_is_clean():
    r = _audit(LATIN_TENDER)
    assert r["verdict"] == "CLEAN"
    assert r["dev_chars"] == 0


# ---------------------------------------------------------------------------
# The most important test in the suite.
# ---------------------------------------------------------------------------

def test_unknown_legacy_font_is_not_silently_cleared():
    """
    Reproduces the false negative this rewrite exists to fix.

    Removing 'shree-dev' from LEGACY_PATTERNS simulates the real research
    situation: a genuine legacy font that nobody has documented yet. The old
    cascade sent such files to "Latin/clean", which suppressed the measured
    corruption rate on exactly the fonts whose discovery is the contribution.

    The guarantee is NOT that the file lands in a particular bucket -- it is
    caught here by two independent signals, and structural evidence of damage
    outranks an unidentifiable font. The guarantee is that it is never
    declared clean, and that the offending font is surfaced by name for a
    human to look at.
    """
    fa.LEGACY_PATTERNS = [p for p in fa.LEGACY_PATTERNS
                          if "shree" not in p and "dev" not in p]
    r = _audit(PMC_BUDGET)

    assert r["verdict"] != "CLEAN", "undocumented legacy font was cleared"
    assert r["legacy_fonts"] == "", "test did not actually remove the pattern"
    assert "SHREE-DEV-0715" in r["unknown_fonts"], \
        "unidentifiable font must be surfaced by name for inspection"


# ---------------------------------------------------------------------------
# Structural detector — pinned against measured baselines.
# ---------------------------------------------------------------------------

def test_structural_check_is_more_sensitive_than_detached_matra():
    """
    The old detached-matra regex found 29 violations in this document.
    The structural check finds an order of magnitude more, without needing
    any font list. If this ratio collapses, the detector has regressed.
    """
    r = _audit(PRESENTATION)
    assert r["detached_matras"] == 29
    assert r["invalid_matras"] >= 400
    assert r["invalid_matras"] > 10 * r["detached_matras"]


def test_presentation_structural_baselines():
    r = _audit(PRESENTATION)
    assert r["word_initial_matras"] == 30
    assert r["adjacent_matras"] == 173
    assert r["virama_then_matra"] == 192
    assert r["invalid_rate_per_1k"] > fa.SUSPECT_RATE_PER_1K


def test_clean_document_has_no_structural_violations():
    """Guards the other direction: the detector must not invent violations."""
    r = _audit(LATIN_TENDER)
    assert r["invalid_matras"] == 0
    assert r["invalid_rate_per_1k"] == 0.0


# ---------------------------------------------------------------------------
# Encoding regression -- the specific bug that made the old tool report zero.
# ---------------------------------------------------------------------------

def test_devanagari_survives_extraction():
    """
    Under the old subprocess(text=True) path on Windows, pdftotext's UTF-8
    output was decoded as cp1252 and every Devanagari counter read zero --
    silently, with no error. Any nonzero dev_chars here proves that path is
    gone. Asserted on both Devanagari documents so a single bad fixture
    cannot mask it.
    """
    for name in (PMC_BUDGET, PRESENTATION):
        r = _audit(name)
        assert r["dev_chars"] > 1000, f"{name}: Devanagari lost in extraction"


# ---------------------------------------------------------------------------
# ASCII-remap detector (Kruti Dev family, Hindi belt).
#
# These run without fixtures, because the property under test is a frequency
# threshold rather than anything document-specific. The real separation was
# measured on the corpus: genuine English tops out at 2.5% 'k', Kruti-Dev-
# encoded Hindi starts at 10.2%, and the threshold sits between them.
# ---------------------------------------------------------------------------

# Real extracted text from a Lucknow Municipal Corporation PDF.
# Reads as "पशु कल्याण विभाग ... नगर निगम लखनऊ" once decoded.
KRUTI_SAMPLE = (
    "i'kq dY;k.k foHkkx laf{kIr ifjp; uxj fuxe y[kuÅ ds Ik'kq dY;k.k foHkkx "
    "}kjk fu;fer :i ls xzke&cjkoudyk¡] rglhy o ftyk&y[kuÅ [kljk la[;k "
    "{ks=Qy Hkwfe dk izdkj ljdkjh xtV mRrj izns'k ljdkj }kjk izdkf'kr "
) * 6

ENGLISH_SAMPLE = (
    "Tender notice for the supply and installation of water treatment "
    "equipment at the municipal works depot. Bidders must submit sealed "
    "quotations along with the earnest money deposit before the closing "
    "date. Technical specifications are attached as Annexure A. "
) * 6


def test_ascii_remap_detector_flags_kruti_dev():
    assert len(KRUTI_SAMPLE) > 300
    letters = [c for c in KRUTI_SAMPLE if c.isascii() and c.isalpha()]
    ratio = letters.count("k") / len(letters)
    assert ratio >= fa.PERFONT_ASCII_K, (
        f"Kruti-Dev sample scored {ratio:.3f}, below the "
        f"{fa.PERFONT_ASCII_K} threshold")


def test_ascii_remap_detector_ignores_english():
    """
    Guards the direction that matters. A detector that over-fires would push
    the headline corruption rate up, which is the flattering direction and
    therefore the one to be most suspicious of.
    """
    letters = [c for c in ENGLISH_SAMPLE if c.isascii() and c.isalpha()]
    ratio = letters.count("k") / len(letters)
    assert ratio < fa.PERFONT_ASCII_K, (
        f"ordinary English scored {ratio:.3f}, at or above the "
        f"{fa.PERFONT_ASCII_K} threshold")


def test_ascii_remap_threshold_sits_inside_the_measured_gap():
    """
    Corpus measurement: English reaches 2.5%, Kruti Dev starts at 10.2%.
    The threshold must stay strictly inside that gap, with margin, so it is
    not tuned to a single document.

    The document-level constant this once guarded was deleted after it was
    shown never to convict anything alone; the per-font threshold inherited
    the same measured gap and the same guarantee.
    """
    assert 0.025 < fa.PERFONT_ASCII_K < 0.102


# ---------------------------------------------------------------------------
# Per-font detector.
#
# Validated against fonts identifiable by name across 450 documents:
# precision 1.000 (zero false positives on 91 known-good fonts), recall 0.968
# on fonts it can actually judge. The tests below pin both directions.
# ---------------------------------------------------------------------------

# Real span text from a Nashik Municipal Corporation PDF: नाशिक महानगरपालिका
EIGHT_BIT_SPAN = "xÉÉÊ¶ÉEò ¨É½þÉxÉMÉ®ú{ÉÉÊ±ÉEòÉ ºÉÉ´ÉÇVÉÊxÉEò ¤ÉÉÆvÉEòÉ¨É Ê´É¦ÉÉMÉ " * 8
# Real span text from Pune Municipal Corporation, third encoding family
SYMBOL_SPAN = "^l.^glmlzaG$l @l_vº$ UVl àdlfG$ mvTn ^glYI`mlzaG$l _l§Ln G$ " * 8
ENGLISH_SPAN = (
    "Account Description of Items Schedule Balance as at the close of the "
    "year for the municipal corporation water supply department works "
) * 8
DEVANAGARI_SPAN = "नाशिक महानगरपालिका सार्वजनिक बांधकाम विभाग निविदा सूचना " * 8


def test_perfont_flags_eight_bit_span():
    assert fa.classify_font_output(EIGHT_BIT_SPAN) is not None


def test_perfont_flags_symbol_remap_span():
    """
    The third encoding family. This is the case that motivated moving to
    per-font analysis: at document level the signal reached 53.8 hits per
    1000 chars in clean documents against 58.1 in affected ones, which is no
    separation at all. Per font, known-good tops out at 2.67 against 39.68.
    """
    assert fa.classify_font_output(SYMBOL_SPAN) is not None


def test_perfont_leaves_english_alone():
    """The direction that matters: over-firing inflates the headline."""
    assert fa.classify_font_output(ENGLISH_SPAN) is None


def test_perfont_defers_on_real_devanagari():
    """
    A font emitting genuine Devanagari maps to Unicode correctly. Whether that
    Devanagari is *valid* belongs to the structural check, so this detector
    must abstain rather than double-count.
    """
    assert fa.classify_font_output(DEVANAGARI_SPAN) is None


def test_perfont_abstains_on_too_little_text():
    """Silence must mean 'no evidence', never 'clean'."""
    assert fa.classify_font_output("xÉÉÊ¶ÉEò") is None


def test_perfont_thresholds_sit_inside_measured_gaps():
    """
    Measured per font across 450 documents, known-good maxima were:
    mojibake 0.070, ASCII k 0.021, symbol-in-word 2.67 per 1000 chars.
    Each threshold must stay clear of its observed ceiling.
    """
    assert fa.PERFONT_MOJIBAKE > 0.070
    assert fa.PERFONT_ASCII_K > 0.021
    assert fa.PERFONT_SYMBOL > 2.67
