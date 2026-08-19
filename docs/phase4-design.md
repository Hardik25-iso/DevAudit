# Phase 4 — legacy-font converter and constraint validation

Settled with the author 2026-08-19, before any converter code was written.

Phase 3 established that **no extractor recovers legacy-encoded text** — five
arms, all failing, differing only in how. That closes the tool-choice route and
leaves exactly one: reverse the encoding. This phase builds that reverse
mapping and measures whether it works.

---

## 1. What a converter has to be

Measured, not assumed. For the dominant family, garbage and correct text are
near the same length (ratio 0.96 over 6 paired pages), so this is substitution
rather than ligature expansion:

```
xÉÉÊ¶ÉEò  ->  नाशिक
x=न   ÉÉ=ा   Ê=ि   ¶=श   Eò=क
```

Two components fall out of that:

1. **An ordered, longest-first replacement table** per family. Multi-character
   sequences (`Eò`→क, `ÉÉ`→ा) must be tried before single characters, or `E`
   matches first and the rest is wrong.
2. **A matra-reordering pass.** `Ê` (ि) sits *before* its consonant in visual
   order and must move after it. This is the same visual-vs-logical order
   problem `OVERVIEW.md` describes as the root cause, appearing now as the
   thing the converter has to undo.

---

## 2. Families: five tables cover 94%

Font names are useless for identifying a family — 546 distinct names across
5,438 convicted observations, and the commonest are `F1`–`F8` (subset IDs),
`Calibri`, `ArialMT`, `Mangal`. Same lesson as Phase 1: **classify by output.**

Clustering the 713 convicted legacy observations by character-frequency
signature (cosine ≥ 0.80 on excerpt text) gives 27 clusters, steeply
concentrated and — importantly — **label-pure**:

| cluster | obs | label | representative names |
|---|---|---|---|
| 1 | 506 | `LEGACY_8BIT` (100%) | DVTTDhruvNormal, F2 |
| 2 | 57 | `LEGACY_8BIT` (100%) | APS-C-DV-Priyanka |
| 3 | 43 | `LEGACY_8BIT` (100%) | DVBW-TTSurekh |
| 4 | 33 | `LEGACY_ASCII` (100%) | MG Shree |
| 5 | 31 | `LEGACY_8BIT` (100%) | TTE2A71928t00 |

Top 5 = 670/713 = **94%**. The tail is clusters of 8, 3, 2 — too little text to
derive a reliable table from, and each still costs a full table. **Scope:
top 5.** The remainder is reported as unconverted, not silently dropped.

Label purity matters: it is evidence the clusters are real encodings rather
than an artifact of the distance metric. A cluster mixing `LEGACY_8BIT` and
`LEGACY_ASCII` would mean the signature was picking up something else.

---

## 3. Deriving the tables

**Decided: learn by alignment from the Phase 3 OCR pairs.** That run left 352
paired pages for the dominant family alone — garbage text and OCR of the same
rendered page, which is a parallel corpus that did not exist before Phase 3.

### 3.1 The spike, and what it showed

Frequency-rank word matching surfaces correct anchors:

```
xÉÉÊ¶ÉEò     -> नाशिक      (874 / 1242)
EòÉä]äõ¶ÉxÉ  -> कोटेशन     (452 / 837)
Eò®úhÉä      -> करणे       (160 / 213)
```

But naive bag co-occurrence over those pairs **does not** recover the character
table. `Eò`→क and `ÉÉ`→ा come out right; `xÉ`→न does not appear in the top four
candidates at all. The cause is visible in the pairs: `¨ÉvÉÒ±É`→फॉर्म and
`¡òÉì¨ÉÇ`→मधील are swapped, and `EòÉ¨ÉÉSÉä`→ठिकाण is wrong. Enough mispairs to
poison the counts.

**This is recorded because it is the phase's main risk and it is already
known to fail in its naive form.** Two corrections, both standard:

- **Monotonic alignment, not bags.** These encodings preserve order except for
  the `ि` matra, so alignment inside a word pair is a DP sequence alignment
  that uses position. Bag co-occurrence throws position away, which is exactly
  the information that separates `xÉ`→न from `xÉ`→ा.
