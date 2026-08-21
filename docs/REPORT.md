# Silent text-layer corruption in Indian government PDFs

A measurement study. 1,602 documents, 8 issuing bodies, 3 states.

This is the consolidated account. Each phase has its own results document with
the full detail and the working; this reads top to bottom and says what was
found, how, and what the findings will not support.

---

## Summary

Roughly **45% of documents per issuing body, 57% pooled**, carry a text layer
that extracts without any error and is linguistically wrong. Nothing warns you.
The PDF renders correctly, the extraction tool exits zero, and the text
underneath is garbage.

Four things this study establishes:

1. **It is common.** Not a few unlucky files.
2. **It can be detected automatically** by measuring output rather than reading
   font names — which matters, because a third of affected documents match no
   known font name at all.
3. **No extraction tool fixes it.** Five were benchmarked. They differ in *how*
   they fail, not whether. One popular tool fails worst while appearing
   cleanest.
4. **Repair is possible and hard.** Reverse-encoding tables can be learned
   automatically from OCR alignment. The best recovers about a third of
   characters — useful to build on, not to trust.

---

## 1. The problem

A PDF is two things stacked: a picture of the page, and a text layer machines
read. Humans see the picture. Search engines, screen readers, archives and AI
training pipelines read the text layer.

Before Unicode was widespread, Indian publishers used fonts that cheated.
Instead of storing "the Devanagari letter क", they stored "the letter k" and
shipped a font that *draws* क wherever a k appears. It renders correctly, and
the file genuinely contains `k`. These fonts are decades old and still in daily
use in government offices.

Three mechanisms appear in this corpus:

| Mechanism | Extracts as | Actually |
|---|---|---|
| Legacy 8-bit (Marathi style) | `xÉÉÊ¶ÉEò ¨É½þÉxÉMÉ®ú{ÉÉÊ±ÉEòÉ` | नाशिक महानगरपालिका |
| Legacy ASCII remap (Kruti Dev family) | `i'kq dY;k.k foHkkx` | पशु कल्याण विभाग |
| Correct font, broken `ToUnicode` CMap | `जानवे ारी` | जानेवारी |

The third is the nastiest: a legitimate Unicode face, a respectable font name,
and output that is real Devanagari — just structurally impossible Devanagari,
such as a vowel sign opening a word.

**The failure is silent in every direction.** No error is raised, no red flag
appears, and the damage is inherited by anything downstream that scrapes,
indexes or trains on the text.

---

## 2. Method

### 2.1 Corpus

1,602 documents from 8 government bodies across Maharashtra, Bihar and Uttar
Pradesh, collected under `robots.txt`, random draws with fixed seeds, per-body
caps.

**`robots.txt` is a hard boundary on what any study of this kind can contain.**
Every `.nic.in` district site publishes through a CDN that disallows all
crawling — hundreds of bodies, uncollectable. The two Hindi-belt bodies here are
present only because they happen to self-host.

### 2.2 Detection

Two decisions did most of the work.

**Classify by output, not by font name.** 32% of affected documents match no
known font name. Some names are auto-generated subset IDs (`TT313t00`); one is a
leaked Windows temp filename (`Z@RAF1C.tmp`).

**Measure per font, not per document.** A document mixes fonts, so a legacy face
beside English headers produces a blended signal that trips no threshold.
Undiluted, the same measurements separate by 15×. This was the single most
productive change to the instrument.

Four signals, each targeting one mechanism, with thresholds **pre-registered
before any data existed** so a negative result would have been publishable:

| Signal | Detects | Threshold |
|---|---|---|
| `mojibake_ratio` | Latin-1 supplement density | 0.15 |
| `ascii_k_ratio` | Kruti Dev remap | 0.05 |
| `symbol_per_1k` | symbol-in-word remap | 10.0 |
| `invalid_rate_per_1k` | structurally impossible Devanagari | 2.0 |

`ascii_k_ratio` is worth explaining: Kruti Dev maps ASCII `k` onto ा, the
commonest character in written Hindi. Encoded text inherits that frequency onto
English's rarest letter. Genuine English tops out at 2.5% `k`; Kruti-Dev-encoded
Hindi starts at 10.2%.

### 2.3 The governing bias

