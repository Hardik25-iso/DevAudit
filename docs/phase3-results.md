# Phase 3 — results

Run 2026-08-19. Design and its reasoning are in `phase3-design.md`; this
document reports what the runs produced.

**Read §1 before quoting any number from this phase.** The largest finding here
is not about extractors at all.

---

## 1. The headline: Phase 1's estimate is a floor

Script concordance — OCR of the rendered page compared against the text layer —
fires on **42.8% (pooled) / 36.6% (macro) of scorable Phase 1 `CLEAN` pages**.
These are documents the shipped detector cleared.

Correcting each body's rate by its own `CLEAN` share and its own measured
mismatch rate:

| | Phase 1 | corrected | delta |
|---|---|---|---|
| macro (each body equal) | 36.5% | **45.4%** | +8.9 |
| pooled (each document equal) | 48.4% | **56.7%** | +8.3 |

Per body:

| body | docs | Phase 1 | `CLEAN` share | mismatch | corrected |
|---|---|---|---|---|---|
| Nagpur MC | 235 | 6.4% | 47.2% | 66.3% | **37.7%** |
| Pune MC | 55 | 14.5% | 36.4% | 65.0% | **38.2%** |
| PCMC | 204 | 78.9% | 8.8% | 81.2% | 86.1% |
| MHADA | 281 | 22.4% | 14.9% | 27.5% | 26.5% |
| Nashik MC | 607 | 79.9% | 9.4% | 32.5% | 83.0% |
| Lucknow MC | 54 | 57.4% | 9.3% | 20.0% | 59.3% |
| Patna MC | 38 | 31.6% | 18.4% | 0.0% | 31.6% |
| Pune Metro | 128 | 0.8% | 39.1% | 0.0% | **0.8%** |

**This is the first defect in the project's history that pushes the estimate
up**, which `CLAUDE.md` names as the direction to distrust. Three things make it
survivable:

- **Pune Metro: 0 of 47 pages.** The body that publishes in English, which
  Phase 1 rated 1% legacy, does not move at all. A check that fired
  indiscriminately would move it.
- **`SUSPECT` fires at 1.7%.** `SUSPECT` is `CMAP_INVALID` — real Devanagari in
  impossible order. It is *not* a script failure, and concordance correctly
  stays silent on it. Specificity, from a class the check should ignore.
- **`LEGACY` fires at 68.1%.** Where the shipped detector is confident,
  concordance agrees.

The three Phase 1 classes behave exactly as the mechanism predicts. That
pattern is much harder to produce by accident than a single rate.

### 1.1 What these documents are

Legacy remaps embedded under **`Helvetica` and `Times-Roman`** — font names
sitting in `KNOWN_GOOD`, output that is plain ASCII with no Latin-1 supplement
and no Kruti Dev `k`:

```
textlayer: "~ ~~ Cfittti&q, •:Wt'{_~ q~H'l~41W'>c61, --11'1'{~ (~~)"
ocr      : 'कार्यालय, नागपूर महानगरपालिका, नागपूर (आस्थापना विभाग)'
```

Every shipped signal is blind to these by construction. This is the coverage
gap `phase0-schema.md` §11.6 identified and said would need "a new signal per
family". **Concordance is that signal**, and it is family-agnostic: it does not
care which mapping produced the output, only that the rendered and extracted
scripts disagree.

### 1.2 The caveats, which are not small

- **Grain mismatch.** Concordance is measured on **page 1 only**; the Phase 1
  verdict is document-level over up to 8 pages per font. A document whose page 1
  is an English cover sheet is scored on that cover sheet. This cuts both ways
  and the size of the resulting bias is not known.
- **It rests on OCR being right about script.** A weak requirement, and the
  Pune Metro control tests it directly — but it is a requirement.
- **`phase1-results.md` is not revised.** Decided with the author 2026-08-19: it
  stays traceable to the rows that produced it, and this correction is reported
  additively, the way Phase 0 reported its findings.

---

## 2. The extractor benchmark

Tier 1: the 317 non-`SCAN` documents carrying the 434 labelled observations,
first 5 pages, 4 text arms. 6,340 rows.

### 2.1 Loss — and why it had to be measured first

```
arm           pages  chars/pg   empty  U+FFFD   fail%   macro
pdftotext      1585       817   0.431   0.170   0.627   0.538
pymupdf        1585       958   0.418   0.000   0.425   0.337
pdfplumber     1585      1028   0.418   0.000   0.421   0.343
pypdf          1585      1003   0.418   0.000   0.421   0.348
```

