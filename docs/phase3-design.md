# Phase 3 — benchmarking extractors

Design, settled with the author 2026-08-19, before any benchmark code was
written. Phases 1 and 2 asked whether the text layer is wrong and whether a
program can tell. This phase asks a different question: **given a document
whose text layer is wrong, does the choice of extraction tool change what you
get?**

Read §2 first if you read nothing else. The pilot measurements there overturned
the assumption this phase was expected to rest on, and they are what fixes the
metric.

---

## 1. The question, stated precisely

For each document, five tools are run over the same bytes and their outputs
compared. The tools are listed in §4. The comparison is *not* against a
reference text — none exists, and manufacturing one would mean transcribing
Devanagari by hand for thousands of pages.

So "recovered correctly" has to be built out of checks that need no reference.
§3 defines four of them. The short version: you cannot ask whether the text is
*right*, but you can ask whether it is *there*, whether it is in the *right
script*, whether it is *well-formed*, and whether the tools *agree*. Those four
questions have reference-free answers and together they bound the thing you
actually care about.

---

## 2. Four pilot measurements, and the assumption they killed

Everything below was measured against the live corpus with the drive attached,
before the design was fixed. Two of the four results contradicted what this
phase was expected to find.

### 2.1 The per-font grain does not survive into Phase 3

Phase 2's most productive decision was measuring per font rather than per
document — a 15× separation, recorded in `CLAUDE.md`. The obvious plan was to
carry that grain forward and score each extractor per font against the 434
labels. Three ways to do it were tried. All three fail:

| approach | result |
|---|---|
| restrict to documents where the labelled font holds ≥80% of characters | 70 of 434 observations qualify; 0 `LEGACY_SYMBOL`, 10 `CORRECT` |
| restrict to *pages* where it holds ≥80% | ~30% of observations have even one such page (60-document probe) |
| span probing — find PyMuPDF's span verbatim in another arm's output | fails, see §2.2 |

These documents are mixed at the page level, not merely at the document level.
The English tender header and the Devanagari body share pages.

**Consequence, stated as a limitation rather than worked around: Phase 3's unit
is the page.** Per-font grain survives only for the two arms that attribute
fonts (PyMuPDF, pdfplumber), which becomes a secondary and tighter comparison
against the 434 labels.

Dilution is real and it depresses every absolute rate this phase reports. But
it depresses all five arms *identically*, because they are run over the same
pages. What dilution destroys is the absolute number, not the ranking — and the
ranking is what this phase is for.

### 2.2 Extractors do not fail identically — the assumption that died

The expected finding was that this phase would be short: a legacy font supplies
no `ToUnicode`, every extractor reads the same content stream, so every
extractor returns the same garbage and there is nothing to benchmark. That is
false.

```
DVI-TTYogesh-Normal   PyMuPDF: ZÉä®úÉìCºÉ{Éä{É®ú      pdftotext: Z�����
F4                    PyMuPDF: xÉÉÊ¶ÉEò¨É½þÉxÉMÉ®ú   pdftotext: ���TM����"־��
```

Different garbage. Each tool has its own fallback ladder when `ToUnicode` is
absent — encoding differences, glyph names, the embedded font's own cmap, or
giving up — and they descend it differently.

This is why span probing fails as a join key: the phenomenon being measured
destroys the alignment. It is also why the phase is worth running at all.

### 2.3 The existing signal battery would rank the worst extractor best

The decisive measurement. The Phase 1/2 signals, run over both arms, 40
documents, first 5 pages:

```
verdict  arm           n   chars  mojibake    dev%   U+FFFD
LEGACY   pymupdf      24    4238     0.415   0.216    0.000
LEGACY   pdftotext    20    4225     0.001   0.000    0.437
CLEAN    pymupdf      14    3018     0.001   0.000    0.000
CLEAN    pdftotext    14    3015     0.000   0.000    0.001
```

On corrupt documents `pdftotext` scores **0.001 mojibake — cleaner than the
clean control** — while emitting **43.7% U+FFFD replacement characters**.
`MOJIBAKE_RANGE` does not cover U+FFFD, so every corruption signal this project
owns is blind to it. Note `n` as well: 4 of 24 corrupt documents yielded under
200 characters from `pdftotext` at all.

Scoring extractors with the Phase 2 signals alone would therefore have produced
a confident, wrong, publishable headline: *poppler is the cleanest extractor*.
It is not cleaner. It is more completely broken, in a way that happens to be
invisible to instruments built to detect a different failure.

This is `CLAUDE.md`'s oldest rule — **the instrument fails toward silence, never
toward alarm** — recurring in a new location. It is the reason for §3.1 and for
the gate in §3.5.

