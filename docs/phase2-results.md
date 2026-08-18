# Phase 2 — Ground truth and detector evaluation

Run 2026-08-15. **Incomplete by its own pre-registered criteria, and shipped
anyway by decision.** Read section 1 before any number below it.

Phase 1 asked *is the problem real* and answered yes. Phase 2 asked *is the
instrument right* — a question needing labelled data at the grain the detector
works at, which Phase 1 never produced. That data now exists. The validation
that was supposed to make it trustworthy does not.

---

## 1. What these numbers are, and are not

The protocol (`phase0-schema.md` §5.2) called for two independent labelling
passes, gated on Cohen's κ ≥ 0.7 per class. That gate was **pre-registered in
§5.4 specifically so it could not be relaxed once data existed.** It has been
relaxed.

What exists is one model-assisted pass over all 434 sampled observations, plus
13 human labels. `adjudication` holds **421 `single`, 12 `unanimous`, 1
`adjudicated`**.

Every figure here therefore describes **the detector's agreement with a single
labelling pass over a stratified sample.** Not ground truth. Not validated
precision and recall. The interval on the corpus rate covers sampling variance
only and says nothing about label error.

Three specific costs, recorded in full at `phase0-schema.md` §5.5:

1. **No inter-annotator reliability figure exists.** κ = 0.882 on n=13 measures
   two readers, but 13 items estimate nothing. A separate κ = 1.000 on n=65
   exists from re-labelling by the same model in the same session — that one
   measures memory rather than reliability, and §11.7 says so.
2. **An error shared by detector and labeller is invisible.** They work by
   different mechanisms — byte heuristics versus reading the text — so the
   comparison is not circular. But nothing here separates "the detector misses
   `LEGACY_SYMBOL`" from "both parties scope that class the same wrong way".
3. **The pass was not blind to the stored signals.** Contrary to §4.5 the
   interface displayed `sampled_chars` and `dev_chars`, and the stratum name —
   which encodes the detector's verdict — on the first few items.

---

## 2. What was built and measured

**6,572 font observations across 1,181 documents**, one row per (document,
font), every signal stored whether or not it fired. Reconciled against the
Phase 1 audit: **0 mismatches**. 163 documents were re-extracted at
full-document cap after the 8-page cap was found to starve sparsely-used fonts
(§8.2).

**434 observations drawn**, seed 20260814, six strata crossing the detector's
verdict with whether the font *name* is informative. That second axis exists to
break a circularity: Phase 1 validated the per-font detector against fonts
identifiable **by name**, which cannot speak to the 32% of legacy documents
where no name matched.

All 434 were labelled from stored excerpts **with the external drive detached
and no PDF opened** — which validates the premise of §2.2 and was the whole
point of storing text.

---

## 3. Detector against the labels

| | |
|---|---|
| precision | 0.975 |
| recall | 0.826 |
| F1 | 0.894 |
| true positives / false positives | 195 / 5 |
| false negatives / true negatives | 41 / 193 |

**41 misses against 5 false positives.** This is the first measurement of an
asymmetry the project has asserted from the start: the instrument fails toward
silence, never toward alarm. Every defect found in the original audit tool
pushed the estimate *down*, and this is what that looks like quantified.

Per mechanism:

| truth | n | precision | recall |
|---|---|---|---|
| `LEGACY_8BIT` | 118 | 0.991 | 0.949 |
| `CMAP_INVALID` | 86 | 0.947 | 0.837 |
| `LEGACY_ASCII` | 23 | 1.000 | 0.348 |
| `LEGACY_SYMBOL` | 9 | 0.000 | 0.000 |

`CMAP_INVALID` at n=86 is notable on its own. Phase 1 had **no per-font
evidence for this class at all**, because `classify_font_output` returns `None`
as soon as real Devanagari appears. It is now the second-largest labelled class.

---

## 4. Threshold sweeps

The report the schema exists to make possible: every signal stored for every
font, so tuning is a query rather than a re-read of 1,602 PDFs.

| signal | target class | shipped | precision | recall |
|---|---|---|---|---|
| `mojibake_ratio` | `LEGACY_8BIT` | 0.15 | 0.974 | 0.949 |
| `invalid_rate_per_1k` | `CMAP_INVALID` | 2.0 | 0.943 | 0.953 |
| `ascii_k_ratio` | `LEGACY_ASCII` | 0.05 | 0.900 | 0.391 |
| `symbol_per_1k` | `LEGACY_SYMBOL` | 10.0 | 0.000 | 0.000 |

The first two are well placed. `invalid_rate_per_1k` is flat from 0.5 to 2.0
and decays slowly above it — a pre-registered threshold sitting inside a
plateau rather than on an edge, which is what one looks like when it turns out
to be right.

---

## 5. Four findings

**1. `LEGACY_ASCII` is a coverage gap, not a tuning problem.** Of 15 missed
observations, 12 carry `ascii_k_ratio` near 0.000 over 800–1,500 sampled
letters. They are not Kruti Dev — they are ISM, transliteration-style, and a
third family whose mappings send nothing in particular to `k`. The signal works
because Kruti Dev maps `k` onto the aa-matra, the commonest character in
written Hindi: a property of *one encoding*, not of ASCII remapping in general.
**No threshold catches the others** — the sweep is flat at roughly 0.39–0.48
recall all the way down. Fixing it means a new signal per family.

**2. `symbol_per_1k` does not work at any threshold.** Peak F1 0.125 near
2.5–4.9, zero at the shipped 10.0. Phase 1 validated this rule at precision
1.000 against fonts identified *by name*; against labels it does not survive.
Either the rule is mis-scoped or `LEGACY_SYMBOL` does not carve at a joint, and
9 observations cannot separate those.

**3. One detection lost to a sample-size gate by four letters.** obs 1974
carries `ascii_k` 0.184, far above threshold, with 196 ASCII letters against
the 200-letter floor. That floor exists to kill a Myriad Pro signature-block
false positive and still earns its place — but it has now cost a true
detection. Corpus-wide, 40 fonts carry `ascii_k` at or above 0.05 while failing
that gate.

**4. Three labels each cover several mechanisms.** `CMAP_INVALID` spans at
least four (visual-order i-matra, systematic consonant substitution, Latin or
IPA-extension stand-ins, Devanagari-Extended stand-ins); `LEGACY_8BIT` at least
five byte mappings; `LEGACY_ASCII` three. Evaluation averages over them, and
the write-up should not describe any of the three as a single phenomenon.

---

## 6. Corpus rate

**58.4% ± 2.7** of font observations corrupt, reweighted from the stratified
draw by inverse selection probability.

Three cautions. It is over **font observations, not documents**, so it is not
comparable to Phase 1's 36.5% macro / 48.4% pooled. The interval is sampling
variance only. And it rests on single-pass labels, per section 1.

---

## 7. What would change any of this

In increasing cost, from `phase0-schema.md` §5.5:

1. Run `llm_annotate.py --submit` for a genuinely blind second pass — it sees
   no strata and no detector context. Needs an API key.
2. Complete a human pass over the 434.
3. Draw a fresh smaller sample and label it twice, properly, a week apart.

Any of the three restores a real κ and puts §5.3's gate back into force. Until
then Phase 2's output is a set of well-evidenced leads about where the
instrument is weak — genuinely useful, and not the same thing as a validated
measurement.