`pdftotext` loses **20.6 points more pages** than the other three, entirely
from U+FFFD replacement characters. The ~42% empty-page floor is shared by all
arms and is pages 2–5 of short documents, not an extractor difference.

**Scored on the Phase 1/2 signal battery alone, `pdftotext` would have ranked
best**, because `MOJIBAKE_RANGE` does not cover U+FFFD and its failure is
therefore invisible to every corruption signal this project owns. That is why
loss is measured first and gates everything downstream, and it is the single
most important design decision in this phase.

### 2.2 Cross-arm agreement — a lower bound that costs no ground truth

```
pair                        pages  identical  same chars  differ
pymupdf vs pypdf              908      0.768       0.000   0.232
pdfplumber vs pypdf           917      0.095       0.654   0.251
pymupdf vs pdfplumber         909      0.094       0.506   0.400
pdftotext vs pymupdf          589      0.041       0.190   0.769

all arms identical on 39/919 = 4.2% of pages
```

So **at least one arm is wrong on ≥ 95.8% of pages**, established without any
reference text or labelling.

`pymupdf` and `pypdf` agree on characters 76.8% of the time and never disagree
on order alone. `pdfplumber` agrees on *characters* with both but reorders them
on 50–65% of pages — it is the outlier on layout, not on decoding.

### 2.3 Structural validity — a ranking I cannot report

```
arm           scored  invalid   macro  rate/1k  spurious-sp
pymupdf          233    0.914   0.872    35.82    0.678
pdfplumber       234    0.983   0.804    51.47    0.863
pypdf            231    0.970   0.974    46.86    0.892
```

**Pooled says `pdfplumber` is worst (0.983 vs 0.914); macro says it is best
(0.804 vs 0.872). The ranking reverses.** `CLAUDE.md`'s rule is to distrust the
pooled figure when the two disagree, and Nashik contributing 152 of 317
documents is the likely cause. Both figures are printed and **no ranking between
PyMuPDF and pdfplumber is claimed on this metric.**

The `spurious-sp` column is unaffected by that dispute and is a result in its
own right: on **68–89% of scorable pages**, a matra violation disappears once
whitespace is removed. The extractor inserted a space mid-word. This is the
defect `phase0-schema.md` §4.4 explicitly deferred to Phase 3, measured for the
first time — and it is attributable to the extractor, not the font.

---

## 3. What can and cannot be said about "which extractor is best"

**Established.** `pdftotext` is materially worse than the other three on the
metric that gates all others, and its failure mode is invisible to conventional
corruption detection. If a pipeline uses poppler on Indic government PDFs, it is
losing about a fifth of its pages to replacement characters with no error raised.

**Established.** No extractor recovers legacy-encoded text. Concordance fires on
49.6–50.2% of all scorable pages for every text arm, within half a point of each
other. The arms differ in *how* they fail, not in *whether* they do.

**Not established.** Any ranking among PyMuPDF, pdfplumber and pypdf. They
differ on layout and on structural validity in ways that reverse between macro
and pooled, and this phase does not resolve it.

**Out of reach entirely.** Whether any output is *correct*. Every metric here is
reference-free; a tool emitting fluent, plausible, wrong Devanagari would pass
all four.

---

## 4. Limitations

- **Page grain, not font grain.** Three ways to keep Phase 2's per-font grain
  were measured and all three failed (`phase3-design.md` §2.1). Absolute rates
  are diluted by mixed-font pages; between-arm comparisons are not, since all
  arms ran on the same pages.
- **Tier 2 is page 1 only.** Chosen so the concordance run could cover all 1,097
  non-`SCAN` documents rather than a sample. §1.2 states the cost.
- **Single OCR engine.** Tesseract 5.4 `tessdata_fast`. A second engine would
  test the §1 finding independently and has not been run.
- **One `pdfplumber` error** across 1,097 documents; recorded in `extraction`
  with its exception rather than silently skipped.

---

## 5. What this hands to Phase 4

Phase 3 establishes that **no extractor recovers legacy-encoded text**, which
means the recovery problem cannot be solved by tool choice. It has to be solved
by a reverse mapping table per encoding family — Phase 4.

It also hands over a **new detector**. Concordance closes the coverage gap
§11.6 documented, is family-agnostic, and needs no font list. Its cost is an
OCR pass. Whether it should be folded into `decide_verdict` is a Phase 4
decision and should be taken only against labelled ground truth, the way three
earlier candidate detectors were measured and rejected.
