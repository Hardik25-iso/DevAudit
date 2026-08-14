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
    "tw cen", "berlin sans", "castellar", "copperplate", "eras", "perpetua",
    # Confirmed genuine after classifying the unidentified set by output.
    # These three were the only real font names among 68; the rest were
    # auto-generated subset identifiers, which are deliberately not listed
    # here because the same string means a different font in another PDF.
    "algerian", "mv boli", "mvboli", "romant",
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

# Hindi-belt ASCII remapping (Kruti Dev and relatives).
#
# The signature above catches Marathi legacy fonts, which map onto the Latin-1
# supplement. Kruti-Dev-style Hindi fonts map onto *plain ASCII*, so they carry
# no high bytes at all and slip past it:
#
#   "xzke&cjkoudyk¡] rglhy o ftyk&y[kuÅ"   = ग्राम...तहसील व जिला-लखनऊ
#   "ljdkjh xtV] mRrj izns'k"              = सरकारी गजट, उत्तर प्रदेश
#   "i'kq dY;k.k foHkkx"                   = पशु कल्याण विभाग
#
# The detector exploits the fact that Kruti Dev is a *fixed, known* mapping
# rather than arbitrary noise. It maps ASCII 'k' onto the Devanagari sign ा
# (U+093E), which is the single most frequent character in written Hindi. Any
# text encoded this way therefore inherits ा's frequency onto the letter 'k' —
# a distribution no English text has, since 'k' is one of English's rarest
# letters at roughly 0.8%.
#
# Measured across the corpus, restricted to documents with no real Devanagari:
#
#   genuine English (tender tables, policy PDFs)   median 0.3%, max 2.5%
#   Kruti-Dev-encoded Hindi                        10.2% to 21.7%
#
# Every one of the 12 documents above 4% was verified by eye as ASCII-remapped
# Devanagari; none was English. The threshold sits in the middle of a gap with
# roughly 2x margin on each side, so it is not finely tuned.
#
# Two weaker discriminators were measured first and rejected: vowel ratio
# (corrupt 0.088-0.185 vs clean down to 0.192) and punctuation inside words
# (corrupt to 46/1000 vs clean to 34/1000). Both overlap legitimate text.
#
# The thresholds themselves now live with the per-font detector below
# (PERFONT_MOJIBAKE, PERFONT_ASCII_K). The document-level versions were
# removed once measurement showed they never convicted anything alone.
DEVANAGARI_AA_ASCII = "k"   # Kruti Dev: 'k' -> ा (U+093E)

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


# ---------------------------------------------------------------------------
# Per-font output analysis
# ---------------------------------------------------------------------------
# PyMuPDF tags every text span with the font that rendered it, so text can be
# accumulated per font and each font judged on its own output instead of the
# document's. That matters because a document mixes fonts: a legacy Devanagari
# face sitting beside ordinary English headers produces a blended signal that
# sits between the two populations and trips no threshold.
#
# The same measurements, taken per font rather than per document, separate
# cleanly. Measured across 450 documents, using fonts identifiable BY NAME as
# labelled ground truth (41 legacy, 53 known-good):
#
#                      known-good max      legacy
#   mojibake ratio           0.070         median 0.684
#   ASCII 'k' frequency      0.021         p90    0.182
#   symbol-inside-word       2.67 /1000    39.68 /1000
#
# The third of those is the payoff. At document level that signal was useless
# — clean documents reached 53.8 hits per 1000 characters against 58.1 for
# affected ones — and was rejected twice for exactly that reason. Per font it
# separates by a factor of 15, because it is no longer diluted by whatever
# else the document contains.
#
# Thresholds sit well inside each gap rather than at its edge.
PERFONT_MAX_PAGES = 8
PERFONT_CAP_CHARS = 2500     # per font; enough to judge, bounded for speed
PERFONT_MIN_CHARS = 200      # below this a font cannot be judged
# The 'k' test needs far more text than the others. A digital-signature block
# rendered in Myriad Pro -- "Vihar Ashok Bodke Digitally signed by ..." -- hit
# 7.8% 'k' across 154 characters purely because Indian personal names are
# dense in that letter. Genuine Kruti-Dev documents run to thousands of
# characters, so demanding a real sample costs nothing and removes a whole
# class of false positive that short signature blocks would otherwise create.
PERFONT_ASCII_MIN_LETTERS = 200
PERFONT_MOJIBAKE = 0.15      # good max 0.070
PERFONT_ASCII_K = 0.05       # good max 0.021
PERFONT_SYMBOL = 10.0        # good max 2.67
PERFONT_ENGLISH = 0.15       # secondary gate on the symbol rule only

