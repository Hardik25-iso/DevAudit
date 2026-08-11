# DevAudit — Indian Government Document Text-Layer Audit

Research project investigating a specific, under-examined failure mode in Indian
government PDFs: **a text layer that extracts "successfully" and is
linguistically wrong.**



## The problem

Many Indian government PDFs are produced with legacy non-Unicode Devanagari
fonts (Shree-Dev, Kruti Dev, DV-TTSurekh, Chanakya). These map Devanagari glyphs
onto ASCII codepoints in *visual* order rather than *logical* order.

The consequence:

- `pdftotext` reports success and returns Devanagari-looking text
- The text is linguistically wrong — reordered vowel signs, substituted
  consonants, spurious spaces
- **No error is raised anywhere in the pipeline**
- Every downstream system (search, RAG, datasets) silently inherits it

Example from a municipal budget document:

| Extracted | Correct | Failure |
|---|---|---|
| `जानवे ारी` | `जानेवारी` | vowel sign moved |
| `स्थालनक सांस्था कर` | `स्थानिक संस्था कर` | न→ल, ं→ां |
| `लमळकत` | `मिळकत` | consonant substituted |
| `जमा बाज ू` | `जमा बाजू` | matra detached |

## Scope

This is **not** a claim to be the first Indian document dataset — IndicDLP
(ICDAR 2025, AI4Bharat / IIT Madras / IIIT Hyderabad) covers layout parsing on
120k pages. It is **not** the first Devanagari OCR benchmark — arXiv 2606.29213
covers OCR on scans.

Neither examines the **digital text layer**, because in English that is a solved
problem. That gap is this project's.

## Phases

| Phase | What | Status |
|---|---|---|
| 0 | Threat/scope definition, extraction schema, annotation guidelines | not started |
| 1 | Collection + font/encoding audit at scale | **in progress** |
| 2 | Ground truth: LLM-assisted draft + human verification | later |
| 3 | Benchmark existing tools | later |
| 4 | Legacy-font converter + constraint-based validation | later |
| 5 | Write-up, dataset release | later |

## Phase 1 — go/no-go gate

Phase 1 exists to answer one question: *what percentage of Indian government
PDFs have (a) no text layer, (b) legacy non-Unicode fonts, (c) structurally
invalid Devanagari, (d) clean Unicode text?*

Decision rule, pre-registered before any numbers were seen:

- **Go:** `LEGACY + SUSPECT >= 15%`, or `LEGACY + SUSPECT >= 5%` with
  `UNCLASSIFIED >= 15%`
- **Pivot:** all three under 10% combined
- **In between:** hand-inspect 30 `UNCLASSIFIED` files, then decide. If
  `UNCLASSIFIED` is too small to sample 30, draw from `LEGACY + SUSPECT`
  instead and use the confirmed-corruption rate among them.

A negative result is a successful week, not a failure.

## How Phase 1 is sequenced, and why

The order matters more than the code does.

| # | Step | Why here |
|---|---|---|
| 1 | Survey the sources | Reachability is the only thing that can kill the phase outright. Learn it before building anything that assumes it. |
| 2 | Rewrite + calibrate the audit | The audit is the measuring instrument. An uncalibrated instrument produces a number that *looks* valid. |
| 3 | Collect the corpus | Only once the instrument is trusted. |
| 4 | Run the audit, report | The go/no-go number. |

**Why the scraper is last.** If the heuristics are wrong, finding out on 20
hand-picked files costs an afternoon. Finding out on 400 scraped files costs the
week — and nothing about the output would look wrong.

**Why the decision rule was written before any data.** Pre-registering the
threshold is what stops it drifting to meet whatever the data turns out to say.
It is also what makes a negative result publishable rather than embarrassing.

**Why `UNCLASSIFIED` is a bucket and not an error.** Every defect found in the
audit tool (§1 of the recon) pushed the corruption estimate *down*. A tool that
fails toward "nothing to see here" will always talk you out of your own project.
Making unclassifiable input visible is the fix.

## Documents

- [`docs/phase1-results.md`](docs/phase1-results.md) — **the go/no-go result**,
  breakdowns by issuing body, font discovery, and known limits
- [`docs/phase1-recon.md`](docs/phase1-recon.md) — audit tool findings and the
  source reachability survey
- [`docs/LICENSING.md`](docs/LICENSING.md) — what each source permits, and why
  the release is the manifest rather than the documents

## Result

**Phase 1: GO.** `LEGACY + SUSPECT = 27.8%` across 407 documents from 6
issuing bodies, against a pre-registered threshold of 15%. Only 31.4% of the
corpus has a text layer that is both present and trustworthy.

Two independent corruption mechanisms were found, not one: legacy 8-bit fonts,
and correct Unicode fonts carrying wrong ToUnicode CMaps. Font-name matching is
blind to the second, which accounts for 12.8 of the 27.8 points.

## Licence / data ethics

Only official `.gov.in` and official body domains are collected. No commercial
tender aggregators, no Scribd — unclear licensing makes a dataset
unpublishable. Every source's terms are logged before collection, not during
write-up.
