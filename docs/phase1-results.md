# Phase 1 Results — Go/No-Go

**Decision: GO.** `LEGACY + SUSPECT = 32.3%`, against a threshold of 15% fixed
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
| **Legacy non-Unicode fonts** | **205** | **19.3%** |
| **Suspect (invalid Devanagari)** | **139** | **13.1%** |
| Unclassified font | 16 | 1.5% |
| Clean Unicode | 288 | 27.1% |

**Only 27.1% of these documents have a text layer that is both present and
trustworthy.**

Set the scans aside — they are an OCR problem, well studied, and not what this
measures. Among the 648 documents that *do* carry a text layer, **53.1% of it
is wrong.** More than half the machine-readable Devanagari published by these
bodies does not say what it appears to say.

## By issuing body

| Body | State | n | scan | legacy | suspect | uncl | clean |
|---|---|---|---|---|---|---|---|
| Pimpri-Chinchwad MC | MH | 183 | 12% | 18% | **60%** | 1% | 9% |
| **Lucknow MC** | **UP** | 54 | 30% | **50%** | 0% | 7% | 13% |
| Nashik MC | MH | 190 | 13% | **47%** | 9% | 2% | 28% |
| Patna MC | BR | 13 | 46% | 23% | 8% | 0% | 23% |
| MHADA | MH | 201 | 63% | 20% | 1% | 0% | 15% |
| Pune Municipal Corp | MH | 112 | 50% | 4% | 5% | 4% | 37% |
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
| Maharashtra (Marathi) | 997 | 31.4% | 39.5% |
| Hindi belt (UP, Bihar) | 67 | **46.3%** | 32.8% |

This was the most plausible deflating explanation for the whole result — that
the corruption tracks a Marathi-specific font ecosystem (`Shree-Dev`,
`Sakal Marathi`, `DVBW-TT`) rather than Indian government publishing generally.
It does not. Lucknow, in Hindi, with an entirely different font ecosystem, has
the highest legacy rate of any body in the corpus.

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
13.1 of the 32.3 points, and every font name in these files looks perfectly
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

**68 fonts remain unidentified** and are recorded rather than guessed at.

### How each corrupt document was caught

| Route | n |
|---|---|
| Font name matched the list | 176 |
| **Output signature only — no name matched** | **29** |

Those 29 are the argument for the whole approach. `Z@RAF1C.tmp` is a Windows
temporary filename that leaked into a PDF as a font identifier; `TT313t00` is a
deliberately opaque subset name. No name-based classifier could ever handle
them, and 14% of the legacy documents would have been missed.

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
output-signature detectors moved it from 26.3% to 32.3%, and reclassified
documents largely moved *between* `SUSPECT` and `LEGACY` while already being
counted. Two independent signals cover the same files, so the number does not
depend on the font list being complete — which was the main threat to its
validity.

## Limits

1. **Three states, all Devanagari.** Tamil, Telugu, Kannada and Malayalam
   bodies are untested. Their legacy ecosystems differ again, and neither the
   structural check nor the ASCII-remap detector transfers unchanged.
2. **Hindi-belt `n` is small** (67 across two bodies, one of which contributes
   13). Expand before quoting 46.3% precisely.
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