# Symbols that legacy encodings map Devanagari onto, appearing *between*
# letters. Ordinary text puts '$' before digits, not inside words.
SYMBOL_IN_WORD = re.compile(r"[A-Za-z][$«»§°º¤¥][A-Za-z]")

# A small stop-word list, used only to confirm that text claiming to be Latin
# actually reads as English before the symbol rule clears it.
COMMON_ENGLISH = set("""
the of and to in for a is on at by with as from or be this that shall will not
are was were which any such other all no notice tender date name department
office corporation municipal work works city road water public list sr page
total amount rate item description year month day time signature government
india state district ward zone plan report annexure form application account
balance schedule contents table
""".split())


def collect_font_spans(doc, max_pages=PERFONT_MAX_PAGES):
    """
    Accumulate rendered text per font, capped for speed, keeping track of where
    each piece came from.

    Returns {name: {"text", "pages", "chunks"}}, where `chunks` records the
    page and the offset within `text` of every span appended. Annotation needs
    that: an excerpt without a page number cannot be checked against the
    rendered document, and adjudication is where page numbers get earned.
    """
    per = {}
    for pno in range(min(doc.page_count, max_pages)):
        try:
            page = doc[pno].get_text("dict")
        except Exception:
            continue
        for block in page.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    name = _base_name(span.get("font") or "")
                    if not name:
                        continue
                    rec = per.setdefault(
                        name, {"text": "", "pages": [], "chunks": []})
                    if pno + 1 not in rec["pages"]:
                        rec["pages"].append(pno + 1)
                    if len(rec["text"]) < PERFONT_CAP_CHARS:
                        piece = (span.get("text") or "") + " "
                        rec["chunks"].append({"page": pno + 1,
                                              "char_start": len(rec["text"]),
                                              "len": len(piece)})
                        rec["text"] += piece
    return per


def collect_font_text(doc, max_pages=PERFONT_MAX_PAGES):
    """Per-font text only — the shape `audit()` has always used."""
    return {n: r["text"]
            for n, r in collect_font_spans(doc, max_pages).items()}


def page_of_offset(chunks, offset):
    """
    Which page a character offset in a font's accumulated text came from.

    Linear rather than bisected: a capped 2,500-character sample holds a few
    hundred spans at most, and a loop that is obviously correct beats a
    bisection that needs a comment explaining its boundary conditions.
    """
    page = chunks[0]["page"] if chunks else None
    for c in chunks:
        if c["char_start"] > offset:
            break
        page = c["page"]
    return page


