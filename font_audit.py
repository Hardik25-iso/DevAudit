#!/usr/bin/env python3
"""
font_audit.py — Phase 1 triage tool.

Scans a folder of PDFs and sorts each into exactly one of five buckets:

    SCAN          no usable text layer — needs OCR
    LEGACY        known non-Unicode Indic font — text layer is wrong
    SUSPECT       Devanagari present but structurally invalid
    UNCLASSIFIED  embedded font we cannot identify either way
    CLEAN         nothing wrong detected

Why this exists:
  A PDF can have a text layer that extracts "successfully" and is still
  linguistically wrong. Legacy Indic fonts (Shree-Dev, Kruti Dev, DV-TTSurekh,
  Chanakya) map Devanagari glyphs onto ASCII codepoints in VISUAL order rather
  than logical order. No error is raised. This finds those files at scale.

Usage:
    python font_audit.py <folder> [--csv report.csv] [--verbose]

Requires: PyMuPDF  (pip install pymupdf)
  Deliberately NOT poppler. The previous version shelled out to pdfinfo/
  pdffonts/pdftotext with subprocess(text=True), which decodes using the
  locale encoding — cp1252 on Windows. pdftotext emits UTF-8, so Devanagari
  arrived as mojibake and every corruption counter silently read zero.
  PyMuPDF returns str directly, so that class of bug cannot recur.
"""

import argparse
import csv
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF


# ---------------------------------------------------------------------------
# Font name lists
# ---------------------------------------------------------------------------
# Known legacy (non-Unicode) Indic families. Extend as you find more — building
# this list out IS part of the research contribution, which is also why unknown
# fonts go to UNCLASSIFIED rather than being assumed clean.
LEGACY_PATTERNS = [
    "shree-dev", "shree dev", "shreedev",
    "kruti", "krutidev",
    "dv-tt", "dvtt", "surekh", "divyae", "divya",
    "chanakya", "shivaji", "yogeshweb", "yogesh",
    "aksharyogini", "millennium", "walkman",
    "agra", "amruta", "shusha", "ism",
    # --- added from the first corpus run -------------------------------
    # These were surfaced by the UNCLASSIFIED bucket and then confirmed by
    # reading their extracted text, not assumed from the name:
    #   MGShree      -> "ukxij egkuxjikfydk"  (= नागपूर महानगरपालिका, ASCII-remapped)
    #   Sakal Marathi-> "´ÖÖ×ÆüŸÖß®Öã×ÃŸÖÛêú"  (8-bit garbage, 0 Devanagari chars)
    #   DVBW-TTBhima -> same 8-bit garbage
    # The DVBW-TT family was previously caught only when the face name
    # happened to be listed (Surekh, Yogesh), so Bhima and Radhika slipped
    # through. Matching the family prefix fixes the whole set at once.
    "dvbw", "dvot", "dvb-tt",
    "mgshree", "mg-shree", "sakal",
    "bhima", "radhika",
    # Kiran produces corrupted Devanagari rather than ASCII, so it is a
    # CMap-level failure rather than a classic 8-bit font. Listed because the
    # output is wrong either way; flagged here as needing a closer look.
    "kiran",
    # --- second corpus run (407 documents) ------------------------------
    # APS-C-DV-* : ten variants across MHADA, all emitting 8-bit garbage
    # ("‚ã. ‰ãŠ. ‡ãŠã¾ããÃÊã¾ããÞãñ") with zero Devanagari characters.
    "aps-c-dv", "aps-dv", "apsdv",
]

