# DevAudit

Measuring how often Indian government PDFs carry a text layer that extracts
cleanly and is wrong.

## The problem

Run `pdftotext` on a Marathi municipal budget and you'll get Devanagari back.
No warning, no error, exit code 0. The text will also be wrong:

| What you get | What it should be | What happened |
|---|---|---|
| `जानवे ारी` | `जानेवारी` | vowel sign moved |
| `स्थालनक सांस्था कर` | `स्थानिक संस्था कर` | न→ल, ं→ां |
| `लमळकत` | `मिळकत` | consonant substituted |
| `जमा बाज ू` | `जमा बाजू` | matra detached |

The cause is legacy non-Unicode fonts — Shree-Dev, Kruti Dev, DV-TTSurekh and
a long tail of others — which map Devanagari glyphs onto ASCII codepoints in
*visual* order instead of *logical* order. The PDF renders correctly on screen.
The text underneath is scrambled.

Nothing in the pipeline notices. Search indexes it, RAG retrieves it, datasets
absorb it, and every downstream consumer inherits the corruption without ever
seeing a stack trace.

## What we found

Phase 1 was a go/no-go gate: is this common enough to be worth a project?

**1,602 documents, 8 issuing bodies, three states, random sample, fixed seed.**

| | n | % |
|---|---|---|
| No text layer (scan) | 505 | 31.5% |
| **Legacy non-Unicode fonts** | **596** | **37.2%** |
| **Suspect (invalid Devanagari)** | **180** | **11.2%** |
| Unclassified font | 11 | 0.7% |
| Clean Unicode | 310 | 19.4% |

Two headline figures, because they disagree and the gap is the point:

| | `LEGACY + SUSPECT` |
|---|---|
| Pooled — every document counts equally | 48.4% |
| **Macro — every issuing body counts equally** | **36.5%** |

**Quote the macro figure.** Repeated collection passes left the bodies very
unequally sized: Nashik alone is 38% of the corpus and is the worst-affected
body in it, so pooling lets one municipal portal steer the result. The macro
average answers "how bad is a typical body", which is the question the project
is actually asking. Both clear the 15% threshold fixed before any data existed.

Set the scans aside — they're an OCR problem, already well studied, and not
what this measures. Among the 1,097 documents that *do* carry a text layer,
**70.7% of it is wrong.**

### It isn't one problem, it's three

The starting hypothesis named one mechanism. The corpus contains three, and
font-name matching is blind to two of them:

- **Legacy 8-bit fonts, Marathi style** — glyphs mapped onto the Latin-1
  supplement. Extracts as `xÉÉÊ¶ÉEò ¨É½þÉxÉMÉ®ú{ÉÉÊ±ÉEòÉ` (= नाशिक महानगरपालिका).
- **Legacy ASCII remapping, Hindi style** — Kruti Dev and relatives map onto
  *plain ASCII*, carrying no high bytes at all. Extracts as
  `i'kq dY;k.k foHkkx` (= पशु कल्याण विभाग). Invisible to a detector built for
  the Marathi case; three such documents sat in `CLEAN` until it was fixed.
- **Correct fonts, wrong CMaps** — documents set in genuine Unicode faces
  (Mangal, Aparajita, Adobe Devanagari) whose ToUnicode tables are wrong,
  usually after a PDF post-processor. Extracts as Devanagari that is
  *structurally impossible* — vowel signs starting words, two matras adjacent.

That last class alone is 11.2 points of the pooled rate, and every font name in
those files looks perfectly respectable.

### It isn't uniform either

| Body | State | n | scan | legacy | suspect | clean |
|---|---|---|---|---|---|---|
| Nashik MC | MH | 607 | 10% | **72%** | 8% | 9% |
| Pimpri-Chinchwad MC | MH | 204 | 11% | 18% | **61%** | 9% |
| **Lucknow MC** | **UP** | 54 | 30% | **57%** | 0% | 9% |
| MHADA | MH | 281 | 62% | 22% | 1% | 15% |
| Patna MC | BR | 38 | 39% | 21% | 11% | 18% |
| Pune Municipal Corp | MH | 55 | 49% | 15% | 0% | 36% |
| Nagpur MC | MH | 235 | 46% | 5% | 1% | 47% |
| Pune Metro | MH | 128 | 60% | 1% | 0% | 39% |

The spread runs from 80% corrupt at Nashik to 1% at Pune Metro.

**The finding holds in both regions, at similar rates** — Maharashtra 48.5%,
the Hindi belt 46.7%. An earlier draft of this README reported the Hindi belt
as substantially worse; that gap was an artifact of Maharashtra being
under-sampled on Nashik at the time, and it closed once the corpus filled out.
The defensible claim is that the problem is not Marathi-specific, not that it
is worse elsewhere.

Patna and PMC both sit at n<60 and carry wide error bars.

