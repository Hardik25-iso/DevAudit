#!/usr/bin/env python3
"""
extractors.py — one adapter per extraction tool, behind a uniform interface.

Five arms (docs/phase3-design.md §4). Each takes a path and a page range and
returns {page_number: text}. Nothing here decides anything; measuring and
deciding are separate jobs, the same split font_audit.py already makes between
measure_font_text() and classify_font_output().

    from extractors import ARMS, extract, measure
    pages = extract("pdftotext", path, range(1, 6))
    row   = measure(pages[1])

Adapters raise on failure rather than returning empty text. An arm that
crashes on a document is a *result* -- a library that dies on 5% of government
PDFs has told you something -- and silently returning "" would file that result
under "this page has no text", which is a different finding entirely.
"""

import hashlib
import re
import subprocess
import tempfile
import time
from pathlib import Path

import config
import font_audit as fa

# Government PDFs include some pathological files. Without a per-arm timeout a
# single one stalls a corpus pass that takes an hour to reach it.
TIMEOUT_S = 120

# C0 controls minus the three that legitimately appear in extracted text.
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f]")
REPLACEMENT_RE = re.compile("�")


# ---------------------------------------------------------------------------
# adapters
# ---------------------------------------------------------------------------

def _pdftotext(path, pages):
    """
    Poppler, via subprocess.

    Bytes, decoded UTF-8 explicitly. NOT subprocess(text=True): that decodes
    with the locale encoding, cp1252 on this machine, so Devanagari arrives as
    mojibake and every corruption counter silently reads zero. This project has
    already shipped that bug once; requirements.txt records it and
    tests/test_phase3_extractors.py pins it.
    """
    lo, hi = min(pages), max(pages)
    out = subprocess.run(
        ["pdftotext", "-f", str(lo), "-l", str(hi), str(path), "-"],
        capture_output=True, timeout=TIMEOUT_S)
    if out.returncode != 0 and not out.stdout:
        raise RuntimeError(f"pdftotext exit {out.returncode}: "
                           f"{out.stderr.decode('utf-8', 'replace')[:200]}")
    # Poppler separates pages with a form feed. The final page carries a
    # trailing one, so the split yields a spurious empty tail.
    chunks = out.stdout.decode("utf-8", "replace").split("\f")
    return {p: chunks[i] if i < len(chunks) else ""
            for i, p in enumerate(range(lo, hi + 1))}


def _pymupdf(path, pages):
    """The incumbent -- every Phase 1 and Phase 2 number came from this call."""
    import fitz
    doc = fitz.open(str(path))
    try:
        return {p: doc[p - 1].get_text()
                for p in pages if p - 1 < doc.page_count}
    finally:
        doc.close()


def _pdfplumber(path, pages):
    import pdfplumber
    with pdfplumber.open(str(path)) as pdf:
        return {p: (pdf.pages[p - 1].extract_text() or "")
                for p in pages if p - 1 < len(pdf.pages)}


def _pypdf(path, pages):
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    return {p: (reader.pages[p - 1].extract_text() or "")
            for p in pages if p - 1 < len(reader.pages)}


def _ocr(path, pages):
    """
    Render the page and read the picture — the only arm that does not touch
    the text layer, and therefore the only possible reference for §3.2.

    Rendering is PyMuPDF's job here, which makes OCR dependent on PyMuPDF. That
    is fine: rendering and text extraction are different code paths, and a
    rendering bug shows up as a blank or garbled *image*, not as text that
    agrees with PyMuPDF's own extraction. The two arms cannot collude.
    """
    import fitz
    exe = config.TESSERACT_EXE
    if not exe.exists():
        raise RuntimeError(f"tesseract not found at {exe}")

    env_prefix = str(config.TESSDATA_DIR)
    out = {}
    doc = fitz.open(str(path))
    try:
        with tempfile.TemporaryDirectory() as td:
            for p in pages:
                if p - 1 >= doc.page_count:
                    continue
                png = Path(td) / f"p{p}.png"
                doc[p - 1].get_pixmap(dpi=config.OCR_DPI).save(str(png))
                r = subprocess.run(
                    [str(exe), str(png), "-", "-l", config.OCR_LANGS,
                     "--tessdata-dir", env_prefix],
                    capture_output=True, timeout=TIMEOUT_S)
                if r.returncode != 0:
                    raise RuntimeError(
                        f"tesseract exit {r.returncode}: "
                        f"{r.stderr.decode('utf-8', 'replace')[:200]}")
                out[p] = r.stdout.decode("utf-8", "replace")
    finally:
        doc.close()
    return out


