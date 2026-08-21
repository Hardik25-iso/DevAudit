# DevAudit — working context

Read this first. It replaces re-reading the whole project history.

## What this is

Measuring how often Indian government PDFs carry a text layer that extracts
without error and is linguistically wrong. **Phases 0-5 are done and pushed.**

`docs/REPORT.md` is the consolidated write-up and the front door — send a
reader there. `docs/phaseN-results.md` are the detailed record behind it.
`docs/LICENSING.md` is what may and may not be collected.

**The current headline is 45.4% macro / 56.7% pooled.** Phase 1's 36.5% /
48.4% is the floor it turned out to be, kept intact in `phase1-results.md`.

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
the drive has dropped mid-run **three times** and the manifest survived every
one. If every document errors with "file missing", the drive is unplugged.

**An interrupted census is a sample.** Both Phase 3 drive drops left partial
runs that were body-skewed rather than random, because the document list was
not shuffled. Every selection path now shuffles; keep it that way.

**The instrument fails toward silence, never toward alarm.** Every defect found
in the original audit tool pushed the corruption estimate *down*. A detector
that over-fires inflates the headline in the flattering direction, which is the
one to distrust. Measure a candidate detector against labelled ground truth
before shipping it; **four have now been measured and rejected**, the latest
being Phase 4's seed-conflict filter, which fixed the defect being looked at
and made everything else worse.

It recurred in three new places after Phase 2. `pdftotext` scores 0.001
mojibake while emitting 43.7% U+FFFD, so it would have ranked *best* on the
signal battery. Phase 4's `fam-02` scores the best structural validity of any
family while producing Devanagari that is not the right Devanagari. And Phase
1's 2.0/1k gate moved 0.000 -> 0.011 while the rate under it improved 5.8x.

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
python -m pytest tests/               # 124 tests (17 Phase 1 calibration)
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

Phase 3. Only `benchmark_extract.py` needs the external drive; every report
reads the manifest:

```bash
python phase3_schema.py                   # create the tables (idempotent)
python benchmark_extract.py --tier labelled --arms text --max-pages 5
python benchmark_extract.py --tier all --arms all --max-pages 1   # + OCR
python evaluate_extractors.py --run labelled-20260819 --all
python evaluate_extractors.py --run all-20260819 --concordance
python evaluate_extractors.py --run all-20260819 --sweep replacement
```

OCR needs Tesseract at `config.TESSERACT_EXE` and language data in
`data/tessdata/` (gitignored, ~7MB, `tessdata_fast` `hin`+`mar`). Without it
the concordance report degrades cleanly and says so; every other report works.

Phase 4. Only `extract_training.py` needs the external drive:

```bash
python phase4_schema.py                   # create the tables (idempotent)
python legacy_families.py --build         # cluster by output signature
python extract_training.py --max-pages 5  # the one drive-attached pass
python derive_mapping.py --family fam-01-dvttdhruvnor
python convert.py --family fam-01-dvttdhruvnor --demo
python evaluate_conversion.py --family fam-01-dvttdhruvnor --run eval-N
python evaluate_conversion.py --negative-control   # run it after EVERY table
```

Phase 5. Neither needs the drive:

```bash
python export_manifest.py                 # refuses if the guard fires
python -m pytest tests/                   # 124 tests
```

Pipe verbose runs to a log and read the tail — full audit output is hundreds of
lines and every one of them stays in the session context afterwards:

```bash
python audit_corpus.py > data/audit.log 2>&1 && tail -20 data/audit.log
```

## Where Phase 1 left off

