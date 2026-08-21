# What this project is

Two explanations of the same thing. Part 1 assumes nothing. Part 2 assumes you
work with PDFs, text encoding, or measurement.

---

# Part 1 — In plain language

## The problem

Open a PDF from an Indian government website — a municipal budget, a tender
notice. It looks completely normal on screen. The Marathi or Hindi text is
right there, readable.

Now select that text and copy it. Paste it somewhere.

You get garbage. Not always — but often. Sometimes it's obvious garbage like
`xÉÉÊ¶ÉEò ¨É½þÉxÉMÉ®ú{ÉÉÊ±ÉEòÉ`. Sometimes it looks like real Marathi but the
letters are subtly in the wrong order, the way `nihgt` is almost `night`.

**The critical part: nothing warns you.** No error, no red flag. The computer
thinks it succeeded. The PDF displays perfectly. Only the invisible text layer
underneath — the part machines read — is wrong.

## Why that matters

A PDF is really two things stacked on top of each other: a *picture* of the
page, and a *text layer* that machines read. Humans see the picture. Search
engines, screen readers, and AI systems read the text layer.

When the text layer is corrupt but the picture is fine:

- **Search doesn't work.** Someone searching the municipal website for a scheme
  name won't find the document that contains it.
- **Screen readers fail.** A blind citizen gets gibberish read aloud.
- **AI systems learn from it.** These documents get scraped into training data
  and retrieval systems. The corruption is inherited silently.
- **Archives preserve the damage.** Long after the original file is gone, the
  broken text is what remains.

And because nobody sees an error, nobody knows to fix it.

## Why it happens

Before Unicode was widespread, Indian publishers used custom fonts that
cheated. Instead of storing "this is the Devanagari letter क", they stored
"this is the letter k" and used a font that *drew* क wherever a k appeared.

Looks right on screen. But the file genuinely contains the letter k. Anyone
reading the text gets `k`, not क.

These fonts are decades old and still in daily use in government offices.

## What we set out to do

Four questions, in order:

1. **Is this actually common,** or a few unlucky files? Nobody had measured it.
2. **Can a program detect it automatically,** without a human reading every
   document?
3. **Does that detector actually work,** or does it just look like it works?
4. **Can the damage be repaired,** and which tools repair it best?

## What we found

We collected **1,602 documents from 8 government bodies across 3 states**,
downloaded legally and at random, and checked every one.

**Roughly 36 to 48 out of every 100 documents have a broken text layer.** The
range is because you can count two ways — per document, or per government body
— and honest reporting means giving both.

A third of the documents are just scanned images with no text at all. That's a
different, well-known problem, so we set those aside. **Of the documents that
do have text, about 70% of it is wrong.**

We also found the problem isn't one problem. It's at least three different
mechanisms, and the obvious way of detecting it — checking the font's name —
misses about a third of cases, because many fonts have meaningless names like
`TT313t00` or, in one real case, a leftover Windows temporary filename.

## Where the project stands

Detection works and has been measured. Repair hasn't been attempted yet.

The honest caveat: to prove the detector works, you need a set of documents
where a human has confirmed what's actually wrong. We built that set but only
partly finished checking it, so the current accuracy numbers are strong
evidence rather than proof. That limitation is written into the results
documents rather than hidden.

---

# Part 2 — In technical terms

## The failure mode

Legacy non-Unicode Indic fonts map Devanagari glyphs onto ASCII or Latin-1
codepoints in **visual order** rather than logical order. The PDF renders
correctly because the font supplies the right glyph shapes; the extracted text
is wrong because the codepoints are wrong.

`pdftotext` exits 0. There is no signal of failure anywhere in the pipeline.

## Three distinct mechanisms

| Mechanism | Extracts as | Actually |
|---|---|---|
| Legacy 8-bit (Marathi style) | `xÉÉÊ¶ÉEò ¨É½þÉxÉMÉ®ú{ÉÉÊ±ÉEòÉ` | नाशिक महानगरपालिका |
| Legacy ASCII remap (Kruti Dev family) | `i'kq dY;k.k foHkkx` | पशु कल्याण विभाग |
| Correct font, broken `ToUnicode` CMap | `जानवे ारी` | जानेवारी |

The third is the nastiest: the font is a legitimate Unicode face (Mangal,
Aparajita), the font *name* looks respectable, and the output is real
Devanagari codepoints — just structurally impossible ones, such as a dependent
vowel sign opening a word.

Annotation later showed each of these three labels covers **several** distinct
byte mappings — at least five for the 8-bit case alone.

## Detection approach

**Classify by output, not by font name.** 32% of affected documents match no
known font name; some names are auto-generated subset IDs (`TT313t00`) or, in
one case, a leaked Windows temp filename (`Z@RAF1C.tmp`).

**Measure per font, not per document.** This was the single most productive
change. A document mixes fonts, so a legacy face beside English headers
produces a blended signal that trips no threshold. Undiluted, the same
measurements separate by 15×.

