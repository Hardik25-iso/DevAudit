"""
Tests for the evaluation statistics.

These pin arithmetic that a headline number will rest on. A wrong kappa or a
missing finite-population correction does not crash — it prints a plausible
figure that is wrong, which is the failure mode this project keeps finding in
its own instrument.

No database, no PDFs, no network.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import evaluate as ev  # noqa: E402


# ---------------------------------------------------------------------------
# Cohen's kappa
# ---------------------------------------------------------------------------
def test_perfect_agreement_is_one():
    pairs = [("A", "A")] * 5 + [("B", "B")] * 5
    assert ev.cohen_kappa(pairs) == 1.0


def test_chance_agreement_is_about_zero():
    """Two labellers splitting 50/50 at random agree half the time and have
    learned nothing; kappa must say so where raw agreement says 50%."""
    pairs = [("A", "A"), ("A", "B"), ("B", "A"), ("B", "B")]
    assert abs(ev.cohen_kappa(pairs)) < 1e-9


def test_dominant_class_does_not_inflate_kappa():
    """The reason kappa is used at all: 90% raw agreement on a 90/10 split is
    what you get for never reading the text."""
    pairs = [("A", "A")] * 90 + [("A", "B")] * 5 + [("B", "A")] * 5
    raw = sum(1 for a, b in pairs if a == b) / len(pairs)
    assert raw == 0.9
    assert ev.cohen_kappa(pairs) < 0.5


def test_single_label_throughout_is_undefined_not_perfect():
    """Both passes said CORRECT to everything. There is no variation to agree
    about — reporting 1.0 here would claim reliability nobody demonstrated."""
    assert ev.cohen_kappa([("CORRECT", "CORRECT")] * 20) is None


def test_no_labels_yet():
    assert ev.cohen_kappa([]) is None


# ---------------------------------------------------------------------------
# Precision / recall
# ---------------------------------------------------------------------------
def test_prf_basic():
    p, r, f1 = ev.prf(tp=8, fp=2, fn=4)
    assert p == 0.8
    assert abs(r - 8 / 12) < 1e-9
    assert abs(f1 - 2 * 0.8 * (8 / 12) / (0.8 + 8 / 12)) < 1e-9


def test_prf_never_divides_by_zero():
    assert ev.prf(0, 0, 0) == (0.0, 0.0, 0.0)
    assert ev.prf(0, 5, 0)[0] == 0.0


# ---------------------------------------------------------------------------
# Stratified estimate — the step from "we labelled 434" to a corpus rate
# ---------------------------------------------------------------------------
def test_strata_are_weighted_by_frame_share_not_by_draw():
    """The draw over-samples the small stratum on purpose. Reading the labelled
    proportion as the corpus proportion is the PMC mistake in a new costume:
    here it would say 50%, and the answer is 10%."""
    strata = [(900, 100, 0), (100, 100, 100)]   # big clean, small all-positive
    point, _ = ev.stratified_estimate(strata)
    assert abs(point - 0.10) < 1e-9

    naive = sum(pos for _, _, pos in strata) / sum(n for _, n, _ in strata)
    assert naive == 0.5


def test_fully_labelled_stratum_has_no_sampling_error():
    """Label every member of the frame and the only uncertainty left is none.
    Without the finite-population correction this reports an interval."""
    point, se = ev.stratified_estimate([(50, 50, 25)])
    assert point == 0.5
    assert se == 0.0


def test_smaller_sample_gives_a_wider_interval():
    _, tight = ev.stratified_estimate([(10000, 400, 200)])
    _, loose = ev.stratified_estimate([(10000, 40, 20)])
    assert loose > tight


def test_empty_frame_does_not_divide_by_zero():
    assert ev.stratified_estimate([]) == (0.0, 0.0)
    assert ev.stratified_estimate([(100, 0, 0)]) == (0.0, 0.0)