- Corpus: 1,602 documents, 8 issuing bodies, 3 states.
- Result: 36.5% macro / 48.4% pooled `LEGACY + SUSPECT`. GO.
- **Both former open items are closed. Do not reopen them** — earlier handoffs
  carried them forward after they had already been done, which cost one session
  a near-miss corpus re-read:
  - `check_detector_overlap.py` **has** run, over all 1,602 documents;
    `data/overlap.log` holds the output. The two document-level detectors fired
    203 and 20 times and were the sole evidence **zero** times, so both were
    deleted from `decide_verdict`. `mojibake_ratio` and `ascii_k_ratio` survive
    in `audit()` as reported measures only. Re-running it answers a question
    already answered.
  - Signal caching shipped as `audit_corpus.py --rebucket` (commit `ca9baa5`),
    and Phase 0's `font_observation` went further by storing every per-font
    signal. A threshold or font-list change is a query now, not a re-read.

## Where Phase 0 left off

Design is done and written up in `docs/phase0-schema.md`. Read that, not this
summary, before touching Phase 2.

**The one-line diagnosis.** The instrument measures per font; the database
stores per document. Everything learned per font is flattened into delimited
text columns, so only convictions survive — a font that was measured and
cleared leaves no record. That is why `--rebucket` can re-apply font *names*
but not per-font *thresholds*, and why the SUSPECT class has no per-font
evidence at all.

Shipped and tested (the original 17 Phase 1 calibration tests unchanged
throughout — that is the check that matters):

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
`LEGACY` on another font, so the headline does not move. **Added to `LEGACY_PATTERNS` on 2026-08-15** — `--rebucket` reported 0 verdict
changes, so the headline is unmoved (§11.8). Backup at
`data/manifest.pre-akruti.sqlite`.

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

**Guidelines are frozen at v0.1** (2026-08-15). §9.1 settled: a spurious space
is `CORRECT` with a mandatory `#spurious-space` note — the glyph mapping is
right, so the defect is the extractor's, not the font's, and the font is the
unit being labelled.

**Label state, and the decision that governs it (2026-08-15).** All 434
observations carry a label from `llm:claude-opus-5-interactive`; `hardik` has
13, of which 12 agreed. `adjudication` holds 421 `single`, 12 `unanimous`, 1
`adjudicated`.

**The two-pass protocol was abandoned by decision, not by oversight.** The
author chose to accept the model pass as the working ground truth and ship
Phase 2 without a human-validated κ. `docs/phase0-schema.md` §5.5 is the
record; read it before quoting any number, because it fixes what the numbers
may be called. Three consequences carry forward:

- There is no inter-annotator reliability figure. κ over 13 items is a
  diagnostic, not an estimate.
- The labels are one automated reading. Detector and labeller use different
  mechanisms so the comparison is not circular, but an error they share is
  invisible — `LEGACY_SYMBOL` at 0.000 recall is the live case.
- The pass was **not** blind to stored signals: the interface showed
  `sampled_chars` and `dev_chars`, and the stratum name on the first few items.
  `saw_detector_output=0` on those rows is to that extent wrong.

The figures may be described as *the detector's agreement with a single
model-assisted labelling pass over a stratified sample* — not ground truth, not
validated precision and recall.

| report | value | status |
|---|---|---|
| binary precision / recall | 0.975 / 0.826 | against single-pass labels |
| corpus rate | 58.1% ± 2.6 | interval is sampling variance only, no label error |
| kappa vs `hardik` | 0.810 on n=8 at last check | n too small to mean anything |

**What the run pointed at, still worth checking:**

- `LEGACY_ASCII` recall **0.348** is a **coverage** gap, not a threshold one
  (§11.6). 12 of the 15 misses have `ascii_k_ratio` ≈ 0.000 over 800–1,500
  sampled letters: they are ISM, transliteration-style and a third family, none
  of which map anything onto `k`. That signal works because Kruti Dev sends `k`
  to ा — a property of one encoding. No threshold catches the rest; it needs a
  new signal per family. One further miss, obs 1974, had `ascii_k` 0.184 and
  196 ASCII letters against the 200-letter floor — missed by four letters.
- `symbol_per_1k` **does not work at any threshold** against `LEGACY_SYMBOL`:
  peak F1 0.125 near 2.5–4.9, 0.000 at the shipped 10.0. Phase 1 validated it
  at precision 1.000 against fonts identified *by name*; against labels it does
  not survive. Either the rule is mis-scoped or the class does not carve at a
  joint, and 9 observations cannot tell you which.