What actually predicts corruption is **who publishes in an Indian language at
all**. Municipal corporations writing Marathi or Hindi cluster at the top.
Bodies publishing mostly English tender notices — Pune Metro, MHADA — have a
scan problem instead: real, well studied, and a different problem needing a
different fix.

## How it works

Three scripts, run in order.

```bash
python collect.py --dry-run          # discover PDFs, download nothing
python collect.py --per-source 60    # random sample, capped per body
python audit_corpus.py               # audit, write results, print report
```

`collect.py` honours `robots.txt`, respects each site's stated crawl delay
(2s floor), identifies itself, and records provenance for every file — source
URL, SHA-256, size, issuing body, timestamp — in `manifest.sqlite`.

`font_audit.py` sorts each document into one of five buckets using PyMuPDF.
`audit_corpus.py` runs it across the corpus and writes results back into the
same database, joined to provenance, so any number in the report traces back
to a checksum and a URL.

```bash
python font_audit.py <folder>        # audit a folder directly
python export_manifest.py            # build the releasable manifest
python -m pytest tests/              # 41 tests
```

### Ground truth

Phase 1 measures per font but stores per document, so only convictions survive
— a font that was measured and cleared leaves no record, and no threshold can
be re-tuned without re-reading every PDF. Phase 0 adds the missing grain: one
row per (document, font) with every signal stored, plus the extracted text
itself, so annotation and threshold sweeps both become queries.

```bash
python phase0_schema.py              # create the tables (once)
python extract_observations.py       # one pass: 6,572 font observations
python extract_observations.py --verify   # reconcile against the audit
python draw_annotation_sample.py     # stratified draw, records selection probability
python annotate.py --next            # label one observation
python llm_annotate.py --submit      # second-opinion pass, Batches API
```

Design, label definitions, and the adjudication protocol are in
[`docs/phase0-schema.md`](docs/phase0-schema.md).

## Design decisions worth knowing

**Classify by output, not by font name.** Every time the name-based approach
missed something, the fix was to look at what the text actually is. **32% of
the legacy documents are caught by output alone** — no font name matched:

- The **structural check** finds impossible Devanagari — vowel signs starting
  words, two matras adjacent — without consulting any font list. On one
  document it caught 492 violations where the original detached-matra heuristic
  found 29.
- The **mojibake check** catches Marathi 8-bit fonts by their Latin-1 output,
  including deliberately obfuscated subset names like `TT274t00` and
  `Z@RAF1C.tmp` that no pattern could ever match.
- The **ASCII-remap check** catches the Hindi Kruti Dev family, which maps onto
  plain ASCII and so carries no high bytes at all. It works because Kruti Dev
  is a *known fixed mapping*: it sends ASCII `k` to ा, the commonest character
  in written Hindi, so encoded text inherits that frequency onto English's
  rarest letter. Measured across the corpus, genuine English tops out at 2.5%
  `k` while Kruti-Dev-encoded Hindi starts at 10.2%; the threshold sits in the
  gap with 2× margin either side.
- The **per-font detector** runs all of the above *per font* rather than per
  document, using PyMuPDF's span-level font tagging. This is the one that
  mattered most. A document mixes fonts, so a legacy face sitting beside
  English headers produces a blended signal that trips no threshold. Measured
  across 450 documents against fonts identifiable by name, the same signals
  separate cleanly once undiluted:

  | | known-good max | legacy |
  |---|---|---|
  | mojibake ratio | 0.070 | median 0.684 |
  | ASCII `k` | 0.021 | p90 0.182 |
  | symbol-in-word | 2.67 /1k | 39.68 /1k |

  That third row is the payoff. At document level it was useless — clean
  documents reached 53.8 hits per 1000 characters against 58.1 for corrupt
  ones — and was rejected twice for exactly that reason. Per font it separates
  by 15×. **Validated at precision 1.000**, zero false positives across 91
  known-good fonts.

**Detectors are rejected more often than shipped.** Vowel ratio and
punctuation-inside-words were both measured and dropped for overlapping
legitimate text. A detector that over-fires pushes the headline *up*, which is
the flattering direction and therefore the one to distrust most.

The one false positive that did slip through is instructive: a digital
signature block rendered in Myriad Pro — `"Vihar Ashok Bodke Digitally signed
by …"` — hit 7.8% `k` across 154 characters, purely because Indian personal
names are dense in that letter. A calibration test caught it, and the fix was a
sample-size floor rather than a threshold change.

**`UNCLASSIFIED` is a bucket, not an error.** When a font can't be identified
either way, saying so beats guessing. Every defect found in the original audit
tool pushed the estimate *down* — a tool that fails toward "nothing to see
here" will talk you out of your own project. Making unclassifiable input
visible is what surfaced four undocumented legacy families:

| Family | Evidence |
|---|---|
| `MGShree` | `ukxij egkuxjikfydk` = नागपूर महानगरपालिका |
| `Sakal Marathi` | 8-bit garbage, zero Devanagari characters |
| `DVBW-TTBhima`, `DVBW-TTRadhika` | same |
| `APS-C-DV-*` (10 variants) | `‚ã. ‰ãŠ. ‡ãŠã¾ããÃÊã¾ããÞãñ` |

**141 distinct legacy variants observed in total**, against a starting list of
about 20 patterns. Each new family was confirmed by reading its extracted
text, never inferred from the name.

Some of those names are not names at all — `TT2C1t00`, `Z@RAF1C.tmp`. The last
one is a Windows temporary filename that leaked into the PDF as a font
identifier. Nothing that reads font names could ever classify these, which is
the whole argument for detecting the output signature instead.

**The threshold was fixed before the data existed.** Pre-registering is what
stops a number drifting to meet whatever you find, and it's what would have
made a negative result publishable rather than embarrassing.

**Sample randomly, cap per source.** One portal shares one template and one
font, so an unbalanced corpus measures that portal rather than the problem.
PMC alone offers 1,200+ documents and would otherwise have supplied most of
the sample. The seed is fixed, so the draw is reproducible.

## Scope

This is not the first Indian document dataset — [IndicDLP](https://arxiv.org/)
(ICDAR 2025, AI4Bharat / IIT Madras / IIIT Hyderabad) covers layout parsing
across 120k pages. It is not the first Devanagari OCR benchmark either; arXiv
2606.29213 covers OCR on scans.

Neither looks at the **digital text layer**, because in English that has been
a solved problem for twenty years. That gap is what this project occupies.

## Limits

Worth stating plainly, because they bound what these figures mean.

1. **Three states, all Devanagari.** Maharashtra (Marathi) and the Hindi belt
   (UP, Bihar) are covered. Southern-language bodies — Tamil, Telugu, Kannada,
   Malayalam — are untested, their legacy font ecosystems differ again, and
   neither the Devanagari structural check nor the ASCII-remap detector
   transfers to them unchanged.
2. **Scans are out of scope.** 40.3% of the corpus has no text layer at all.
   That's an OCR problem, already well studied, and not what this measures.
3. **`UNCLASSIFIED` has no verified positive control.** It sits at 0.5% and is
   exercised only by a synthetic test that removes `shree-dev` from the
   pattern list to simulate an undocumented font.
4. **Homepage-reachable bias in early sampling** was corrected by depth
   crawling, but discovery still cannot see documents behind form navigation.
5. **PMC's first sample was biased and had to be discarded.** Its documents
   come from a Drupal JSON:API holding ~25,000 PDFs in upload order, and the
   first pass walked only the oldest 1,227 — a slice of time rather than a
   sample of PMC. It reported 0% legacy. Redrawn by random offset across the
   full range, PMC reports 10% corrupt. Sampling method, not data, accounted
   for the entire difference.
6. **PMC's n is 49**, smaller than the other bodies, because random-offset
   sampling draws from a set that includes non-document uploads. Its figures
   carry correspondingly wider error bars.

## Data and licensing

The corpus is **not redistributed**, and that's a deliberate call rather than
an oversight. The sources do not share a licence: Nashik MC restricts
redistribution "for commercial purposes, including on other websites",
GODL-India permits commercial reuse with attribution, and four of six bodies
publish no discoverable terms at all. No single licence could describe the
collection honestly.

What is released is the manifest — every measurement, every source URL, every
checksum — plus a rebuild script that re-fetches from the originals and
verifies each file against its SHA-256. Government sites re-path constantly,
so the rebuild reports mismatches rather than quietly producing a different
corpus and calling it the same one.

Only official `.gov.in` and official body domains are collected. No commercial
tender aggregators, no Scribd.

**The code and the corpus are licensed separately.** The code in this
repository is MIT ([`LICENSE`](LICENSE)). That says nothing about the
documents: they are not ours to relicense, and the MIT grant does not extend to
them. Details in [`docs/LICENSING.md`](docs/LICENSING.md).

## Documents

- [`docs/phase1-results.md`](docs/phase1-results.md) — the full result, with
  breakdowns and caveats
- [`docs/phase1-recon.md`](docs/phase1-recon.md) — what was wrong with the
  original audit tool, and the source survey
- [`docs/LICENSING.md`](docs/LICENSING.md) — what each source permits

## Phases

| | | |
|---|---|---|
| 0 | Schema, annotation guidelines | **done** |
| 1 | Collection + font/encoding audit | **done — GO** |
| 2 | Ground truth: LLM draft + human verification | in progress |
| 3 | Benchmark pdftotext, pdfplumber, Surya, PaddleOCR, a VLM | later |
| 4 | Legacy-font converter + constraint validation | later |
| 5 | Write-up and release | later |

Phase 2 is underway: 6,572 font observations extracted and reconciled, 434
drawn for annotation, 0 labelled so far.