- **Iterate (EM).** Seed with noisy pairs, estimate character mappings, re-score
  the word pairs using those mappings, drop the bad ones, re-align, re-estimate.
  Once `¨É`→म is known, `¨ÉvÉÒ±É` prefers मधील and the swap self-corrects.

### 3.2 Stopping rule

Iteration stops when the table stops changing or a pass adds no mapping above
the attestation floor. A mapping enters the table only if attested in **≥ 5
distinct documents** — not merely 5 times, since one repetitive document could
otherwise author a rule by itself. Government documents repeat phrases heavily,
which is what makes the anchors findable and is also what makes a per-document
floor necessary rather than a per-occurrence one.

### 3.3 The circularity, named

Tables are learned from OCR. Therefore **OCR agreement cannot be the primary
measure of whether conversion worked** — it would be scoring the answer against
its own source. §4 handles this.

---

## 4. What counts as a successful conversion

Three measures, in the order their independence decreases.

**Primary — structural validity.** Converted text must be well-formed
Devanagari: `invalid_rate_per_1k` below the Phase 1 threshold of 2.0. This is
**independent of OCR** and therefore not circular. It is also the "constraint
validation" half of this phase's name. A table that produces plausible-looking
nonsense fails here, because legacy-order output is structurally impossible
Devanagari.

**Secondary — OCR character agreement on a held-out split.** Documents are
split before derivation; tables are learned on train, agreement measured on
test. Circularity is reduced, not eliminated — the same OCR engine produced
both — so this is reported as corroboration, never as accuracy.

**Negative control — the converter must not damage clean text.** Run it over
documents Phase 1 called `CLEAN` and that concordance agreed were clean. The
converter should leave them alone. An encoding-detector that fires on correct
Devanagari would corrupt working documents, which is worse than not converting
at all. This is the control that makes the phase safe to recommend.

Reported together, always. A high structural-validity score with a low
agreement score means the table produces valid Devanagari that is not the right
Devanagari — a failure mode worth being able to see.

---

## 5. Scope boundaries

- **Concordance is not folded into `decide_verdict`.** Decided 2026-08-19. It
  has been checked against Phase 1 verdicts but never against the 434 labels,
  and three earlier candidate detectors were measured and rejected. Phase 3's
  correction stays a reported finding.
- **`CMAP_INVALID` is out of scope.** Those documents emit real Devanagari
  codepoints in impossible order — a reordering problem, not an encoding one.
  A different fix, and Phase 3 §2.4 showed it is also where extractors diverge
  most.
- **The 6% tail is reported, not converted.**
- **No new collection.** This phase reads the existing corpus only.

---

## 6. A constraint that is not technical

`phase0-schema.md` §9.7 records that the excerpt text contains dates of birth
and caste categories for identifiable private individuals — 39 and 60 excerpts
respectively, across 11 documents. Three decisions about it remain unmade.

A working converter **increases** the exposure: it turns text that was
practically unreadable into clean, searchable, indexable Devanagari. That is the
entire point, and it is also why the §9.7 decisions should be made before any
converted output is published or shared, rather than after. Nothing in this
phase publishes anything, and no text leaves the machine.

---

## 7. Implementation

Additive only. No Phase 1, 2 or 3 table is written to.

| file | job |
|---|---|
| `phase4_schema.py` | `font_family`, `mapping_entry`, `conversion` tables |
| `legacy_families.py` | cluster observations into families; assign membership |
| `derive_mapping.py` | the EM aligner of §3.1; emits mapping tables |
| `convert.py` | apply table + matra reordering |
| `evaluate_conversion.py` | the three measures of §4 |

Build order matters: `legacy_families.py` and `convert.py` are certain,
`derive_mapping.py` is the risk. Convert is written against a **hand-authored
table for one family first**, so that the applier is proven correct before the
deriver is trusted to fill it. Otherwise a conversion failure is ambiguous
between a bad table and a bad applier.
