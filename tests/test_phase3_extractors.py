"""
Tests for the Phase 3 extractor benchmark.

Same spirit as the Phase 0 and Phase 1 tests: these pin behaviour a later
number depends on. Three groups matter most.

1. `measure()` reproduces the Phase 1 battery EXACTLY. If it drifts, Phase 3
   cannot be compared against Phase 1, and that comparison is most of the
   point of running this phase at all.
2. The loss metrics catch what the Phase 1 battery cannot see. The pilot found
   pdftotext scoring 0.001 mojibake on pages that were 43.7% U+FFFD; a
   regression there would silently restore the trap.
3. pdftotext is decoded as UTF-8 and never with the locale encoding. This
   project has already shipped that bug once.

Most tests are pure string work and need neither the corpus nor the drive.
Run:  python -m pytest tests/ -v
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import evaluate_extractors as ev  # noqa: E402
import extractors as ex  # noqa: E402
import font_audit as fa  # noqa: E402


# Real strings from the corpus. The first is Nashik 8-bit legacy, the second
# its correct rendering, the third Kruti Dev, the fourth structurally invalid
# Devanagari. Taken from docs/OVERVIEW.md so they stay in step with the prose.
LEGACY_8BIT = "xÉÉÊ¶ÉEò ¨É½þÉxÉMÉ®ú{ÉÉÊ±ÉEòÉ " * 12
CORRECT_DEVA = "नाशिक महानगरपालिका " * 20
LEGACY_ASCII = "i'kq dY;k.k foHkkx " * 20
ENGLISH = "the tender notice for the municipal corporation of the city " * 6


# ---------------------------------------------------------------------------
# 1. the Phase 1 battery must not drift
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [LEGACY_8BIT, CORRECT_DEVA, LEGACY_ASCII,
                                  ENGLISH, "", "   ", "123 456"])
def test_measure_reproduces_phase1_battery(text):
    """
    Every shared signal is byte-identical to font_audit.measure_font_text().

    This is the check that matters. extractors.measure() adds the loss group
    on top of the Phase 1 battery; it must never redefine any part of it.
    """
    mine = ex.measure(text)
    theirs = fa.measure_font_text(text)
    for k in ("dev_chars", "latin_letters", "n_tokens", "mojibake_ratio",
              "ascii_k_ratio", "symbol_per_1k", "english_ratio",
              "invalid_matras", "invalid_rate_per_1k",
              "invalid_matras_nospace"):
        assert mine[k] == theirs[k], f"{k} drifted from the Phase 1 definition"


def test_legacy_8bit_still_trips_mojibake():
    """The Phase 1 threshold still fires on Phase 1's own example."""
    assert ex.measure(LEGACY_8BIT)["mojibake_ratio"] >= fa.PERFONT_MOJIBAKE


def test_correct_devanagari_is_quiet():
    m = ex.measure(CORRECT_DEVA)
    assert m["mojibake_ratio"] < fa.PERFONT_MOJIBAKE
    assert m["dev_share"] > 0.9
    assert ex.loss_verdict(m) is None


# ---------------------------------------------------------------------------
# 2. loss — the group Phase 1 has no equivalent of
# ---------------------------------------------------------------------------

def test_replacement_chars_are_invisible_to_the_phase1_battery():
    """
    The trap, pinned. A page of U+FFFD scores clean on every Phase 1 signal
    while being totally unusable, which is why loss exists and why it gates.
    """
    m = ex.measure("�" * 500)
    assert m["mojibake_ratio"] == 0.0          # Phase 1 sees nothing
    assert m["ascii_k_ratio"] == 0.0
    assert m["invalid_rate_per_1k"] == 0.0
    assert m["replacement_ratio"] == 1.0       # loss sees it
    assert ex.loss_verdict(m).startswith("replacement")


def test_empty_output_fails_loss_rather_than_scoring_clean():
    m = ex.measure("")
    assert m["mojibake_ratio"] == 0.0
    assert ex.loss_verdict(m).startswith("empty")


def test_loss_gate_boundaries():
    just_under = ex.measure("�" * 4 + "a" * 96)     # 0.04
    just_over = ex.measure("�" * 6 + "a" * 94)      # 0.06
    assert ex.loss_verdict(just_under) is None
    assert ex.loss_verdict(just_over).startswith("replacement")


def test_control_characters_are_caught():
    m = ex.measure("\x00\x01\x02" * 40 + "abc" * 30)
    assert ex.loss_verdict(m).startswith("control")


def test_loss_verdict_returns_none_not_false():
    """
    Silence means 'no evidence of loss', never 'good' — the same convention as
    classify_font_output(). A caller treating the return as a boolean would
    invert the meaning, so the sentinel is pinned.
    """
    assert ex.loss_verdict(ex.measure(ENGLISH)) is None


# ---------------------------------------------------------------------------
# 3. hashing — separating 'wrong characters' from 'wrong order'
# ---------------------------------------------------------------------------

def test_whitespace_does_not_change_either_hash():
    a = ex.measure("नाशिक महानगरपालिका")
    b = ex.measure("नाशिक   महानगर\nपालिका")
    assert a["text_hash"] == b["text_hash"]
    assert a["bag_hash"] == b["bag_hash"]