- `mojibake_ratio` (0.974/0.949) and `invalid_rate_per_1k` (0.943/0.953) are
  both well placed, the latter sitting inside a plateau rather than on an edge.
- 41 misses against 5 false positives: the fail-toward-silence asymmetry,
  measured for the first time.

**To lift the limitation later** (§5.5, increasing cost): run
`llm_annotate.py --submit` for a blind second pass; complete a human pass; or
draw a fresh smaller sample and label it twice properly.

**Blocked on the author, not on code:**

1. Label independently. `python annotate.py --next` — the model pass must not
   be treated as the human pass, and until `hardik` has real coverage there is
   no agreement figure worth reporting.
2. Set `ANTHROPIC_API_KEY` and run `llm_annotate.py --submit` for the *blind*
   batch pass. The interactive pass saw strata and detector context; the batch
   pass does not, and only that one belongs in an agreement calculation.

**Phase 2 is closed** (2026-08-15), by decision rather than by meeting its
criteria. `docs/phase2-results.md` is the deliverable and states the limitation
in its first section. Do not reopen it to chase the kappa gate unless the
author asks; the labelling was optional upside and its absence is documented,
not hidden.

## Where Phase 3 stands

**Design settled 2026-08-19 and written up in `docs/phase3-design.md`. Read
that, not this summary.** Both opening questions are answered: five arms
(`pdftotext`, PyMuPDF, `pdfplumber`, `pypdf`, Tesseract OCR), and "recovered
correctly" is four reference-free measurements rather than one, with **loss
gating the other three**.

Shipped and tested (90 tests, the 17 Phase 1 calibration tests unchanged):
`phase3_schema.py`, `extractors.py`, `benchmark_extract.py`,
`evaluate_extractors.py`.

**Three things the pilot established that are expensive to rediscover:**

**Extractors do not fail identically on legacy fonts.** The expected result was
that they would — same bytes, same missing `ToUnicode` — which would have made
the phase pointless. False. Each descends its own fallback ladder and produces
*different* garbage. This is also why per-font grain cannot be carried forward:
the divergence destroys any alignment key between arms.

**The Phase 1/2 signal battery would rank the worst extractor best.** On corrupt
documents `pdftotext` scores `mojibake_ratio` 0.001 — cleaner than the clean
control — while emitting **43.7% U+FFFD**. `MOJIBAKE_RANGE` does not cover
U+FFFD, so every corruption signal this project owns is blind to it. Silence
scoring as success, one more time. That is why loss is measured first and gates
everything downstream.

**The grain drops from font to page, and there is no way around it.** Three
mitigations were measured and all three failed: document-level font dominance
(70 of 434 observations qualify), page-level dominance (~30%), and span probing
(defeated by the divergence above). Dilution hits every arm equally, so
rankings survive; absolute rates do not, and must be quoted with that attached.

**The finding that outgrew the phase.** Script concordance — comparing OCR of
the rendered page against the text layer — turned out to be a *detector*, and
it fires on documents Phase 1 called `CLEAN`: 15 of 32 scorable pages in a
45-document draw. They are legacy remaps embedded under `Helvetica` and
`Times-Roman`, plain ASCII, no Latin-1 supplement, no Kruti Dev `k` — invisible
to every shipped signal, and exactly the coverage gap §11.6 said would need "a
new signal per family". Concordance is family-agnostic, so it is that signal.

Pune Metro scores 0/10, which is the negative control working. **This is the
first defect in the project's history that pushes the estimate UP**, which
`CLAUDE.md`'s own rule says is the direction to distrust — hence the corpus-wide
run rather than a claim from n=32.

Decided with the author 2026-08-19: `docs/phase1-results.md` is **not** revised.
It stays traceable to the rows that produced it, and Phase 3 reports the
correction as its own finding, additively, the way Phase 0 did.

## Phase 3 results — both runs complete, 2026-08-19

