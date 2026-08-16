# DevAudit — working context

Read this first. It replaces re-reading the whole project history.

## What this is

Measuring how often Indian government PDFs carry a text layer that extracts
without error and is linguistically wrong. Phase 1 is complete and answered
GO. See `README.md` for the finding, `docs/phase1-results.md` for the full
result and caveats, `docs/LICENSING.md` for what may and may not be collected.

## How the author wants to work

- Boring, readable code over clever code. One line of *why* on each design
  decision.
- Say so when something won't work rather than building it anyway.
- Small commits, clear messages. No Claude co-author trailer.
- Work phase by phase. Explain reasoning — the point is for the author to
  understand everything we build, not just to have it built.
- **Ask before downloading anything.** Bulk storage is an external drive that
  is not always attached.

## Hard-won facts that are expensive to rediscover

**Storage.** Bulk PDFs live on `D:\DevAudit-data\raw` (external, frequently
detached). `manifest.sqlite` lives on the SSD inside the repo, deliberately —
the drive has dropped mid-run twice and the manifest survived both times. If
every document errors with "file missing", the drive is unplugged.

**The instrument fails toward silence, never toward alarm.** Every defect found
in the original audit tool pushed the corruption estimate *down*. A detector
that over-fires inflates the headline in the flattering direction, which is the
one to distrust. Measure a candidate detector against labelled ground truth
before shipping it; three were measured and rejected.

**Classify by output, not by font name.** 32% of legacy documents are caught
with no font name matching at all — some "font names" are auto-generated subset
IDs (`TT313t00`) or leaked Windows temp filenames (`Z@RAF1C.tmp`).

**Per font, not per document.** A document mixes fonts, so a legacy face beside
English headers produces a blended signal that trips no threshold. The same
measurement separates 15x once taken per font. This was the single most
productive change to the instrument.

**Sampling has bitten three times.** PMC reported 0% legacy from a pool that was
the oldest 5% of its documents; the regional comparison reversed when one body
was under-sampled; per-source caps limit each *run*, not the total, so bodies
drift out of balance. Report the macro average (each body weighted equally)
alongside the pooled figure and distrust the pooled one when they disagree.

**`pip` and `python` are different interpreters on this machine.** `pip`
resolves to the Windows Store Python; `python` resolves to
`AppData\Local\Programs\Python\Python312`, which is what runs the scripts. A
plain `pip install X` reports success and installs nothing the scripts can see.
Always use `python -m pip install X`.

**robots.txt is a hard limit.** `cdn.s3waas.gov.in` serves
`User-agent: * / Disallow: /`, and every `.nic.in` district site publishes
through it — hundreds of bodies, uncollectable. This bounds what any corpus of
this kind can contain and belongs in the write-up's limitations.

## Running it

```bash
python -m pip install -r requirements.txt   # first time; see the pip note above
python collect.py --dry-run           # discover, download nothing
python collect.py --per-source 60     # random draw, capped per body
python audit_corpus.py                # audit into manifest, print report
python -m pytest tests/               # 52 tests (17 Phase 1 calibration)
```

Phase 2, in order. Only `extract_observations.py` needs the external drive:

```bash
python phase0_schema.py               # create the tables (idempotent)
python extract_observations.py        # the one drive-attached pass
python extract_observations.py --deep # re-read documents the page cap starves
python draw_annotation_sample.py --sample-id gt-v1 --seed 20260814
python annotate.py --next             # label; --label OBS_ID LABEL to record
python llm_annotate.py --submit       # second-opinion pass (needs an API key)
python adjudicate.py --auto           # agreements settle in bulk
python adjudicate.py --next           # then one disagreement at a time
python evaluate.py --agreement        # gates everything below it
python evaluate.py --detector         # precision and recall against truth
python evaluate.py --sweep mojibake_ratio
python evaluate.py --estimate         # corpus rate, reweighted, with an interval
```

Pipe verbose runs to a log and read the tail — full audit output is hundreds of
lines and every one of them stays in the session context afterwards:

```bash
python audit_corpus.py > data/audit.log 2>&1 && tail -20 data/audit.log
```

## Where Phase 1 left off

- Corpus: 1,602 documents, 8 issuing bodies, 3 states.
- Result: 36.5% macro / 48.4% pooled `LEGACY + SUSPECT`. GO.
- Open: `check_detector_overlap.py` has never run (needs the drive attached);
  it answers whether the two document-level detectors still earn their place
  now that the per-font detector runs the same measurements on better input.
- Open: audit re-runs re-read every PDF to recompute signals that mostly have
  not changed. Caching the raw signals would make threshold changes a SQL
  query instead of a thousand-file re-read.

## Where Phase 0 left off

Design is done and written up in `docs/phase0-schema.md`. Read that, not this
summary, before touching Phase 2.

**The one-line diagnosis.** The instrument measures per font; the database
stores per document. Everything learned per font is flattened into delimited
text columns, so only convictions survive — a font that was measured and
cleared leaves no record. That is why `--rebucket` can re-apply font *names*
but not per-font *thresholds*, and why the SUSPECT class has no per-font
evidence at all.