Four signals, each targeting one mechanism:

| Signal | Detects | Threshold |
|---|---|---|
| `mojibake_ratio` | Latin-1 supplement density | 0.15 |
| `ascii_k_ratio` | Kruti Dev remap | 0.05 |
| `symbol_per_1k` | symbol-in-word remap | 10.0 |
| `invalid_rate_per_1k` | structurally impossible Devanagari | 2.0 |

`ascii_k_ratio` deserves explanation: Kruti Dev maps ASCII `k` onto ा, the
commonest character in written Hindi. Encoded text therefore inherits that
frequency onto English's rarest letter. Genuine English tops out at 2.5% `k`;
Kruti-Dev-encoded Hindi starts at 10.2%.

## Measurement design

Sampling is the recurring hazard, and it has produced wrong answers three times
in this project's history. Mitigations: random draws with fixed seeds, per-body
caps, and reporting the **macro average** (each body weighted equally) beside
the pooled figure, distrusting the pooled one when they disagree.

Thresholds were **pre-registered** before data existed, so a negative result
would have been publishable rather than embarrassing.

## Results

**Phase 1 — prevalence** (1,602 documents, 8 bodies, 3 states):

| | |
|---|---|
| No text layer (scan) | 31.5% |
| Legacy non-Unicode fonts | 37.2% |
| Structurally invalid Devanagari | 11.2% |
| Clean | 19.4% |
| **Corrupt, pooled** | **48.4%** |
| **Corrupt, macro by body** | **36.5%** |

**Phase 3 later established that these are a floor.** Comparing OCR of the
rendered page against the text layer catches legacy remaps hiding under font
names like `Helvetica` — invisible to every signal above. It fires on 42.8% of
scorable pages Phase 1 called clean, moving the estimate to **45.4% macro /
56.7% pooled**. Three controls hold: Pune Metro (English-publishing) moves 0 of
47 pages, `SUSPECT` fires at 1.7%, `LEGACY` at 68.1%.

Among documents that carry a text layer at all, 70.7% is wrong. Per-body rates
range from 1% (Pune Metro, publishes in English) to 80% (Nashik MC).

**Phase 2 — detector evaluation** (6,572 font observations, 434 labelled):

| | |
|---|---|
| precision | 0.975 |
| recall | 0.826 |
| false negatives vs false positives | 41 vs 5 |

The 41:5 asymmetry is the first quantification of a property asserted from the
start: **the instrument fails toward silence, never toward alarm.** Every defect
found in the original tool pushed the estimate *down*, which is why a detector
that over-fires is the one to distrust.

## Known weaknesses

- **`ascii_k_ratio` is a coverage gap, not a tuning problem.** It detects one
  encoding family. ISM, transliteration-style, and a third family map nothing
  onto `k`, so no threshold catches them. Needs a new signal per family.
- **`symbol_per_1k` does not work at any threshold** against labelled data,
  despite validating at precision 1.000 against name-identified fonts in
  Phase 1.
- **Labels are one pass.** The two-pass protocol with a pre-registered κ ≥ 0.7
  gate was not completed. Current figures are the detector's agreement with a
  single labelling pass — strong evidence, not validated accuracy. See
  `phase0-schema.md` §5.5.

## Scope limits

- **Devanagari only.** Southern-language legacy font ecosystems differ and
  neither the structural check nor the ASCII-remap detector transfers.
- **Scans are out of scope.** 31.5% of the corpus has no text layer; that is an
  OCR problem, already well studied.
- **`robots.txt` is a hard boundary.** Every `.nic.in` district site publishes
  through a CDN that disallows all crawling, making hundreds of bodies
  uncollectable. This bounds what any corpus of this kind can contain.
- **The corpus is not redistributed.** Sources have incompatible licences; what
  is released is the manifest — measurements, URLs, checksums — plus a rebuild
  script that verifies against SHA-256.

## Phases

| | | |
|---|---|---|
| 0 | Schema and annotation guidelines | done |
| 1 | Collection and audit | done — GO |
| 2 | Ground truth and detector evaluation | done, with a stated limitation |
| 3 | Benchmark extractors against ground truth | done |
| 4 | Legacy-font converter, constraint validation | done — partial |
| 5 | Write-up and release | in progress |

## Reading order

1. [`REPORT.md`](REPORT.md) — the consolidated write-up, start here
2. [`README.md`](../README.md) — the finding and how to run everything
3. [`phase1-results.md`](phase1-results.md) — prevalence, with breakdowns
4. [`phase2-results.md`](phase2-results.md) — detector evaluation and its limits
5. [`phase3-results.md`](phase3-results.md) — extractor benchmark, and the correction above
6. [`phase4-results.md`](phase4-results.md) — the converter, and where it falls short
7. [`phase0-schema.md`](phase0-schema.md) — schema, label definitions, protocol
8. [`LICENSING.md`](LICENSING.md) — what each source permits