`docs/phase3-results.md` is the deliverable. Read it, not this summary.

**Runs, all resumable and all in the manifest:** `labelled-20260819` (317 docs,
5 pages, 4 text arms, 6,340 rows), `all-20260819` (1,097 non-SCAN docs, page 1,
5 arms including OCR, 5,515 rows), `fonts-20260819` (2,024 font observations,
`compare_fonts.py`).

**The corpus estimate moves up by ~9 points and Phase 1 becomes a floor.**
Concordance fires on 42.8% pooled / 36.6% macro of scorable `CLEAN` pages, so
36.5% / 48.4% becomes **45.4% / 56.7%**. The pilot said 47% at n=32; the corpus
said 42.8% at n=276. Nagpur moves 6.4% → 37.7%, Pune MC 14.5% → 38.2%.

**Do not re-litigate whether to believe it.** Three controls were measured and
all three hold: Pune Metro 0 of 47 pages, `SUSPECT` 1.7% (it is `CMAP_INVALID`,
not a script failure, so concordance should ignore it and does), `LEGACY` 68.1%.
All three Phase 1 classes behave as the mechanism predicts.

**`pdftotext` loses 20.6 points more pages than the other three arms**, entirely
to U+FFFD, and would have ranked *best* on the Phase 1/2 battery alone. All four
arms agree on only 4.2% of pages, so at least one is wrong on ≥95.8%.

**No ranking between PyMuPDF, pdfplumber and pypdf is claimed.** Structural
validity reverses between macro (pdfplumber better) and pooled (pdfplumber
worse); Nashik supplies 152 of 317 documents. Both figures are printed.

**The per-font run localised the §2.2 divergence:** it is concentrated in
`CMAP_INVALID` (9.1% identical vs 67–72% elsewhere) — where `ToUnicode` is
present but broken, each arm decides separately whether to trust it. And
reordering is 0.000 at font grain, so pdfplumber's page-level reordering is
page assembly, not text-run scrambling.

**Two bugs found the hard way, both fixed.** A column added to `SCHEMA` without
a `MIGRATIONS` entry does nothing to an existing table. And `select_documents`
returned before the shuffle on the `all` path, so *both* drive drops left
body-skewed partial runs rather than random subsets — the completed runs are
unaffected, but the lesson is that "an interrupted census is a sample".

## Where Phase 4 stands

Design in `docs/phase4-design.md`, results in `docs/phase4-results.md`. Read
those, not this summary. **Partial result: one family works usefully, one fails
instructively, three are too thin to judge.**

Shipped and tested (116 tests): `phase4_schema.py`, `legacy_families.py`,
`convert.py`, `derive_mapping.py`, `evaluate_conversion.py`,
`extract_training.py`.

**Five families cover 94% of convicted legacy observations**, clustered by
output signature — font names are useless (546 names, commonest are F1-F8 and
Calibri). Every cluster is label-pure, which is the evidence they are real
encodings.

**Held-out medians, and only fam-01 has a test set worth the name:**

| family | rules | cover | inval/1k | ocr_sim | test |
|---|---|---|---|---|---|
| fam-01-dvttdhruvnor | 183 | 0.73 | 19.3 | 0.366 | 93 |
| fam-02-apscdvpriyan | 154 | 0.69 | **8.2** | **0.059** | 7 |
| fam-03-dvbwttsurekh | 174 | 0.75 | 12.6 | 0.366 | 4 |
| fam-04-f1 | 144 | 0.60 | 52.1 | 0.267 | 3 |
| fam-05-tte2a71928t0 | 69 | 0.18 | 372.5 | 0.022 | 9 |

**`fam-02` is the failure the metric design was built to catch:** best
structural validity of any family, worst OCR agreement. It produces confident,
well-formed Devanagari that is not the right Devanagari. Structural validity
alone — the designated primary measure — would have ranked it best.

**Three defects found by measurement, all fixed and all worth not
rediscovering:**

- Greedy longest-first is wrong for a *derived* table (rules are learned in
  context). DP over the whole segmentation fixed it.
