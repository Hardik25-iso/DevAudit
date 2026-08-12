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

**1,064 documents, 8 issuing bodies, three states, random sample, fixed seed.**

| | n | % |
|---|---|---|
| No text layer (scan) | 416 | 39.1% |
| **Legacy non-Unicode fonts** | **205** | **19.3%** |
| **Suspect (invalid Devanagari)** | **139** | **13.1%** |
| Unclassified font | 16 | 1.5% |
| Clean Unicode | 288 | 27.1% |

`LEGACY + SUSPECT = 32.3%`, against a threshold of 15% fixed before any data
was collected. **Only 27.1% of the corpus has a text layer that is both
present and trustworthy.**

Set the scans aside — they're an OCR problem, already well studied, and not
what this measures. Among the 648 documents that *do* carry a text layer,
**53.1% of it is wrong.** More than half the machine-readable Devanagari these
bodies publish does not say what it appears to say.

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

That last class alone is 13.1 of the 32.3 points, and every font name in those
files looks perfectly respectable.

### It isn't uniform either

| Body | State | n | scan | legacy | suspect | clean |
|---|---|---|---|---|---|---|
| Pimpri-Chinchwad MC | MH | 183 | 12% | 18% | **60%** | 9% |
| **Lucknow MC** | **UP** | 54 | 30% | **50%** | 0% | 13% |
| Nashik MC | MH | 190 | 13% | **47%** | 9% | 28% |
| Patna MC | BR | 13 | 46% | 23% | 8% | 23% |
| MHADA | MH | 201 | 63% | 20% | 1% | 15% |
| Pune Municipal Corp | MH | 112 | 50% | 4% | 5% | 37% |
| Nagpur MC | MH | 183 | 48% | 3% | 2% | 48% |
| Pune Metro | MH | 128 | 60% | 1% | 0% | 38% |

**The finding generalises beyond Marathi, and gets worse.** Lucknow — Hindi,
Uttar Pradesh, a different font ecosystem entirely — has the highest legacy
rate of any body at 50%. This is not a Marathi tooling quirk.

Patna is included for completeness but `n = 13` is too small to carry weight.

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
python -m pytest tests/              # calibration tests
```

## Design decisions worth knowing

**Classify by output, not by font name.** Three times the name-based approach
missed things, and every time the fix was to look at what the text actually is:

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
  gap with 2× margin either side. Adding it moved Lucknow from 33% to 50%
  legacy.

Two weaker discriminators were measured for that last one and **rejected**:
vowel ratio and punctuation-inside-words both overlap legitimate text. A
detector that over-fires pushes the headline *up*, which is the flattering
direction and therefore the one to distrust most.

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

**121 distinct legacy variants observed in total**, against a starting list of
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

Worth stating plainly, because they bound what the 32.3% means.

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

Details in [`docs/LICENSING.md`](docs/LICENSING.md).

## Documents

- [`docs/phase1-results.md`](docs/phase1-results.md) — the full result, with
  breakdowns and caveats
- [`docs/phase1-recon.md`](docs/phase1-recon.md) — what was wrong with the
  original audit tool, and the source survey
- [`docs/LICENSING.md`](docs/LICENSING.md) — what each source permits

## Phases

| | | |
|---|---|---|
| 0 | Schema, annotation guidelines | next |
| 1 | Collection + font/encoding audit | **done — GO** |
| 2 | Ground truth: LLM draft + human verification | after 0 |
| 3 | Benchmark pdftotext, pdfplumber, Surya, PaddleOCR, a VLM | later |
| 4 | Legacy-font converter + constraint validation | later |
| 5 | Write-up and release | later |

Phase 1 answered its question, so Phase 0 is unblocked.
