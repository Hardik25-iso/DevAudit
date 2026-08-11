# Phase 1 Results — Go/No-Go

**Decision: GO.** `LEGACY + SUSPECT = 27.8%`, against a pre-registered go
threshold of 15%.

## Corpus

407 documents from 6 issuing bodies, drawn randomly (seed `20260807`) from
3,846 discovered URLs, capped at 60 per body so no single portal dominates.
Full provenance — source URL, SHA-256, size, retrieval timestamp — in
`manifest.sqlite`.

## Headline

| Bucket | n | % |
|---|---|---|
| No text layer (scan) | 164 | 40.3% |
| **Legacy non-Unicode fonts** | **61** | **15.0%** |
| **Suspect (invalid Devanagari)** | **52** | **12.8%** |
| Unclassified font | 2 | 0.5% |
| Clean Unicode | 128 | 31.4% |

**Only 31.4% of these documents have a text layer that is both present and
trustworthy.** A further 27.8% have a text layer that extracts without error
and is wrong — which is the failure mode this project exists to measure.

## By issuing body

| Body | n | scan | legacy | suspect | clean |
|---|---|---|---|---|---|
| Pimpri-Chinchwad MC | 65 | 15% | 15% | **58%** | 11% |
| Nashik MC | 70 | 13% | **47%** | 9% | 30% |
| MHADA | 96 | 65% | 17% | 1% | 18% |
| Nagpur MC | 69 | 49% | 3% | 4% | 43% |
| Pune Metro | 60 | 62% | 0% | 0% | 38% |
| PMC | 47 | 26% | 0% | 9% | 64% |

The problem is **not uniform**, and that is the most useful thing in this
table. It concentrates in Marathi-language municipal documents: PCMC is 73%
corrupt, Nashik 56%. Bodies producing English tender notices (Pune Metro,
MHADA) are dominated by scans instead — a different problem with a different
fix.

Sampling caveat worth stating plainly: **PMC shows 0% legacy here, yet the
document that motivated this entire project is a PMC budget set in
Shree-Dev.** The PMC sample was drawn from the Drupal JSON:API in upload
order, and the budget PDFs evidently did not fall in the draw. PMC's true rate
is unmeasured, not zero.

## Two corruption mechanisms, not one

The original hypothesis was legacy non-Unicode fonts. The corpus shows a
second, independent mechanism:

1. **Legacy 8-bit fonts** (61 docs) — Shree-Dev, Kruti Dev, DV-TT, APS-C-DV
   and others map Devanagari glyphs onto ASCII. Text extracts as garbage.
2. **Correct fonts, wrong CMaps** (52 docs) — documents set in *Unicode*
   faces (Mangal, Aparajita, Adobe Devanagari, Gabriola) whose ToUnicode
   mappings are wrong, usually after a PDF post-processor. Text extracts as
   Devanagari that is structurally impossible.

Font-name matching cannot see the second class at all. That is why the
structural check exists, and it accounts for 12.8 points of the 27.8.

## Font discovery

**67 distinct legacy font variants observed**, against a starting list of ~20
patterns. Families the starting list missed entirely, each confirmed by
reading extracted text rather than inferred from the name:

| Family | Evidence |
|---|---|
| `MGShree` | `ukxij egkuxjikfydk` = नागपूर महानगरपालिका |
| `Sakal Marathi` | `´ÖÖ×ÆüŸÖß®Öã×ÃŸÖÛêú`, 0 Devanagari chars |
| `DVBW-TTBhima`, `DVBW-TTRadhika` | same 8-bit garbage |
| `APS-C-DV-*` (10 variants) | `‚ã. ‰ãŠ. ‡ãŠã¾ããÃÊã¾ããÞãñ` |
| `TT274t00`, `TT2F1t00`, `TT2F3t00` | `xÉÉÊ¶ÉEò ¨É½þÉxÉMÉ®ú{ÉÉÊ±ÉEòÉ` = नाशिक महानगरपालिका |

The last row forced a design change. Those names are **deliberately
obfuscated subsets** — no name pattern can ever match them. So the audit now
also detects the 8-bit *output signature*: dense Latin-1 supplement text, zero
Devanagari, embedded fonts with no ToUnicode. That reclassified 6 documents
and dropped `UNCLASSIFIED` from 1.7% to 0.5%.

This is the same move twice: **classify by what the text actually is, not by
what the font claims to be.** Both times it caught what the name-based
approach could not.

## Robustness

The estimate moved from 26.3% (76-document pilot) to 27.8% (407 documents,
random draw). Adding four font families and the mojibake detector moved it by
1.5 points, because documents reclassified between `SUSPECT` and `LEGACY` were
already being counted — two independent signals covering the same files. The
headline number is therefore not sensitive to the completeness of the font
list, which was the main threat to its validity.

## Known limits

1. **PMC is unmeasured**, per the sampling caveat above.
2. **Scans are 40.3%** and are simply out of scope for a text-layer study;
   the corrupt-text rate among documents that *have* a text layer is
   `113/243 = 46.5%`, which is arguably the more meaningful figure.
3. **One state.** Every body is in Maharashtra, so this measures Marathi
   Devanagari practice, not "Indian government PDFs" generally. Hindi-belt
   and southern-language bodies are untested.
4. **`UNCLASSIFIED` is now 0.5%** (2 documents) and still has no verified
   positive control in the test suite; only the synthetic monkey-patch test
   exercises it.

## Next

Phase 1's question is answered, so Phase 0 (schema, annotation guidelines) and
Phase 2 (ground truth) are unblocked. Before that, two cheap things worth
doing while the collector is warm:

- Re-sample PMC deliberately targeting budget documents, to measure the source
  the project was named after.
- Add a second state's municipal bodies to test whether the 27.8% generalises
  beyond Marathi.