### 2.4 Where nothing is wrong, the arms agree

The `CLEAN` rows above are the control, and they behave: both arms quiet, both
near zero on every signal. So the divergence in §2.2 is a property of corrupt
documents, not background noise between libraries.

---

## 3. What "recovered correctly" means

Four measurements, not one. Each is blind to what the others catch, so all four
are always reported together.

### 3.1 Loss — is there text, and is it made of real characters?

| measure | definition |
|---|---|
| `chars_per_page` | non-whitespace characters ÷ pages attempted |
| `replacement_ratio` | U+FFFD ÷ non-whitespace characters |
| `control_ratio` | C0 controls (excl. tab/LF/FF) ÷ non-whitespace characters |
| `empty_page_rate` | share of pages yielding under `SCAN_CHARS_PER_PAGE` (25) |

**This is measured first and gates the rest (§3.5).** Every metric below it is a
rate, and a rate flatters an empty numerator: an extractor that returns nothing
has no mojibake, no invalid matras and no script mismatch.

The 25-character floor is `font_audit.SCAN_CHARS_PER_PAGE`, reused deliberately
— Phase 1 already fixed what "no usable text layer" means and a second
definition would make the two phases incomparable.

### 3.2 Script concordance — is it even the right script?

The only check that reaches outside the text layer, and therefore the only one
that can say an extractor is *wrong* rather than merely *different*.

The page is rendered to an image and OCR'd. Compare Devanagari share:

```
dev_share(text) = dev_chars / non-whitespace chars

script_mismatch  if  dev_share(ocr) >= 0.30  and  dev_share(arm) < 0.10
script_excess    if  dev_share(ocr) <  0.10  and  dev_share(arm) >= 0.30
```

**Why this works despite OCR being unreliable on Devanagari.** The check does
not use OCR as a reference text. It asks OCR one question — *what script is on
this page?* — which is far weaker than asking what the characters are, and
survives a large character error rate intact. A page rendering Devanagari while
its text layer emits Latin-1 is a mile-wide gap; no plausible OCR error rate
closes it.

This catches `LEGACY_8BIT`, `LEGACY_ASCII` and `LEGACY_SYMBOL` by construction,
since mapping Devanagari glyphs onto Latin codepoints *is* a script mismatch.

There is no substitute for it. Script cannot be read off the font: legacy faces
have no reliable Unicode mapping, and `docs/phase0-schema.md` §4.3 forbids
judging by font name — 32% of affected documents match no known name, and some
names are leaked Windows temp filenames.

`script_excess` is the reverse case and is expected to be rare. It is measured
anyway, because a check that can only fire in the direction you expect is not a
check.

### 3.3 Structural validity — right script, possible sequences?

`invalid_rate_per_1k` from `font_audit.measure_font_text()`, unchanged, applied
to each arm's page text. This is the `CMAP_INVALID` case: real Devanagari
codepoints in structurally impossible order, which §3.2 cannot see because the
script is right.

Unchanged is the point. It is the one Phase 1 signal that survived Phase 2
evaluation well (0.943 / 0.953, sitting inside a plateau rather than on an
edge), so it enters this phase as a known quantity.

### 3.4 Extractor-attributable defects — right characters, wrong arrangement

The class `docs/phase0-schema.md` §4.3 explicitly reserved for this phase:
reading-order scrambling and spurious spaces are labelled `CORRECT` there,
because the font is fine and the defect belongs to the extractor. Here is where
they get counted against the extractor that caused them.

Measured only on pages where the arms agree on the *character bag* — so the
encoding question is already settled and arrangement is the only variable:

- **Reading order.** `difflib.SequenceMatcher` ratio over token sequences,
  pairwise between arms. Boring and legible beats a custom alignment.
- **Spurious spaces.** The gap between `invalid_matras` and
  `invalid_matras_nospace`, already stored per font in Phase 0 and defined
  there for exactly this purpose. A violation count that collapses when
  whitespace is removed is a space the extractor inserted, not a reordered
  glyph stream.

### 3.5 The composite, and why loss gates it

**usable-page rate** = share of pages that pass loss *and* script concordance
*and* structural validity.

A page failing the loss check is scored failed outright and never reaches §3.2
–§3.4. This mirrors `evaluate.py --agreement`, which gates the reports beneath
it for the same reason: a downstream number computed on absent data is worse
than no number, because it looks like a result.

Loss thresholds, chosen from the §2.3 pilot:

| measure | fails at | clean baseline | observed on failure |
|---|---|---|---|
| `chars_per_page` | < 25 | — | 4 of 24 docs under 200 total |
| `replacement_ratio` | ≥ 0.05 | 0.001 | 0.437 |
| `control_ratio` | ≥ 0.05 | 0.000 | — |