**The instrument fails toward silence, never toward alarm.** Every defect found
in the original tool pushed the corruption estimate *down*. A detector that
over-fires inflates the headline in the flattering direction, which is the one
to distrust.

This was asserted from the start and later quantified: against labelled data the
detector produced **41 false negatives to 5 false positives.**

It is also why the one correction that pushes the estimate *up* (§3.3) was made
to clear three separate controls before being believed.

---

## 3. Results

### 3.1 Prevalence

| | n | % |
|---|---|---|
| No text layer (scan) | 505 | 31.5% |
| Legacy non-Unicode fonts | 596 | 37.2% |
| Structurally invalid Devanagari | 180 | 11.2% |
| Unclassified | 11 | 0.7% |
| Clean Unicode | 310 | 19.4% |

**36.5% macro / 48.4% pooled.** Quote the macro figure: Nashik alone is 38% of
the corpus and the worst-affected body in it, so pooling lets one municipal
portal steer the result.

Scans set aside — a different, well-studied problem — **70.7% of the documents
that carry a text layer at all have a wrong one.** Per-body rates run from 1%
(Pune Metro, publishes in English) to 80% (Nashik).

### 3.2 Does the detector work?

6,572 font observations; 434 drawn stratified and labelled.

| | |
|---|---|
| precision | 0.975 |
| recall | 0.826 |

**These are agreement with a single model-assisted labelling pass, not validated
accuracy.** A two-pass protocol gated on Cohen's κ ≥ 0.7 was pre-registered and
not completed. The deviation is recorded rather than hidden, and the figures
must be described accordingly.

Two signals hold up well (`mojibake_ratio` 0.974/0.949, `invalid_rate_per_1k`
0.943/0.953). Two do not. `ascii_k_ratio` has a **coverage** problem rather than
a tuning problem: it detects one encoding family, and no threshold reaches ISM
or transliteration-style remaps, which map nothing onto `k`. `symbol_per_1k`
does not work at any threshold against labels despite validating at precision
1.000 against name-identified fonts in Phase 1.

### 3.3 The estimate is a floor

Comparing OCR of the *rendered page* against the text layer asks a question no
other signal here asks: **is the output even in the right script?** OCR need
only be right about script, not characters, which is a far weaker requirement
than accuracy and survives a large error rate intact.

It catches what §3.2 said would need "a new signal per family": legacy remaps
embedded under `Helvetica` and `Times-Roman`, plain ASCII, no Latin-1
supplement, no `k` excess. Invisible to everything above.

It fires on **42.8% of scorable pages Phase 1 called clean.**

| | Phase 1 | corrected |
|---|---|---|
| Macro | 36.5% | **45.4%** |
| Pooled | 48.4% | **56.7%** |

Three controls, because this moves the estimate in the flattering-to-distrust
direction:

- **Pune Metro: 0 of 47 pages.** English-publishing, Phase 1 rated it 1%, and it
  does not move.
- **`SUSPECT`: 1.7%.** That class is invalid Devanagari, not a script failure,
  so the check should ignore it — and does.
- **`LEGACY`: 68.1%.** Where the shipped detector is confident, they agree.

All three classes behave as the mechanism predicts, which is much harder to
produce by accident than any single rate.

### 3.4 No extractor recovers the text

Five arms: `pdftotext`, PyMuPDF, `pdfplumber`, `pypdf`, Tesseract OCR.

The expectation was that they would fail *identically* — same bytes, same
missing `ToUnicode`. **False.** Each descends its own fallback ladder and
produces different garbage:

```
DVI-TTYogesh-Normal   PyMuPDF: ZÉä®úÉìCºÉ{Éä{É®ú      pdftotext: Z�����
```

**`pdftotext` loses 20.6 points more pages than the other three**, entirely to
U+FFFD replacement characters — and **would have ranked best** on the signal
battery above, because none of those signals can see a replacement character.
Silence scoring as success, one more time. That is why the benchmark measures
loss first and gates everything else on it.

All four text arms agree on only **4.2% of pages**, so at least one is wrong on
≥95.8% — a lower bound on extractor error that costs no ground truth at all.

No ranking is claimed among PyMuPDF, `pdfplumber` and `pypdf`: structural
validity reverses between the macro and pooled figures.