- The converter corrupted clean documents — 11 of 11 changed, 5 made worse,
  because rules like `x`->क fire on Latin text inside Marathi pages.
  `convert()` now refuses text already above 5% Devanagari.
- Phase 1's 2.0/1k threshold is nearly blind as a converter grade: it moved
  0.000 -> 0.011 while the underlying rate improved 5.8x.

**Training volume was the big lever and is probably spent.** Re-extracting
whole pages over pages 1-5 (`page_text`, 2,849,365 chars vs 599,444) cut
violations 5.8x and doubled OCR agreement. A further 5x is unlikely to close a
gap this size; the residual errors are structural (`Ç` never maps at all).

**Do not re-run the parameter sweep.** Segment sizes and length penalties were
swept; anchor accuracy sat at 2/6 across every setting. The bottleneck was
never segmentation.

**fam-01 is seeded with 13 hand rules** — a deviation from "learn by
alignment", documented in results §5. The unseeded run ranks 12 of the 13
first in its own counts, so the seed settles one ambiguity rather than
supplying answers. The other four have no seed.

## Where Phase 5 stands

Design in `docs/phase5-design.md`, deliverable in `docs/REPORT.md`.
**Shipped and pushed 2026-08-21** (30 commits, `dbeeb49..1bdb59a`).

`docs/REPORT.md` is the front door now — the consolidated write-up. The five
phase documents are the detailed record behind it. Send a reader there, not to
README.

**The headline moved, deliberately.** README and OVERVIEW now lead with
**45.4% macro / 56.7% pooled** and present Phase 1's 36.5% / 48.4% as the floor
Phase 3 showed it to be. `docs/phase1-results.md` is **not** revised and must
not be — it stays traceable to the rows that produced it, and the correction is
reported additively. The correction ships with its three controls attached
(Pune Metro 0 of 47, `SUSPECT` 1.7%, `LEGACY` 68.1%) and both limitations named.

**§9.7 is settled on the technical side and enforced, not remembered.**
`export_manifest.py` refuses to write any column that quotes document text —
Devanagari, a DOB pattern, or a caste category — outside the identifier columns
the rebuild needs. The principle: **an exported column may identify a document
or measure it, never quote it.**

The guard fired on its first run, on `filename` and `source_url`: 65 filenames
are Marathi document *titles* (`नामनिर्देशन_अर्ज_व_शासन_पत्र`), not names of
people, and the rebuild cannot fetch without them. Inspected, then exempted as
identifiers, with the inspection recorded in the code. **Do not widen the
exemption set without re-running that inspection.**

**Re-measure §9.7 whenever a phase adds a text table.** Phase 0 recorded 39
excerpts across 11 documents; Phases 2, 3 and 4 each added one and none
re-measured, so by Phase 5 it was ~11.1M characters with 3,416 DOB matches
across `excerpt`, `extraction`, `page_text` and `conversion`. A working
converter makes this worse by design — `conversion.text_after` holds 396 of
those in readable form.

**What is released:** `manifest.csv` (1,602 rows), `mapping_tables.csv` (688
rules, 5 families), `rebuild_corpus.py`, `summary.json`, `LICENSING.md`,
`MAPPING_TABLES.md`. Never the documents, never `manifest.sqlite`.

## Open, and genuinely the author's

**The DPDP 2023 question.** The technical position is settled and enforced;
whether a qualified person should read the Act against this corpus before the
release is promoted has not been decided. Nothing in this repo is legal advice
and "already public" does not obviously settle it.

## Next phase

**Phase 4b**, if anything. Results §7 lists what it would need: a second
hand-seeded family, more paired pages for the thin families, and a few hundred
hand-transcribed lines to break the OCR circularity that a held-out split can
only reduce.

**Do not spend more effort on rule-level tuning.** Three attempts — the
parameter sweep, repha for fam-01, and the seed-conflict filter — all landed
neutral-to-negative on held-out numbers. The two changes that moved anything
were training volume (5.8x) and the DP applier.
