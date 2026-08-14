"""
Tests for the Phase 0 extraction and sampling code.

Same spirit as test_audit_calibration.py: these pin behaviour that a later
number depends on. The ones that matter most are the two that check the
refactor changed nothing — measuring was split out of deciding, and if that
moved a single verdict the Phase 1 figure would no longer be reproducible.

Fixtures are the same three SSD documents; tests skip cleanly without them.
No test here reads the corpus.

Run:  python -m pytest tests/ -v
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import draw_annotation_sample as ds  # noqa: E402
import extract_observations as ex  # noqa: E402
import font_audit as fa  # noqa: E402


FIXTURES_DIR = Path(
    os.environ.get("DEVAUDIT_FIXTURES", r"C:\Users\HARDIK\Desktop\docs")
)
PMC_BUDGET = "936a96e9-59da-4654-b9e4-01697b763f38.pdf"
PRESENTATION = "098b86ae-f4b3-457e-8bff-b68eac28eede.pdf"
LATIN_TENDER = "TN_09-EE-NHADB-28_04_2025.pdf"


def _observe(name):
    path = FIXTURES_DIR / name
    if not path.exists():
        pytest.skip(f"fixture not available: {path}")
    return {r["font_name"]: r for r in ex.observe(path, "0" * 64)}


# ---------------------------------------------------------------------------
# The refactor must be invisible. Splitting measurement out of classification
# is only safe if the classifier's answers are byte-identical.
# ---------------------------------------------------------------------------
LEGACY_8BIT_SAMPLE = "xÉÉÊ¶ÉEò ¨É½þÉxÉMÉ®ú{ÉÉÊ±ÉEòÉ " * 20
KRUTI_SAMPLE = "i'kq dY;k.k foHkkx ljdkjh xtV mRrj izns'k dk;kZy; " * 12
ENGLISH_SAMPLE = ("the corporation shall publish the tender notice for the "
                  "work of road water supply in the ward office ") * 8


def test_classifier_agrees_with_itself_whether_or_not_measurements_are_reused():
    for text in (LEGACY_8BIT_SAMPLE, KRUTI_SAMPLE, ENGLISH_SAMPLE, "", "x" * 50):
        assert (fa.classify_font_output(text)
                == fa.classify_font_output(text, fa.measure_font_text(text)))


def test_measurements_reproduce_the_shipped_reason_strings():
    assert fa.classify_font_output(LEGACY_8BIT_SAMPLE).startswith("8bit(")
    assert fa.classify_font_output(KRUTI_SAMPLE).startswith("ascii-remap(k=")
    assert fa.classify_font_output(ENGLISH_SAMPLE) is None


def test_measurement_records_signals_the_classifier_never_reports():
    """A cleared font must still leave its numbers behind, or no threshold can
    ever be re-tuned without re-reading every PDF."""
    m = fa.measure_font_text(ENGLISH_SAMPLE)
    assert fa.classify_font_output(ENGLISH_SAMPLE, m) is None
    assert m["mojibake_ratio"] == 0.0
    assert m["latin_letters"] > 200
    assert "ascii_k_ratio" in m and "symbol_per_1k" in m


def test_structural_signals_are_taken_even_when_the_classifier_defers():
    """The SUSPECT class has no per-font evidence in Phase 1 because the
    classifier returns None as soon as real Devanagari appears. Measurement
    must not inherit that."""
    text = "ेजानवे ारी ्ाा स्थालनक सांस्था कर " * 20
    m = fa.measure_font_text(text)
    assert fa.classify_font_output(text, m) is None    # still defers
    assert m["invalid_matras"] > 0                     # but the count exists
    assert m["invalid_rate_per_1k"] > 0


# ---------------------------------------------------------------------------
# Per-font aggregation
# ---------------------------------------------------------------------------
def test_xrefs_collapse_to_one_row_per_font_name():
    """`all_fonts` stores 'Calibri|0;Calibri|0;Calibri|1' — the same face with
    contradictory flags. One row per name, xrefs counted, is the fix."""
    per = ex.aggregate_fonts([
        {"name": "Calibri", "raw_name": "AAA+Calibri", "embedded": False,
         "has_tounicode": True, "pages": {1, 2}},
        {"name": "Calibri", "raw_name": "BBB+Calibri", "embedded": True,
         "has_tounicode": False, "pages": {2, 7}},
    ])
    assert list(per) == ["Calibri"]
    assert per["Calibri"]["n_xrefs"] == 2
    assert per["Calibri"]["embedded"] == 1
    # One mapped copy does not make the unmapped copy safe.
    assert per["Calibri"]["has_tounicode"] == 0
    assert per["Calibri"]["n_pages_declared"] == 3
    assert per["Calibri"]["first_page"] == 1


def test_page_offsets_map_back_to_the_page_they_came_from():
    chunks = [{"page": 1, "char_start": 0, "len": 10},
              {"page": 4, "char_start": 10, "len": 10},
              {"page": 9, "char_start": 20, "len": 10}]
    assert fa.page_of_offset(chunks, 0) == 1
    assert fa.page_of_offset(chunks, 9) == 1
    assert fa.page_of_offset(chunks, 10) == 4
    assert fa.page_of_offset(chunks, 25) == 9
    assert fa.page_of_offset([], 5) is None


# ---------------------------------------------------------------------------
# Detector labels
# ---------------------------------------------------------------------------
def test_silence_is_labelled_no_evidence_not_correct():
    """The instrument fails toward silence. A label that reads as a positive
    finding would hide exactly that."""
    label, _ = ex.detector_label(fa.measure_font_text(ENGLISH_SAMPLE), None)
    assert label == "NO_EVIDENCE"


def test_too_little_text_abstains():
    label, why = ex.detector_label(fa.measure_font_text("short"), None)
    assert label == "UNDECIDABLE"
    assert why == "too-little-text"


def test_conviction_reasons_map_to_labels():
    m = fa.measure_font_text(LEGACY_8BIT_SAMPLE)
    assert ex.detector_label(m, "8bit(0.76)")[0] == "LEGACY_8BIT"
    assert ex.detector_label(m, "ascii-remap(k=0.182)")[0] == "LEGACY_ASCII"
    assert ex.detector_label(m, "symbol-remap(40/1k)")[0] == "LEGACY_SYMBOL"


# ---------------------------------------------------------------------------
# Excerpts — what the annotator actually reads
# ---------------------------------------------------------------------------
def test_violation_excerpts_are_produced_for_invalid_devanagari():
    """Without these an annotator skimming a page of nearly-right Devanagari
    never sees the two impossible clusters in it."""
    text = "सामान्य मजकूर " * 30 + "ेजानवे ारी" + " आणखी मजकूर " * 20
    chunks = [{"page": 3, "char_start": 0, "len": len(text)}]
    kinds = {e["kind"] for e in ex.choose_excerpts(text, chunks, "seed")}
    assert "violation" in kinds and "head" in kinds


def test_excerpt_draw_is_reproducible_and_not_run_order_dependent():
    text = "काही मजकूर येथे आहे " * 60
    chunks = [{"page": 1, "char_start": 0, "len": len(text)}]
    a = ex.choose_excerpts(text, chunks, "sha:FontA")
    b = ex.choose_excerpts(text, chunks, "sha:FontA")
    c = ex.choose_excerpts(text, chunks, "sha:FontB")
    assert a == b
    assert [e["char_start"] for e in a] != [e["char_start"] for e in c]


def test_excerpt_keys_are_unique():
    """The table's UNIQUE(obs_id, kind, page, char_start) must not be
    violated by two random draws colliding on a short sample."""
    text = "मजकूर " * 45
    chunks = [{"page": 2, "char_start": 0, "len": len(text)}]
    got = ex.choose_excerpts(text, chunks, "s")
    keys = [(e["kind"], e["page"], e["char_start"]) for e in got]
    assert len(keys) == len(set(keys))


def test_empty_font_yields_no_excerpts():
    assert ex.choose_excerpts("   ", [], "s") == []


# ---------------------------------------------------------------------------
# Sampling frame
# ---------------------------------------------------------------------------
def test_strata_split_on_detector_and_on_name_informativeness():
    assert ds.name_axis("DVBW-TTSurekh") == "name_legacy"
    assert ds.name_axis("Calibri") == "name_known_good"
    # The cell Phase 1 could never validate against: a name that says nothing.
    assert ds.name_axis("TT313t00") == "name_uninformative"
    assert ds.name_axis("Z@RAF1C.tmp") == "name_uninformative"


def test_undecidable_observations_are_outside_the_frame():
    rows = [(1, "Calibri", "UNDECIDABLE"), (2, "Calibri", "NO_EVIDENCE")]
    assignments, _ = ds.plan_strata(rows, quotas={("no_evidence",
                                                   "name_known_good"): 5})
    assert [a["obs_id"] for a in assignments] == [2]


def test_small_stratum_is_taken_whole_at_probability_one():
    rows = [(i, "TT313t00", "LEGACY_8BIT") for i in range(3)]
    assignments, summary = ds.plan_strata(
        rows, quotas={("positive", "name_uninformative"): 100})
    assert len(assignments) == 3
    assert all(a["selection_prob"] == 1.0 for a in assignments)
    assert summary[0]["want"] == 100 and summary[0]["took"] == 3


def test_selection_probability_is_recorded_so_the_draw_can_be_reweighted():
    rows = [(i, "TT313t00", "LEGACY_8BIT") for i in range(200)]
    assignments, _ = ds.plan_strata(
        rows, quotas={("positive", "name_uninformative"): 50})
    assert len(assignments) == 50
    assert all(a["selection_prob"] == 0.25 for a in assignments)
    assert all(a["stratum_size"] == 200 for a in assignments)


def test_draw_is_reproducible_from_the_seed():
    rows = [(i, "TT313t00", "LEGACY_8BIT") for i in range(200)]
    q = {("positive", "name_uninformative"): 50}
    first = [a["obs_id"] for a in ds.plan_strata(rows, q, seed=7)[0]]
    again = [a["obs_id"] for a in ds.plan_strata(rows, q, seed=7)[0]]
    other = [a["obs_id"] for a in ds.plan_strata(rows, q, seed=8)[0]]
    assert first == again
    assert first != other


def test_row_order_does_not_change_the_draw():
    """SQLite row order is not guaranteed stable; a sample that depends on it
    is not reproducible."""
    rows = [(i, "TT313t00", "LEGACY_8BIT") for i in range(100)]
    q = {("positive", "name_uninformative"): 20}
    forward = [a["obs_id"] for a in ds.plan_strata(rows, q, seed=3)[0]]
    backward = [a["obs_id"] for a in ds.plan_strata(rows[::-1], q, seed=3)[0]]
    assert forward == backward


# ---------------------------------------------------------------------------
# End to end, on the three fixtures
# ---------------------------------------------------------------------------
def test_legacy_fixture_yields_per_font_rows_with_evidence():
    obs = _observe(PMC_BUDGET)
    assert len(obs) > 1
    assert all(r["signals_version"] for r in obs.values())


def test_suspect_fixture_gets_per_font_evidence_for_the_first_time():
    """Phase 1 can say this document has invalid Devanagari. It cannot say
    which font produced it. This is the row that answers that."""
    obs = _observe(PRESENTATION)
    culprits = [n for n, r in obs.items()
                if r["detector_label"] == "CMAP_INVALID"]
    assert culprits, "no font carries the structural damage"
    assert all(obs[n]["invalid_matras"] > 0 for n in culprits)


def test_clean_fixture_convicts_nothing():
    obs = _observe(LATIN_TENDER)
    assert not [n for n, r in obs.items()
                if r["detector_label"].startswith("LEGACY")
                or r["detector_label"] == "CMAP_INVALID"]


def test_the_myriad_pro_signature_block_still_abstains():
    """Phase 1's one false positive: 154 characters of a digitally-signed
    name block, 7.8% 'k', in Myriad Pro. The sample-size floor caught it and
    must keep catching it."""
    obs = _observe(LATIN_TENDER)
    myriad = [r for n, r in obs.items() if "Myriad" in n]
    if not myriad:
        pytest.skip("fixture no longer contains a Myriad Pro block")
    assert all(r["detector_label"] == "UNDECIDABLE" for r in myriad)


def test_under_sampled_fonts_are_visible_rather_than_silently_abstained():
    """Shree-Dev-0708 in this fixture is declared on 24 pages and contributes
    24 characters to the 8-page sample. The abstention is an artifact of the
    cap, and the pair of numbers is what makes that visible."""
    obs = _observe(PMC_BUDGET)
    thin = [r for r in obs.values()
            if r["sampled_chars"] < fa.PERFONT_MIN_CHARS
            and r["n_pages_declared"] >= 5]
    assert thin, "expected at least one under-sampled font in this fixture"
    assert all(r["detector_label"] == "UNDECIDABLE" for r in thin)