### 3.5 Repair: partial

Five encoding families cover 94% of convicted legacy observations. They are
identified by output signature, not font name — 546 distinct names cover 5,438
observations and the commonest are `F1`–`F8` and `Calibri`. Every cluster is
label-pure, which is the evidence they are real encodings.

Reverse tables were **learned** by aligning extracted text against OCR of the
same page — a parallel corpus the extractor benchmark left behind.

```
xÉÉÊ¶ÉEò ¨É½þÉxÉMÉ®ú{ÉÉÊ±ÉEòÉ  ->  नाशिक महानगरपालिका
```

| family | rules | coverage | invalid/1k | OCR agreement | test pages |
|---|---|---|---|---|---|
| fam-01 | 183 | 0.73 | 19.3 | 0.37 | 93 |
| fam-02 | 154 | 0.69 | **8.2** | **0.09** | 7 |
| fam-03 | 174 | 0.75 | 12.6 | 0.37 | 4 |
| fam-04 | 144 | 0.60 | 52.1 | 0.27 | 3 |
| fam-05 | 69 | 0.18 | 372.5 | 0.02 | 9 |

One family converts usefully and remains well short of correct. Test sets of
3–9 pages carry no weight; only fam-01 has an evaluation worth the name.

**fam-02 is the instructive failure.** It has the *best* structural validity of
any family and the *worst* agreement with the page. It produces confident,
well-formed Devanagari that is not the right Devanagari — and structural
validity alone, the designated primary measure, would have ranked it best. This
is why all measures are always reported together.

---

## 4. What this does not support

- **Devanagari only.** Southern-language legacy font ecosystems differ; neither
  the structural check nor the ASCII-remap detector transfers.
- **Ground truth is one pass.** §3.2's precision and recall are agreement with a
  single model-assisted labelling, not validated accuracy.
- **The correction in §3.3 rests on one OCR engine** and compares page 1 against
  a document-level verdict. A second engine would test it independently and has
  not been run.
- **The converter is not accurate enough to use unattended.** Roughly a third of
  characters on the best family.
- **External validity is bounded by `robots.txt`.** Findings describe
  self-hosting bodies. Whether S3WAAS-published bodies differ is unmeasurable
  from outside.
- **Absolute rates in the extractor benchmark are diluted** by mixed-font pages;
  rankings survive, absolute recovery rates should be quoted with that attached.

---

## 5. What is released, and what is not

**Not released: the documents.** Sources have incompatible licences and at least
one forbids redistribution.

**Not released: the working database.** It holds ~11.1M characters of extracted
document text, which includes dates of birth and caste categories for
identifiable private individuals — collected incidentally from public portals,
never sought. India's DPDP Act 2023 is the relevant regime and "already public"
does not obviously settle it. This project's position is that the working
database is never published, and that no released artifact quotes document text.
`export_manifest.py` enforces that rather than relying on it being remembered:
it refuses to write any column carrying Devanagari, a date-of-birth pattern, or
a caste category outside the identifier columns the rebuild needs.

**Released:**

| | |
|---|---|
| `manifest.csv` | 1,602 rows: source URL, SHA-256, issuing body, every measurement |
| `rebuild_corpus.py` | re-fetches from source URLs, verifies each SHA-256 |
| `mapping_tables.csv` | 688 reverse-encoding rules across 5 families |
| `summary.json` | headline counts, checkable against the rows |
| `LICENSING.md` | source-by-source redistribution terms |

The checksums make this a **reproduction rather than an approximation**: if a
source re-paths or edits a PDF, the rebuild reports a mismatch instead of
quietly producing a different corpus.

---

## 6. Reading further

| | |
|---|---|
| [`phase1-results.md`](phase1-results.md) | prevalence, with per-body breakdowns |
| [`phase2-results.md`](phase2-results.md) | detector evaluation and its limits |
| [`phase3-results.md`](phase3-results.md) | extractor benchmark; the §3.3 correction |
| [`phase4-results.md`](phase4-results.md) | the converter and where it falls short |
| [`phase0-schema.md`](phase0-schema.md) | schema, label definitions, annotation protocol |
| [`LICENSING.md`](LICENSING.md) | what each source permits |
