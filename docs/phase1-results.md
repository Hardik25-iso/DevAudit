# Phase 1 Results — Go/No-Go

**Decision: GO.** `LEGACY + SUSPECT = 36.9%`, against a threshold of 15% fixed
before any data was collected.

## Corpus

1,064 documents from 8 issuing bodies across 3 states, drawn randomly with a
fixed seed (`20260807`) and capped per body so no single portal dominates.
Full provenance — source URL, SHA-256, size, retrieval timestamp — in
`manifest.sqlite`, joined to every audit measurement.

## Headline

| Bucket | n | % |
|---|---|---|
| No text layer (scan) | 416 | 39.1% |
| **Legacy non-Unicode fonts** | **260** | **24.4%** |
| **Suspect (invalid Devanagari)** | **133** | **12.5%** |
| Unclassified font | 6 | 0.6% |
| Clean Unicode | 249 | 23.4% |

**Only 23.4% of these documents have a text layer that is both present and
trustworthy.**

Set the scans aside — they are an OCR problem, well studied, and not what this
measures. Among the 648 documents that *do* carry a text layer, **60.6% of it
is wrong.** Three fifths of the machine-readable Devanagari published by these
bodies does not say what it appears to say.

## By issuing body

| Body | State | n | scan | legacy | suspect | uncl | clean |
|---|---|---|---|---|---|---|---|
| Nashik MC | MH | 190 | 13% | **69%** | 8% | 1% | 9% |
| **Lucknow MC** | **UP** | 54 | 30% | **57%** | 0% | 4% | 9% |
| Pimpri-Chinchwad MC | MH | 183 | 12% | 18% | **60%** | 1% | 9% |
| Patna MC | BR | 13 | 46% | 31% | 0% | 0% | 23% |
| MHADA | MH | 201 | 63% | 21% | 1% | 0% | 15% |
| Pune Municipal Corp | MH | 112 | 50% | 11% | 4% | 0% | 36% |
| Nagpur MC | MH | 183 | 48% | 3% | 2% | 0% | 48% |
| Pune Metro | MH | 128 | 60% | 1% | 0% | 1% | 38% |

The spread is enormous — 78% corrupt at PCMC, 1% at Pune Metro — and it is not
random. What predicts corruption is whether a body publishes in an Indian
language at all. Municipal corporations writing Marathi or Hindi cluster at the
top; bodies publishing mostly English tender notices have a scan problem
instead.

Patna is reported for completeness, but `n = 13` is too small to carry weight.

## The finding is not Marathi-specific

| Region | n | LEGACY+SUSPECT | scan |
|---|---|---|---|
| Maharashtra (Marathi) | 997 | 35.9% | 39.5% |
| Hindi belt (UP, Bihar) | 67 | **52.2%** | 32.8% |

This was the most plausible deflating explanation for the whole result — that
the corruption tracks a Marathi-specific font ecosystem (`Shree-Dev`,
`Sakal Marathi`, `DVBW-TT`) rather than Indian government publishing generally.
It does not. Lucknow, in Hindi, with an entirely different font ecosystem, sits
at 57% legacy, second only to Nashik, and the Hindi belt as a whole runs 16
points above Maharashtra.

The Hindi-belt sample is small and should be expanded before the number is
quoted precisely. The direction is not in doubt.

## Three corruption mechanisms

The starting hypothesis named one. The corpus contains three.

**1. Legacy 8-bit fonts, Marathi style.** Glyphs mapped onto the Latin-1
supplement. Extracts as `xÉÉÊ¶ÉEò ¨É½þÉxÉMÉ®ú{ÉÉÊ±ÉEòÉ` (= नाशिक महानगरपालिका).

**2. Legacy ASCII remapping, Hindi style.** Kruti Dev and relatives map onto
*plain ASCII*, so the text carries no high bytes at all. Extracts as
`i'kq dY;k.k foHkkx` (= पशु कल्याण विभाग). This class was invisible to the
detector built for class 1, and three such documents were sitting in `CLEAN`
until it was fixed.