# Fonts we can positively vouch for. A font NOT on this list is not assumed
# guilty — it is assumed *unknown*, which is the whole point of UNCLASSIFIED.
KNOWN_GOOD = [
    # Standard Latin faces
    "arial", "helvetica", "times new roman", "times", "courier", "calibri",
    "cambria", "candara", "consolas", "constantia", "corbel", "georgia",
    "verdana", "tahoma", "trebuchet", "segoe", "garamond", "book antiqua",
    "bookman", "century", "franklin", "futura", "gill sans", "impact",
    "lucida", "palatino", "rockwell", "symbol", "wingdings", "webdings",
    "zapfdingbats", "liberation", "dejavu", "roboto", "open sans", "lato",
    "montserrat", "poppins", "nunito", "raleway", "source sans", "pt sans",
    "ms mincho", "ms gothic", "mincho", "gothic", "batang", "simsun",
    # Added after the first real corpus run: all three were flagged
    # UNCLASSIFIED as ordinary Latin faces simply missing from this list.
    # Keeping the list current is maintenance, not a design flaw -- the
    # detector is meant to fail toward "look at this".
    "microsoft sans serif", "aptos", "adobefangsong", "fangsong",
    "malgun", "meiryo", "yu gothic", "sitka", "selawik", "leelawadee",
    "bahnschrift", "ink free", "javanese", "myanmar", "nirmala ui",
    # Confirmed Latin faces that the corpus run surfaced as unidentified.
    "myriad", "minion", "frutiger", "univers", "optima", "baskerville",
    "gabriola", "angsana", "cordia", "browallia", "adobe devanagari",
    "adobe fan", "adobe song", "adobe ming", "kokila", "raavi", "vrinda",
    # Unicode-correct Indic faces
    "mangal", "aparajita", "nirmala", "kokila", "utsaah", "sanskrit text",
    "arial unicode", "noto sans devanagari", "noto serif devanagari",
    "noto sans", "noto serif", "kohinoor", "shobhika", "annapurna",
    "samyak", "lohit", "gargi", "chandas", "uttara", "sarai", "kalimati",
    "devanagari mt", "devanagari sangam", "itf devanagari", "murty",
]


# ---------------------------------------------------------------------------
# Devanagari structure
# ---------------------------------------------------------------------------
# Consonants, including the nukta-composed set and the extended block.
CONSONANT = r"क-हक़-य़ॸ-ॿ"
# Dependent vowel signs ("matras"). These are the characters that CANNOT stand
# alone — every one of them must attach to a preceding consonant.
MATRA = r"ऺऻा-ौॎॏॢॣ"
VIRAMA = "्"
NUKTA = "़"

DEV_RANGE = re.compile(r"[ऀ-ॿ]")
DEV_DIGIT_RE = re.compile(r"[०-९]")

# The 8-bit legacy signature. A non-Unicode Devanagari font maps glyphs onto
# single bytes, so its text extracts as a dense run of Latin-1 supplement and
# Latin-Extended letters plus stray typographic punctuation:
#   "xÉÉÊ¶ÉEò ¨É½þÉxÉMÉ®ú{ÉÉÊ±ÉEòÉ"  = नाशिक महानगरपालिका
#   "‚ã. ‰ãŠ. ‡ãŠã¾ããÃÊã¾ããÞãñ"
#
# This matters because some producers give fonts deliberately opaque subset
# names (TT274t00, TT2F1t00) that no name pattern can ever match. Detecting
# the output signature catches those, and is the same move as testing
# Devanagari structure rather than trusting the font list.
MOJIBAKE_RANGE = re.compile(r"[¡-ɏ̀-ͯ‘-‰]")
MOJIBAKE_RATIO = 0.20      # of non-whitespace characters
MOJIBAKE_MIN_CHARS = 200   # ignore short fragments

# The core validity test, and the reason it replaces the old detached-matra
# regex: a matra is valid ONLY directly after a consonant (optionally nukta-ed).
# One rule therefore catches every structural failure at once —
#   word-initial matra, matra after another matra, matra after virama,
#   matra after an independent vowel — all of which are impossible in
#   well-formed Devanagari and all of which visual-order corruption produces.
INVALID_MATRA = re.compile(
    f"(?<![{CONSONANT}{NUKTA}])[{MATRA}]"
)