def measure_font_text(text):
    """
    Measure one font's rendered text and return every signal, unconditionally.

    Split out of classify_font_output because a classifier reports only the
    rule that fired. Phase 2 needs the numbers behind the rules that did not:
    a threshold cannot be re-tuned against labelled ground truth from the
    string "8bit(0.76)", and a font that was measured and cleared currently
    leaves no record at all. Measuring and deciding are separate jobs; this
    does the measuring and nothing else.

    Gates are reported rather than applied -- `ascii_k_eligible` says whether
    the sample-size floor was met, so the floor itself stays open to review.
    Structural counts are taken even when the classifier would defer on them,
    which is what gives the SUSPECT class per-font evidence it has never had.
    """
    body = "".join(text.split())
    letters = [c for c in text if c.isascii() and c.isalpha()]
    tokens = re.findall(r"[A-Za-z]{2,}", text.lower())
    nospace = re.sub(r"\s+", "", text)
    dev_chars = len(DEV_RANGE.findall(text))
    invalid = len(INVALID_MATRA.findall(text))

    return {
        "sampled_chars": len(body),
        "dev_chars": dev_chars,
        "latin_letters": len(letters),
        "n_tokens": len(tokens),
        # --- shipped signals, in the units the thresholds are stated in -----
        "mojibake_ratio": (len(MOJIBAKE_RANGE.findall(body)) / len(body)
                           if body else 0.0),
        "ascii_k_ratio": (letters.count(DEVANAGARI_AA_ASCII) / len(letters)
                          if letters else 0.0),
        "ascii_k_eligible": len(letters) >= PERFONT_ASCII_MIN_LETTERS,
        "symbol_per_1k": (1000.0 * len(SYMBOL_IN_WORD.findall(text)) / len(body)
                          if body else 0.0),
        "english_ratio": (sum(1 for w in tokens if w in COMMON_ENGLISH)
                          / len(tokens) if len(tokens) >= 20 else 0.0),
        # --- structural signals, per font rather than per document ---------
        "invalid_matras": invalid,
        "invalid_rate_per_1k": (1000.0 * invalid / dev_chars
                                if dev_chars else 0.0),
        "word_initial_matras": len(WORD_INITIAL_MATRA.findall(text)),
        "adjacent_matras": len(ADJACENT_MATRA.findall(text)),
        "virama_then_matra": len(VIRAMA_THEN_MATRA.findall(text)),
        "detached_matras": len(DETACHED_MATRA.findall(text)),
        # Candidate signal, deliberately not wired into any verdict. If the
        # violation count collapses once whitespace is removed, the damage is
        # a space the extractor inserted mid-word ("जमा बाज ू"), which is a
        # different and milder defect than a genuinely reordered glyph stream.
        # It stays a stored measurement until Phase 2 ground truth says
        # whether it separates -- three candidate detectors were measured and
        # rejected, and none of them shipped on plausibility.
        "invalid_matras_nospace": len(INVALID_MATRA.findall(nospace)),
    }


def classify_font_output(text, m=None):
    """
    Judge one font by its own rendered text.

    Returns a short reason string when the output is legacy-encoded, or None
    when the font is fine or there is too little text to tell. Silence means
    "no evidence", never "clean" -- that distinction is what keeps the
    detector from manufacturing corruption it cannot see.

    Pass `m` to reuse measurements already taken; the decision rule and its
    thresholds are unchanged from Phase 1.
    """
    if m is None:
        m = measure_font_text(text)

    if m["sampled_chars"] < PERFONT_MIN_CHARS:
        return None

    # Real Devanagari present means the font maps to Unicode correctly. Whether
    # that Devanagari is *valid* is the structural check's job, not this one.
    if m["dev_chars"] > 20:
        return None

    if m["mojibake_ratio"] >= PERFONT_MOJIBAKE:
        return f"8bit({m['mojibake_ratio']:.2f})"

    if m["ascii_k_eligible"] and m["ascii_k_ratio"] >= PERFONT_ASCII_K:
        return f"ascii-remap(k={m['ascii_k_ratio']:.3f})"

    if (m["symbol_per_1k"] >= PERFONT_SYMBOL
            and m["english_ratio"] < PERFONT_ENGLISH):
        return f"symbol-remap({m['symbol_per_1k']:.0f}/1k)"
    return None


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
                # Keep counting pages after the first sighting. A font declared
                # on 24 pages that yields 24 characters of sampled text is
                # under-sampled, and that is only visible if both numbers are
                # recorded. See `n_pages_declared` in extract_observations.py.
                seen[xref]["pages"].add(pno + 1)
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
                # Pages are 1-based here because these numbers are read by
                # humans during annotation, not used as indices.
                "first_page": pno + 1,
                "pages": {pno + 1},
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