Shipped and tested (**41 tests, was 17 — the original 17 unchanged**):

- `font_audit.measure_font_text()` — measuring split from deciding. Returns
  every signal unconditionally; `classify_font_output()` sits on top with
  identical thresholds and byte-identical output. `collect_font_spans()` is now
  the primitive and records page + offset per span; `collect_font_text()` is a
  wrapper, so `audit()` is untouched. No Phase 1 number moved.
- `phase0_schema.py` — `font_observation` (one row per document+font, every
  signal stored), `excerpt`, `annotation_sample`, `annotation` (append-only),
  `adjudication`, `doc_annotation`, `ground_truth` view. **Run against the live
  manifest**; backup at `data/manifest.pre-phase0.sqlite` (gitignored).
- `extract_observations.py` — the extraction pass. **Not yet run over the
  corpus; needs the drive.** `--verify` reconciles against `audit` without
  reading PDFs.
- `draw_annotation_sample.py` — stratified draw, records selection probability
  so the labelled proportion can be reweighted to a corpus proportion.

**Corpus state (extraction has been run):** 6,572 font observations across
1,181 documents; 163 of those documents re-extracted at full-document cap
(`signals_version` ends `+deep`, so the two populations stay separable).
Reconciliation against `audit.n_legacy_by_output`: **0 mismatches**. Phase 1
verdicts and the 36.5%/48.4% figure are untouched — nothing here feeds
`decide_verdict`.

**A fifth undocumented legacy family: `Akruti`** (§8.3). Not in
`LEGACY_PATTERNS`. Sits below every threshold — mojibake 0.08 vs 0.15, `k`
0.03 vs 0.05 — because it falls between the Marathi 8-bit and Kruti Dev
styles. Confirmed from its stored excerpt (`cne[e De@ke�� 1976` = म्हाडा
अधिनियम 1976) without opening a PDF. 11 observations in 1 document, already
`LEGACY` on another font, so the headline does not move. Adding it to
`LEGACY_PATTERNS` is a `--rebucket`, not a re-audit.

**Cap size cuts both ways (§8.3).** Reading every page fixed one false
positive (`TimesNewRoman,Bold`, k 0.058→0.019) and created one false negative
by dilution. A bigger window is not uniformly better; treat `PERFONT_MAX_PAGES`
as a swept parameter in Phase 2, not a knob to turn now.

**Two findings from testing on the SSD fixtures, both in §8.2:**

1. The 8-page per-font cap starves sparsely-used fonts. `Shree-Dev-0708` is
   declared on 24 of 43 pages and contributes 24 characters to the sample, so
   the detector abstains for sampling reasons, not font reasons. Mitigated by
   storing `n_pages_declared` beside `sampled_chars` so it is queryable.
2. On that same document the LEGACY verdict rests on a name match against a
   font that renders almost nothing — the actual corruption is in
   `ArialUnicodeMS`, a Unicode face with a broken CMap. Right answer, wrong
   reason. Decide whether to raise `PERFONT_MAX_PAGES` *before* annotating.

Open decisions that must be settled *before* annotating: whether a spurious
space is `CORRECT` with a note or its own label (§4.4, §9.1), and the
re-annotation fraction for rare classes (§9.4).

## Where Phase 2 stands

Extraction and every tool are done and tested; **no labels exist yet**. The
whole remaining phase is annotation and the four reports that read it.

- 6,572 font observations, 1,181 documents, 0 reconciliation mismatches.
- Sample `gt-v1` drawn: 434 observations, seed 20260814, 6 strata.
- `annotate.py` (human), `llm_annotate.py` (model, Batches API),
  `adjudicate.py` (two passes into one final label), `evaluate.py` (agreement,
  detector, sweep, estimate). Every report degrades cleanly on empty state, so
  they can be run at any point.
- The chain is `annotation` → `adjudicate.py` → `adjudication` →
  `ground_truth` → `evaluate.py`. Skipping adjudication leaves every report
  empty however much labelling gets done, because the view joins that table.

**Blocked on the author, not on code:**

1. Settle §9.1 — is a spurious space `CORRECT` + `#spurious-space`, or its own
   label? Every row carries `guideline_version`, so changing this later means
   re-labelling the affected rows, not starting over.
2. Label. `python annotate.py --next`.
3. `pip install --upgrade anthropic` before `llm_annotate.py --submit` — the
   installed 0.52.0 predates structured outputs, and the script refuses rather
   than paying for a batch of unconstrained labels.

**Phase 2 ends when** all four hold: 434 observations labelled by both passes;
disagreements adjudicated into `adjudication`; per-class kappa ≥ 0.7 (the floor
was fixed in advance — a class below it is blocked from evaluation until the
guidelines are revised, not waived); and `--detector`, `--sweep`, `--estimate`
produce the numbers for the write-up. The deliverable is a corpus rate with an
interval, which is the first version of the headline figure that has error bars
rather than a caveat paragraph.

Then Phase 3: benchmark extractors against that ground truth.

Still open from Phase 1: `check_detector_overlap.py` has never run. It needs
the drive and is a separate question — do not fold it into a Phase 2 pass.