0.05 is 50× the clean baseline and roughly one ninth of the observed failure
rate, so it sits in a wide empty gap rather than on an edge — the property
§11.6 identifies as what a threshold should look like when it is right.

That gap is asserted from a 40-document pilot, not established. **The loss
threshold is therefore a swept parameter**, and `evaluate_extractors.py
--sweep` reports it the same way `evaluate.py --sweep` reports the Phase 1
signals. If the distribution turns out to be continuous rather than bimodal,
the gate is wrong and the sweep will say so.

The composite is never reported alone. All four components appear beside it, so
a healthy composite cannot conceal a dead component.

---

## 4. The extractors

| arm | why it is here |
|---|---|
| `pdftotext` (poppler 25.x) | the de facto default — what people actually run, and what most pipelines shell out to |
| `PyMuPDF` | the incumbent. Every Phase 1/2 number came from it, so it anchors this phase to the 36.5% / 48.4% finding. Attributes fonts. |
| `pdfplumber` (pdfminer.six) | independent code lineage, and per-character font names — the second arm that keeps per-font grain |
| `pypdf` | what LLM and RAG pipelines actually use, which is the "AI systems inherit the corruption" motivation in `OVERVIEW.md` |
| Tesseract 5.4 OCR | the only arm that reads the page rather than the text layer, and therefore the only source for §3.2 |

Not included: bare `pdfminer.six` (same engine as pdfplumber, so it would add a
duplicate rather than an arm).

**`pdftotext` must be read as bytes and decoded UTF-8 explicitly.** The project
has already been burned by this once: `subprocess(text=True)` decodes using the
locale encoding, cp1252 on this machine, so Devanagari arrived as mojibake and
every corruption counter silently read zero. `requirements.txt` records it. The
adapter decodes explicitly and a test pins the behaviour.

**OCR configuration.** Tesseract 5.4.0, `tessdata_fast` for `hin` and `mar`,
kept in a gitignored `data/tessdata/` with `TESSDATA_PREFIX` pointed at it
rather than installed into `Program Files` — no admin needed, and the language
data stays pinned to this project. `tessdata_fast` rather than `tessdata_best`
because §3.2 needs the script right, not the characters, and the fast models are
an order of magnitude smaller on a slow connection.

Note that OCR here runs on a **cleanly rendered digital page**, not a scan. This
is a much easier problem than the scan-OCR literature describes, and its error
rate should not be read across from that work.

---

## 5. Sampling

Sampling has produced a wrong answer three times in this project, so the guards
are not optional: fixed seed, per-body caps, and **the macro average by issuing
body reported beside the pooled figure**, with the pooled figure distrusted
when they disagree.

| tier | population | arms | pages |
|---|---|---|---|
| 1 | the 320 documents carrying the 434 labelled observations | 4 text arms | first 5 |
| 2 | stratified draw across all 8 bodies for coverage | 4 text arms | first 5 |
| 3 | ~150 documents drawn from tier 1 | + OCR | 2 |

`SCAN` documents (505 of 1,602) are excluded throughout: no text layer means
nothing for a text extractor to recover, and OCR of a scan is a separate and
well-studied question that `OVERVIEW.md` already places out of scope.

Tier 3 is small because rendering plus OCR costs seconds per page against
milliseconds for the text arms. It is drawn from tier 1 so that every OCR'd page
also carries font labels.

---

## 6. Implementation

Additive only. No Phase 1 or Phase 2 table is written to, the same rule Phase 0
followed — the 36.5% / 48.4% figure stays traceable to the rows that produced
it.

| file | job |
|---|---|
| `phase3_schema.py` | `extraction_run`, `extraction` tables; idempotent |
| `extractors.py` | one adapter per arm behind a uniform interface |
| `benchmark_extract.py` | the drive-attached pass; resumable |
| `evaluate_extractors.py` | the four reports of §3, plus `--sweep` |

---

## 7. What this phase cannot answer

Recorded now, so the write-up does not have to discover it later.

- **It cannot say which extractor is *correct*,** only which is least wrong on
  four reference-free checks. A tool that emits fluent, plausible, wrong
  Devanagari would pass every check in §3.
- **It is page-grained, not font-grained** (§2.1). Absolute rates are diluted by
  mixed-font pages. Rankings survive; absolute recovery rates should be quoted
  with that attached.
- **§3.2 rests on OCR being right about script.** That is a weak requirement,
  but it is a requirement, and it is untested on this corpus until the tier 3
  run produces its first numbers.
- **Repair is out of scope.** Establishing that no extractor recovers a legacy
  font is a Phase 3 result; building the reverse mapping table that would is
  Phase 4.