def decide_verdict(row):
    """
    Assign a bucket from stored measurements. First match wins, so the buckets
    stay disjoint and the percentages sum.

    Deliberately reads nothing but `row`. That is what lets a threshold or
    pattern change be re-applied to the whole corpus straight from the
    database, instead of re-opening a thousand PDFs to recompute numbers that
    have not changed. See `audit_corpus.py --rebucket`.
    """
    chars_per_page = row["chars"] / max(row["pages"] or 1, 1)

    if not row["n_fonts"] or chars_per_page < SCAN_CHARS_PER_PAGE:
        # Decided on extracted text as well as font count: a PDF with fonts
        # but nothing extractable is a scan too, which the original version
        # misfiled by deciding before extracting.
        return "SCAN"

    if row.get("legacy_fonts") or row.get("n_legacy_by_output"):
        # Two routes in: the font is named in LEGACY_PATTERNS, or a font was
        # convicted by its own rendered output.
        #
        # There were four. Two document-level detectors were removed after
        # check_detector_overlap.py measured them across the whole corpus:
        # they fired 203 and 20 times and were the sole evidence zero times.
        # The per-font detector runs the same measurements on undiluted text,
        # so it catches everything they did and 144 documents they missed.
        return "LEGACY"

    if (row["invalid_rate_per_1k"] >= SUSPECT_RATE_PER_1K
            and row["invalid_matras"] >= SUSPECT_MIN_ABS):
        # Observed corruption outranks an unidentifiable font: this is
        # evidence of damage, not merely absence of proof of safety.
        return "SUSPECT"

    if row.get("unknown_fonts"):
        return "UNCLASSIFIED"
    return "CLEAN"


def rematch_fonts(all_fonts):
    """
    Re-apply the current pattern lists to font names recorded earlier.

    Returns (legacy_fonts, unknown_fonts) exactly as `audit` would, so adding
    a family to LEGACY_PATTERNS can be re-applied corpus-wide without touching
    a single PDF. `all_fonts` carries the embedded/no-ToUnicode flag per name,
    because that conjunction is what makes an unknown name suspicious rather
    than merely unfamiliar.
    """
    legacy, unknown = set(), set()
    for entry in (all_fonts or "").split(";"):
        if not entry:
            continue
        name, _, flag = entry.rpartition("|")
        if not name:
            name, flag = entry, ""
        if _match_any(name, LEGACY_PATTERNS):
            legacy.add(name)
        elif not _match_any(name, KNOWN_GOOD) and flag == "1":
            unknown.add(name)
    return ";".join(sorted(legacy)), ";".join(sorted(unknown))


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
    # Every font name seen, with the embedded-and-no-ToUnicode flag that makes
    # an unfamiliar name suspicious. Recorded so that adding a font family to
    # the pattern list can be re-applied across the corpus from the database
    # alone -- without it, only matched and unmatched names survive, and a new
    # pattern matching a previously known-good font would be missed.
    row["all_fonts"] = ";".join(sorted(
        f"{f['name']}|{int(bool(f['embedded']) and not f['has_tounicode'])}"
        for f in fonts if f["name"]))

    # Per-font output analysis, before the document is closed. Catches legacy
    # encodings whose font names are auto-generated subset identifiers, which
    # no name-based rule can ever match.
    bad_fonts = {}
    try:
        for fname, ftext in collect_font_text(doc).items():
            reason = classify_font_output(ftext)
            if reason:
                bad_fonts[fname] = reason
    except Exception:
        pass
    row["legacy_by_output"] = ";".join(
        f"{n}={r}" for n, r in sorted(bad_fonts.items()))
    row["n_legacy_by_output"] = len(bad_fonts)

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

    # Document-wide mojibake and ASCII-remap ratios, kept as reported measures
    # only. They no longer feed the verdict: measured across all 1,602
    # documents, the document-level detectors fired 203 and 20 times and were
    # the sole evidence exactly zero times. The per-font detector subsumes
    # both, because it runs the same tests on undiluted input.
    body = "".join(text.split())
    row["mojibake_ratio"] = (round(len(MOJIBAKE_RANGE.findall(body)) / len(body), 3)
                             if body else 0.0)
    letters = [c for c in text if c.isascii() and c.isalpha()]
    row["ascii_k_ratio"] = (
        round(letters.count(DEVANAGARI_AA_ASCII) / len(letters), 4)
        if len(letters) >= 300 else 0.0)

    if row["dev_chars"] >= MIN_DEV_CHARS:
        row["invalid_rate_per_1k"] = round(
            1000.0 * row["invalid_matras"] / row["dev_chars"], 2)
    else:
        row["invalid_rate_per_1k"] = 0.0

    row["verdict"] = decide_verdict(row)
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