**3. Correct fonts, wrong CMaps.** Documents set in genuine Unicode faces
(Mangal, Aparajita, Adobe Devanagari) whose ToUnicode tables are wrong, usually
after a PDF post-processor. Extracts as Devanagari that is *structurally
impossible* — vowel signs beginning words, two matras adjacent. Accounts for
12.5 of the 36.9 points, and every font name in these files looks perfectly
respectable.

## Font discovery

**121 distinct legacy font variants observed**, against a starting list of
about 20 patterns. Families the starting list missed entirely, each confirmed
by reading extracted text rather than inferred from the name:

| Family | Evidence |
|---|---|
| `MGShree` | `ukxij egkuxjikfydk` = नागपूर महानगरपालिका |
| `Sakal Marathi` | `´ÖÖ×ÆüŸÖß®Öã×ÃŸÖÛêú`, zero Devanagari chars |
| `DVBW-TTBhima`, `DVBW-TTRadhika` | same 8-bit garbage |
| `APS-C-DV-*` (10 variants) | `‚ã. ‰ãŠ. ‡ãŠã¾ããÃÊã¾ããÞãñ` |
| `TT274t00`, `TT313t00`, `Z@RAF1C.tmp` | `xÉÉÊ¶ÉEò`, `xzke&cjkoudyk` |

### Classifying the 68 unidentified fonts

All 68 were classified by extracting text **per font** — PyMuPDF tags each text
span with the font that rendered it, so each font is judged on its own output
rather than the document's.

| Classification | n | Evidence |
|---|---|---|
| Legacy, 8-bit (Marathi style) | 8 | mojibake ratio 0.55–0.76 |
| Legacy, ASCII remap (Hindi style) | 10 | `k` frequency 0.096–0.362 |
| **Legacy, third family** | **9** | see below |
| Devanagari present but invalid | 4 | `महापािलका आयु यांचे` |
| Devanagari, no violations | 5 | benign |
| Genuine Latin | 2 | `Account Description of Items Schedule` |
| Too little text to judge | 30 | used for a handful of glyphs each |

**Only 2 of the 68 were genuine Latin fonts.** 31 are confirmed legacy. The
remaining 35 are either benign Devanagari or carry too little text to call.

Of the 68, **65 are auto-generated subset identifiers** (`TT2F3t00`,
`TTE2A70A90t00`, `Z@RAF1C.tmp`) rather than font names. They are deliberately
*not* added to `LEGACY_PATTERNS`: the same string denotes a different font in a
different PDF, so matching on them would misfire. Only three real names turned
up in the whole set — `Algerian`, `MV Boli`, `RomanT` — and those went to the
known-good list.

### A third legacy encoding family

Nine fonts emit text that is neither Latin-1 mojibake nor Kruti-Dev ASCII:

```
TT270t00       ^l.^glmlzaG$l @l_vº$ UVl àdlfG$
TT2C0t00       mvTn mnQ= Jln`mR=n zf.f.G«$. 334, 335/1@/336
Z@RAF3D.tmp    godmd¥Îmr doVZ d A§eXmZ {ZYr {hñgm
```

Mojibake ratio sits at 0.04–0.20, below the 8-bit threshold, and `k` frequency
is 0.000, so neither existing detector fires. The signature is `$`, `«` and `§`
appearing *between* letters.

Measured at document level, that signal does not separate: clean documents
reach 53.8 hits per 1000 characters against 58.1 for the affected ones, so it
was rejected twice as a document-level rule.

**Per font it separates by a factor of 15** — known-good fonts top out at 2.67
hits per 1000 characters against 39.68 for this family — because the signal is
no longer diluted by whatever else the document contains. That is what the
per-font detector below exploits.

### The per-font detector

The single most productive change to the instrument. Instead of measuring a
document's aggregate text, PyMuPDF's span-level font tagging is used to
accumulate text **per font**, and each font is judged on its own output.

A document mixes fonts. A legacy Devanagari face sitting beside English
headers, page numbers and tables produces a blended signal that lands between
the two populations and trips no threshold. Undiluted, the same measurements
separate cleanly. Measured across 450 documents, using fonts identifiable by
name as labelled ground truth (41 legacy, 53 known-good):

| Signal | Known-good max | Legacy | Ratio |
|---|---|---|---|
| Mojibake ratio | 0.070 | median 0.684 | ~10× |
| ASCII `k` frequency | 0.021 | p90 0.182 | ~9× |
| Symbol-in-word | 2.67 /1k | 39.68 /1k | ~15× |

**Validation: precision 1.000** — zero false positives across 91 known-good
fonts. Recall is 0.508 overall, but that number is misleading: of the fonts it
declined to convict, 18 carried too little text to judge and 10 render genuine
Devanagari and are deliberately deferred to the structural check. On fonts it
can actually judge, recall is 30/31.

Effect: `LEGACY` rose from 205 to 260 documents, `UNCLASSIFIED` fell from 16 to
6, and the headline moved from 32.3% to 36.9%. Spot-checking the documents it
newly convicted found `xÉÉÊ¶ÉEò ¨É½þÉxÉMÉ®ú{ÉÉÊ±ÉEòÉ` (= नाशिक महानगरपालिका) at
mojibake ratios of 0.46–0.76 — unambiguously legacy-encoded.

#### The false positive it produced

One document regressed: a Latin-only NHAI tender, previously `CLEAN`, was
convicted on a digital signature block rendered in Myriad Pro —
`"Vihar Ashok Bodke Digitally signed by …"` — which hit 7.8% `k` across 154
characters purely because Indian personal names are dense in that letter.

It was caught by a calibration test, not by the 450-document validation, which
had happened to sample a larger instance of the same font. The fix was a
sample-size floor of 200 letters for the `k` test rather than a threshold
change, since genuine Kruti-Dev documents run to thousands of characters.

Worth recording because it is the failure mode this project is most exposed
to: a detector firing in the flattering direction on a small sample.

### How each corrupt document was caught

| Route | n |
|---|---|
| Font name matched the list | 176 |
| **Output signature only — no name matched** | **84** |

Those 84 — 32% of all legacy documents — are the argument for the whole approach. `Z@RAF1C.tmp` is a Windows
temporary filename that leaked into a PDF as a font identifier; `TT313t00` is a
deliberately opaque subset name. No name-based classifier could ever handle
them, and a third of the legacy documents would have been missed.

## Method notes worth carrying into the write-up

**The threshold was pre-registered.** Fixed at 15% before collection, so it
could not drift to meet the data. That is also what would have made a negative
result publishable rather than embarrassing.

**Two sampling errors were found and corrected**, both of which had produced
plausible-looking numbers:

- PMC was drawn from the oldest 1,227 of its ~25,000 PDFs — a slice of time
  rather than a sample — and reported 0% legacy. Redrawn by random offset
  across the full range, it reports 9% corrupt. Sampling method, not data,
  accounted for the entire difference.
- Early discovery read seed pages only, biasing toward short scanned notices.
  Corrected by depth crawling.

**The headline is robust to the font list.** Adding four font families and two
output-signature detectors moved it from 26.3% to 36.9%, and reclassified
documents largely moved *between* `SUSPECT` and `LEGACY` while already being
counted. Two independent signals cover the same files, so the number does not
depend on the font list being complete — which was the main threat to its
validity.

## Limits

1. **Three states, all Devanagari.** Tamil, Telugu, Kannada and Malayalam
   bodies are untested. Their legacy ecosystems differ again, and neither the
   structural check nor the ASCII-remap detector transfers unchanged.
2. **Hindi-belt `n` is small** (67 across two bodies, one of which contributes
   13). Expand before quoting 52.2% precisely.
3. **68 fonts remain unclassified**, so the true rate is understated rather
   than overstated.
4. **Per-body `n` exceeds the intended 60** for several sources, through
   repeated collection passes. Each draw is independent and random so the
   samples stay unbiased, but the sampling section of any write-up should be
   reconstructed from `manifest.sqlite` rather than from narrative.
5. **`UNCLASSIFIED` has no verified positive control** in the test suite; only
   a synthetic test that removes `shree-dev` from the pattern list exercises it.

## Next

Phase 1's question is answered, so Phase 0 (schema, annotation guidelines) and
Phase 2 (ground truth) are unblocked.

Cheap work worth doing first, while the collector is warm:

- Expand the Hindi-belt sample beyond 67 documents.
- Add one southern-language body to test whether the detectors transfer at all.
- Classify the 68 remaining unknown fonts; each one found so far has been a
  real legacy family.