def test_reordering_changes_text_hash_but_not_bag_hash():
    """This is what makes a reading-order defect distinguishable from a
    content disagreement (§3.4)."""
    a = ex.measure("जानेवारी")
    b = ex.measure("जानवे ारी")     # the CMAP_INVALID example from OVERVIEW.md
    assert a["text_hash"] != b["text_hash"]
    assert a["bag_hash"] == b["bag_hash"]


def test_different_content_changes_both():
    a = ex.measure(CORRECT_DEVA)
    b = ex.measure(LEGACY_8BIT)
    assert a["text_hash"] != b["text_hash"]
    assert a["bag_hash"] != b["bag_hash"]


# ---------------------------------------------------------------------------
# 4. script concordance (§3.2)
# ---------------------------------------------------------------------------

def _row(text):
    """A dict standing in for an extraction row; classify only reads these."""
    return ex.measure(text)


def test_concordance_flags_the_legacy_failure():
    """Page renders Devanagari, text layer emits Latin-1: the whole point."""
    assert ev.classify_concordance(_row(LEGACY_8BIT),
                                   _row(CORRECT_DEVA)) == "script_mismatch"


def test_concordance_accepts_agreement():
    assert ev.classify_concordance(_row(CORRECT_DEVA),
                                   _row(CORRECT_DEVA)) == "match"


def test_concordance_accepts_genuinely_english_pages():
    """
    Pune Metro publishes in English. If an English page scored as a mismatch
    the check would fire on a third of the corpus for no reason — this is the
    negative control that the pilot's 0/10 on Pune Metro demonstrated.
    """
    assert ev.classify_concordance(_row(ENGLISH), _row(ENGLISH)) == "match"


def test_concordance_defers_when_the_arm_lost_the_text():
    """A page with no text is a loss result, not a script result. Without
    this, silence would score as a script match and flatter the arm."""
    assert ev.classify_concordance(_row(""), _row(CORRECT_DEVA)) == "loss"


def test_concordance_defers_without_a_reference():
    assert ev.classify_concordance(_row(CORRECT_DEVA), None) == "no_reference"


def test_concordance_defers_when_ocr_itself_failed():
    assert ev.classify_concordance(_row(CORRECT_DEVA), _row("")) == "no_reference"


def test_script_excess_fires_in_the_reverse_direction():
    """A check that can only fire the way you expect is not a check."""
    assert ev.classify_concordance(_row(CORRECT_DEVA),
                                   _row(ENGLISH)) == "script_excess"


# ---------------------------------------------------------------------------
# 5. pdftotext decoding — the bug this project has already shipped once
# ---------------------------------------------------------------------------

def test_pdftotext_is_available():
    if not _have_pdftotext():
        pytest.skip("pdftotext not on PATH")


def _have_pdftotext():
    try:
        subprocess.run(["pdftotext", "-v"], capture_output=True, timeout=20)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def test_pdftotext_adapter_never_passes_text_true():
    """
    subprocess(text=True) decodes with the locale encoding — cp1252 here — so
    UTF-8 Devanagari arrives as mojibake and every corruption counter reads
    zero. requirements.txt records this burning the project once. Pinned by
    reading the source, because the failure is invisible in the return value.
    """
    src = Path(__file__).resolve().parents[1].joinpath("extractors.py").read_text(
        encoding="utf-8")
    fn = src.split("def _pdftotext")[1].split("def _pymupdf")[0]
    # Drop the docstring: it explains the bug by name, so scanning the whole
    # function body would match the warning against the bug rather than the bug.
    code = fn.split('"""')[2]
    assert "text=True" not in code
    assert "encoding=" not in code
    assert 'decode("utf-8"' in code


def test_ocr_adapter_fails_loudly_when_tesseract_is_absent(monkeypatch, tmp_path):
    """
    A missing OCR binary must raise, never return "". An empty OCR result would
    read as 'no Devanagari on this page', which is the exact answer the
    concordance check exists to establish — so a silent failure would turn
    every page into a false 'match'.
    """
    import config
    monkeypatch.setattr(config, "TESSERACT_EXE", tmp_path / "nope.exe")
    with pytest.raises(RuntimeError, match="tesseract not found"):
        ex.ARMS["ocr"](tmp_path / "x.pdf", [1])


# ---------------------------------------------------------------------------
# 6. reporting helpers
# ---------------------------------------------------------------------------

def test_macro_and_pooled_diverge_when_bodies_are_unbalanced():
    """
    The reason both figures are always printed. One body with 100 pages all
    failing and one with 2 pages all passing: pooled says 98% fail, macro says
    50%. Sampling has produced a wrong answer three times in this project.
    """
    macro, pooled = ev.macro_pooled({"big": 100, "small": 0},
                                    {"big": 100, "small": 2})
    assert macro == pytest.approx(0.5)
    assert pooled == pytest.approx(100 / 102)


def test_macro_pooled_handles_empty_state():
    assert ev.macro_pooled({}, {}) == (None, None)


def test_macro_pooled_ignores_bodies_with_no_denominator():
    macro, pooled = ev.macro_pooled({"a": 1}, {"a": 2, "b": 0})
    assert macro == pytest.approx(0.5)
    assert pooled == pytest.approx(0.5)