# Kept as separate diagnostics so a failure says *which* way it broke.
WORD_INITIAL_MATRA = re.compile(f"(?<![ऀ-ॿ])[{MATRA}]")
ADJACENT_MATRA = re.compile(f"[{MATRA}][{MATRA}]")
VIRAMA_THEN_MATRA = re.compile(f"{VIRAMA}[{MATRA}]")
DETACHED_MATRA = re.compile(f"\\s[{MATRA}{VIRAMA}]")

# Thresholds. Rate-normalised per 1000 Devanagari characters so a 200-page
# budget is not flagged merely for being long.
SUSPECT_RATE_PER_1K = 2.0   # clean Unicode documents sit at ~0
SUSPECT_MIN_ABS = 5         # ignore noise in very short documents
MIN_DEV_CHARS = 100         # below this, the rate is not meaningful
SCAN_CHARS_PER_PAGE = 25    # under this, there is no usable text layer


def _norm(s):
    """
    Lowercase and strip everything that is not a letter or digit.

    Font names are written inconsistently by the tools that embed them:
    'Book Antiqua' and 'BookAntiqua', 'DV-TTSurekh' and 'DVTTSurekh' are the
    same face. Normalising both sides means one pattern covers every spelling,
    instead of the list needing an entry per punctuation variant. BookAntiqua
    was flagged as unidentified purely because the list said 'book antiqua'.
    """
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _match_any(name, patterns):
    """Substring match, insensitive to case, spaces and punctuation."""
    low = _norm(name)
    return [p for p in patterns if _norm(p) in low]


def _base_name(raw):
    """Strip the 'ABCDEF+' subset prefix that PDF producers prepend."""
    return raw.split("+", 1)[1] if "+" in raw[:8] else raw


def _safe(s):
    """
    Strip unpaired surrogates so the row can be written to a UTF-8 CSV.

    Real government PDFs carry junk in their metadata dictionaries -- the
    first corpus run crashed writing the report, not reading the documents.
    Note the text layer itself was clean of surrogates; this is Producer and
    Creator only.
    """
    if not isinstance(s, str):
        return s
    return s.encode("utf-8", "replace").decode("utf-8")


def collect_fonts(doc):
    """
    Return one record per distinct font in the document.

    We scan every page rather than a sample: font discovery is the research
    contribution, and a legacy font used on one late page is exactly the kind
    of thing that must not be missed.
    """
    seen = {}
    for pno in range(doc.page_count):
        try:
            page_fonts = doc.get_page_fonts(pno)
        except Exception:
            continue
        for f in page_fonts:
            xref, ext, ftype, basefont, _name, encoding = f[:6]
            if xref in seen:
                continue
            # ('null','null') when the key is absent, ('xref', 'N 0 R') present.
            try:
                has_tounicode = doc.xref_get_key(xref, "ToUnicode")[0] != "null"
            except Exception:
                has_tounicode = False
            seen[xref] = {
                "name": _base_name(basefont or ""),
                "raw_name": basefont or "",
                "encoding": encoding or "",
                # ext is '' for non-embedded fonts, 'ttf'/'cff'/... when embedded
                "embedded": bool(ext),
                "type": ftype or "",
                "has_tounicode": has_tounicode,
            }
    return list(seen.values())


def classify_fonts(fonts):
    """
    Split fonts into legacy hits and unidentifiable ones.

    The conjunction that defines 'unidentifiable' is the heart of this tool.
    A legacy Devanagari font embeds as a TrueType subset with WinAnsiEncoding
    and no ToUnicode — byte for byte the same shape as an ordinary embedded
    Latin font. Encoding cannot separate them and neither can ToUnicode.
    Only the name can. So when the name is unknown we must say 'unknown'
    rather than 'clean', or every legacy font missing from LEGACY_PATTERNS
    silently becomes evidence that the problem does not exist.
    """
    legacy, unknown = set(), set()
    for f in fonts:
        name = f["name"]
        if not name:
            continue
        if _match_any(name, LEGACY_PATTERNS):
            legacy.add(name)
            continue
        if _match_any(name, KNOWN_GOOD):
            continue
        # Unknown name. Only worrying when it carries no Unicode mapping —
        # with a ToUnicode CMap the text is at least self-describing.
        if f["embedded"] and not f["has_tounicode"]:
            unknown.add(name)
    return sorted(legacy), sorted(unknown)