ARMS = {
    "pdftotext": _pdftotext,
    "pymupdf": _pymupdf,
    "pdfplumber": _pdfplumber,
    "pypdf": _pypdf,
    "ocr": _ocr,
}

# Arms that can attribute text to a font. Only these support the secondary
# per-font comparison against the 434 labels (design §2.1); the rest are
# page-grained and nothing can be done about it.
FONT_ATTRIBUTING = ("pymupdf", "pdfplumber")


def extract(arm, path, pages):
    """Run one arm. Returns ({page: text}, elapsed_ms). Raises on failure."""
    t0 = time.perf_counter()
    text = ARMS[arm](Path(path), list(pages))
    return text, int((time.perf_counter() - t0) * 1000)


# ---------------------------------------------------------------------------
# measurement
# ---------------------------------------------------------------------------

def measure(text):
    """
    Every signal for one page of one arm's output.

    The Phase 1 battery is taken verbatim from font_audit.measure_font_text()
    rather than reimplemented. If these were redefined here, Phase 3 could not
    be compared against Phase 1 at all, and that comparison is most of the
    point of running this.

    What is added is the loss group, which Phase 1 has no equivalent of and
    which is the reason this phase needed a new metric: pdftotext scores 0.001
    mojibake on documents where 43.7% of its output is U+FFFD, and no signal
    this project owns can see that.
    """
    body = re.sub(r"\s+", "", text)
    n = len(body)
    m = fa.measure_font_text(text)

    n_repl = len(REPLACEMENT_RE.findall(body))
    n_ctrl = len(CONTROL_RE.findall(body))

    row = {
        "n_chars": n,
        "n_chars_raw": len(text),
        "replacement_chars": n_repl,
        "control_chars": n_ctrl,
        "replacement_ratio": n_repl / n if n else 0.0,
        "control_ratio": n_ctrl / n if n else 0.0,
        # dev_share, not dev_chars, is what §3.2 compares across arms: an OCR
        # page and a text-layer page differ in length, so only the proportion
        # is comparable between them.
        "dev_share": m["dev_chars"] / n if n else 0.0,
        # Whitespace-stripped, so two arms agreeing on characters but differing
        # on spacing hash identically. That makes "did these arms produce the
        # same output?" a GROUP BY instead of a pairwise comparison, and it
        # keeps the encoding question separate from the spacing question --
        # the same trick invalid_matras_nospace uses.
        "text_hash": hashlib.sha256(body.encode("utf-8", "replace")).hexdigest()[:16],
        # Sorted characters: equal bag with unequal text means same characters,
        # different order -- which is a reading-order defect and belongs to the
        # extractor, not the font (phase0-schema.md §4.3).
        "bag_hash": hashlib.sha256(
            "".join(sorted(body)).encode("utf-8", "replace")).hexdigest()[:16],
        "text_sample": text[:600],
    }
    for k in ("dev_chars", "latin_letters", "n_tokens", "mojibake_ratio",
              "ascii_k_ratio", "symbol_per_1k", "english_ratio",
              "invalid_matras", "invalid_rate_per_1k", "invalid_matras_nospace"):
        row[k] = m[k]
    return row


# ---------------------------------------------------------------------------
# the loss gate (design §3.5)
# ---------------------------------------------------------------------------
#
# Chosen from the pilot in design §2.3: clean pages measured 0.001 replacement
# ratio and failing pages 0.437, so 0.05 is 50x the clean baseline and about a
# ninth of the observed failure. That is a wide empty gap rather than an edge.
#
# Asserted from 40 documents, not established -- which is why
# evaluate_extractors.py --sweep reports it, exactly as evaluate.py --sweep
# reports the Phase 1 thresholds. If the distribution is continuous rather than
# bimodal, the gate is wrong and the sweep is what says so.
LOSS_MIN_CHARS = fa.SCAN_CHARS_PER_PAGE   # 25; Phase 1's "no usable text layer"
LOSS_MAX_REPLACEMENT = 0.05
LOSS_MAX_CONTROL = 0.05


def loss_verdict(row, min_chars=LOSS_MIN_CHARS,
                 max_replacement=LOSS_MAX_REPLACEMENT,
                 max_control=LOSS_MAX_CONTROL):
    """
    Did this arm produce usable text on this page? Returns a reason or None.

    None means "passed", never "good" -- the same convention as
    classify_font_output(), where silence is the absence of evidence rather
    than a clean bill of health.
    """
    if row["n_chars"] < min_chars:
        return f"empty({row['n_chars']})"
    if row["replacement_ratio"] >= max_replacement:
        return f"replacement({row['replacement_ratio']:.3f})"
    if row["control_ratio"] >= max_control:
        return f"control({row['control_ratio']:.3f})"
    return None
