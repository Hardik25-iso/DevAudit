# Phase 4 — results

Run 2026-08-19/20. Design in `phase4-design.md`. This reports what the
converter does, and it is a **partial result**: one family works usefully,
one fails in an instructive way, and three are too thin to judge.

---

## 1. The headline: better than nothing, well short of correct

Phase 3 established that no extractor recovers legacy-encoded text. This phase
built the reverse mapping and measured it. On the dominant family, held out
from derivation (93 test pages, table learned from 207 training documents):

```
'नाशिक महानगरपालिका नाशिक ई - कोटेशन नोटीस ्र. दि 05/08/2024 निमनलिखिात'
```

The document header is recovered. `निमनलिखिात` should be `निम्नलिखित`, and
about a third of characters remain wrong. **Median OCR agreement is 0.366.**

This is a working demonstration that the reverse mapping can be learned rather
than hand-authored. It is not a usable converter.

## 2. All five families

Held-out medians. `cover` is the share of characters the table matched;
`inval/1k` is structural violations after conversion; `ocr_sim` is character
agreement with OCR of the same page.

| family | rules | cover | inval/1k | ocr_sim | test pages |
|---|---|---|---|---|---|
| `fam-01-dvttdhruvnor` | 183 | 0.73 | 19.3 | 0.366 | 93 |
| `fam-02-apscdvpriyan` | 154 | 0.69 | **8.2** | **0.059** | 7 |
| `fam-03-dvbwttsurekh` | 174 | 0.75 | 12.6 | 0.366 | 4 |
| `fam-04-f1` | 144 | 0.60 | 52.1 | 0.267 | 3 |
| `fam-05-tte2a71928t0` | 69 | 0.18 | 372.5 | 0.022 | 9 |

**Test sets of 3–9 pages carry no weight.** They are here because the
alternative is not reporting those families at all, not because the medians
mean anything. Only `fam-01` has an evaluation worth the name.

### 2.1 `fam-02` is the failure the metric design was built to catch

It has the **best structural validity of any family** (8.2/1k, better than the
working one) and the **worst OCR agreement** (0.059). Coverage is a healthy
0.69, so it is not abstaining — it is confidently producing well-formed
Devanagari that is not the right Devanagari.

Design §4 predicted exactly this: *"A high structural-validity score with a low
agreement score means the table produces valid Devanagari that is not the right
Devanagari — a failure mode worth being able to see."* Structural validity
alone, the phase's designated primary measure, would have ranked `fam-02` the
best converter built here.

That is the argument for reporting all measures together, made concrete.

### 2.2 `fam-05` barely converts

Coverage 0.18 against 0.60–0.75 elsewhere. It has 75 paired pages, but a
5,308 garbage-word to 36,104 Devanagari-word imbalance: the pages are mostly
Devanagari already, so there is very little legacy text to pair against and the
aligner has almost nothing to learn from. Reported as unconverted.

## 3. What moved the numbers, and what did not

**Training volume mattered, a lot.** The deriver first trained on
`extraction.text_sample` — 600 characters per page, page 1 only, a field that
exists so a human can eyeball a page with the drive detached. Re-extracting
whole pages over pages 1–5 gave 2,849,365 characters against 599,444, a 4.75×
increase:

| held-out, n=93 | 600 chars | full pages |
|---|---|---|
| invalid_rate median | 111.1/1k | **19.3/1k** |
| ocr_similarity median | 0.161 | **0.366** |
| pages under 25/1k | 0.000 | **0.753** |

A 5.8× reduction in structural violations. `काेण्याान नाटेीस` became
`कोटेशन नोटीस`.

**Volume is unlikely to be the next lever.** 4.75× bought 5.8×, and the
residual errors look structural rather than statistical: characters like `Ç`
are never mapped at all, and the confusions are matra-level.

**Parameter tuning did not help.** Segment sizes and length penalties were
swept; anchor accuracy sat at 2/6 across every setting while coverage went to
1.00. The bottleneck was never the segmentation.

## 4. Three defects found by measurement, not by reasoning

**The applier was wrong for a derived table.** Greedy longest-first works for a
hand table but not a learned one, because the deriver learns rules in context —
`ÉEò`→शक is only valid after `Ê¶`, and greedy fired it anywhere. Decoding the
whole segmentation by DP fixed it, and it is the criterion the deriver already
used, so the two stopped disagreeing.

**The converter corrupted clean documents.** Applied to Devanagari pages Phase 1
called `CLEAN`, the derived table changed **11 of 11** and made 5 structurally
worse: single-character rules like `x`→क and `V`→व fire on the ordinary Latin
text inside Marathi documents. `convert()` now refuses text already above 5%
Devanagari — that near-total absence of Devanagari *is* the defining property of
the failure being repaired, so refusing is correct rather than a limitation.

All five families now pass the control 11/11 with 0 made worse.

**The primary metric was nearly blind to progress.** Phase 1's 2.0/1k threshold
was calibrated to *detect* corruption in native Devanagari, not to grade a
converter, and one bad sequence fails a whole page. Between the two runs it
moved 0.000 → 0.011 while the underlying rate improved 5.8×. The report now
prints the distribution beside the gate, the same reasoning as printing macro
beside pooled.

## 5. Limitations

- **The tables are semi-supervised, not purely learned.** `fam-01` is seeded
  with 13 hand-authored rules. The unseeded run ranks 12 of those 13 first in
  its own counts, so the seed settles one genuine ambiguity (`Ê` scores क 77 to
  ि 37) rather than supplying the answer — but the phase was scoped as "learn by
  alignment", and this is a deviation. The other four families have **no seed**,
  which is one plausible reason `fam-02` and `fam-05` fail.
- **Circularity is reduced, not removed.** Tables are learned from OCR and
  `ocr_similarity` is measured against OCR. The held-out split separates the
  documents; it does not separate the engine. Structural validity is the only
  fully independent measure, and §2.1 shows what it misses on its own.
- **Six anchor words were a bad early proxy.** They reached 5/6 while held-out
  agreement was 0.17. They are the highest-frequency words in the corpus and
  therefore the best-attested rules — the easiest possible cases.
- **`CMAP_INVALID` is out of scope** by design: real Devanagari in impossible
  order is a reordering problem, not an encoding one.
- **The 6% tail** of 22 small clusters is unconverted and unmeasured.

## 6. The constraint that is not technical

`phase0-schema.md` §9.7 records dates of birth and caste categories for
identifiable private individuals in the excerpt text, and three unmade decisions
about it. A working converter makes that **worse**: it turns text that was
practically unreadable into clean, searchable, indexable Devanagari.

Nothing in this phase publishes anything and no text left the machine. But the
better this converter gets, the more those decisions need making — and they
should be made before any converted output is shared, not after.

## 7. What a next phase would need

- **A second family with a hand seed**, to test whether seeding explains the
  `fam-02` failure or whether that encoding is genuinely harder.
- **More paired pages for the thin families** — three of five have single-digit
  test sets, which no amount of careful reporting fixes.
- **An independent reference for a sample.** Every measure here is either
  reference-free or OCR-derived. A few hundred hand-transcribed lines would
  break the circularity that §5 can only reduce.