def audit(path):
    """Audit one PDF. Returns a flat dict — one row of the report."""
    row = {"file": path.name, "path": str(path),
           "size_mb": round(path.stat().st_size / 1e6, 2)}
    try:
        doc = fitz.open(path)
    except Exception as e:
        row["verdict"] = "ERROR"
        row["error"] = f"{type(e).__name__}: {e}"[:200]
        return row

    if doc.needs_pass:
        row["verdict"] = "ERROR"
        row["error"] = "encrypted"
        doc.close()
        return row

    row["pages"] = doc.page_count
    meta = doc.metadata or {}
    row["producer"] = _safe(str(meta.get("producer") or ""))[:60]
    row["creator"] = _safe(str(meta.get("creator") or ""))[:60]

    fonts = collect_fonts(doc)
    legacy, unknown = classify_fonts(fonts)
    row["n_fonts"] = len(fonts)
    row["fonts_no_unicode"] = sum(1 for f in fonts if not f["has_tounicode"])
    row["legacy_fonts"] = ";".join(legacy)
    row["unknown_fonts"] = ";".join(unknown)

    # Text sample. 30 pages is plenty for triage and keeps large budgets cheap.
    last = min(doc.page_count, 30)
    try:
        text = "".join(doc[i].get_text() for i in range(last))
    except Exception:
        text = ""
    doc.close()

    row["chars"] = len(text)
    row["dev_chars"] = len(DEV_RANGE.findall(text))
    row["dev_digits"] = len(DEV_DIGIT_RE.findall(text))
    row["ascii_digits"] = len(re.findall(r"[0-9]", text))

    row["invalid_matras"] = len(INVALID_MATRA.findall(text))
    row["word_initial_matras"] = len(WORD_INITIAL_MATRA.findall(text))
    row["adjacent_matras"] = len(ADJACENT_MATRA.findall(text))
    row["virama_then_matra"] = len(VIRAMA_THEN_MATRA.findall(text))
    row["detached_matras"] = len(DETACHED_MATRA.findall(text))

    # Mojibake signature: dense non-ASCII Latin with no Devanagari at all, in
    # a document whose fonts are embedded without Unicode mappings. Computed
    # over non-whitespace characters so layout does not dilute it.
    body = "".join(text.split())
    row["mojibake_chars"] = len(MOJIBAKE_RANGE.findall(body))
    row["mojibake_ratio"] = (round(row["mojibake_chars"] / len(body), 3)
                             if body else 0.0)
    row["mojibake_signature"] = int(
        len(body) >= MOJIBAKE_MIN_CHARS
        and row["dev_chars"] == 0
        and row["mojibake_ratio"] >= MOJIBAKE_RATIO
        and row["fonts_no_unicode"] > 0
    )

    if row["dev_chars"] >= MIN_DEV_CHARS:
        row["invalid_rate_per_1k"] = round(
            1000.0 * row["invalid_matras"] / row["dev_chars"], 2)
    else:
        row["invalid_rate_per_1k"] = 0.0

    # --- bucket assignment; first match wins, so buckets stay disjoint ------
    chars_per_page = row["chars"] / max(row["pages"], 1)

    if row["n_fonts"] == 0 or chars_per_page < SCAN_CHARS_PER_PAGE:
        # Fixed from the previous version, which decided this on zero-fonts
        # alone and BEFORE extracting text — so a PDF with fonts but no
        # extractable characters was misfiled as having a text layer.
        verdict = "SCAN"
    elif legacy or row["mojibake_signature"]:
        # Either the font is named in the list, or the extracted text carries
        # the unmistakable 8-bit signature. The second clause is what catches
        # producers who ship deliberately anonymous subset names.
        verdict = "LEGACY"
    elif (row["invalid_rate_per_1k"] >= SUSPECT_RATE_PER_1K
          and row["invalid_matras"] >= SUSPECT_MIN_ABS):
        # Observed corruption outranks an unidentifiable font: this is
        # evidence of damage, not merely absence of proof of safety.
        verdict = "SUSPECT"
    elif unknown:
        verdict = "UNCLASSIFIED"
    else:
        verdict = "CLEAN"

    row["verdict"] = verdict
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("folder")
    ap.add_argument("--csv", default=None, help="write full report to CSV")
    ap.add_argument("--verbose", action="store_true",
                    help="show font names per file")
    args = ap.parse_args()

    folder = Path(args.folder)
    pdfs = sorted(folder.rglob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found under {folder}")
        sys.exit(1)

    rows = []
    for p in pdfs:
        r = audit(p)
        rows.append(r)
        print(f"{r['file'][:44]:46} {str(r.get('pages','?')):>4}p  "
              f"{r['verdict']}")
        if r.get("legacy_fonts"):
            print(f"{'':46}      legacy: {r['legacy_fonts'][:70]}")
        if r.get("unknown_fonts"):
            print(f"{'':46}      unknown: {r['unknown_fonts'][:70]}")
        if args.verbose and r.get("invalid_matras"):
            print(f"{'':46}      invalid matras: {r['invalid_matras']} "
                  f"({r['invalid_rate_per_1k']}/1k dev chars)")

    # --- summary: the number that decides the project ----------------------
    n = len(rows)
    counts = {}
    for b in ("SCAN", "LEGACY", "SUSPECT", "UNCLASSIFIED", "CLEAN", "ERROR"):
        counts[b] = sum(1 for r in rows if r.get("verdict") == b)

    depts = len({r.get("producer", "") for r in rows})
    print("\n" + "=" * 64)
    print(f"{n} documents, {depts} distinct producers")
    for b in ("SCAN", "LEGACY", "SUSPECT", "UNCLASSIFIED", "CLEAN"):
        label = {"SCAN": "no text layer (scan)",
                 "LEGACY": "legacy non-Unicode fonts",
                 "SUSPECT": "suspect (invalid Devanagari)",
                 "UNCLASSIFIED": "unclassified font",
                 "CLEAN": "clean"}[b]
        print(f"  {label:32} {counts[b]:4}  ({100.0*counts[b]/n:5.1f}%)")
    if counts["ERROR"]:
        print(f"  {'errors':32} {counts['ERROR']:4}")
    print("=" * 64)

    # Pre-registered decision rule — written before any data was seen, so the
    # threshold cannot drift to meet whatever the corpus turns out to say.
    ls = 100.0 * (counts["LEGACY"] + counts["SUSPECT"]) / n
    un = 100.0 * counts["UNCLASSIFIED"] / n
    print(f"LEGACY+SUSPECT = {ls:.1f}%   UNCLASSIFIED = {un:.1f}%")
    # ASCII only in console output. A tool about encoding failures has no
    # business emitting mojibake when piped through a cp1252 console.
    if ls >= 15 or (ls >= 5 and un >= 15):
        print("DECISION: GO - the corrupted-text-layer problem is real.")
    elif ls + un < 10:
        print("DECISION: PIVOT - not prevalent enough to build on.")
    else:
        print("DECISION: INSPECT - hand-check 30 UNCLASSIFIED files, or if "
              "too few exist, 30 drawn from LEGACY+SUSPECT.")

    if args.csv:
        keys = sorted({k for r in rows for k in r})
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
        print(f"\nFull report written to {args.csv}")


if __name__ == "__main__":
    main()
