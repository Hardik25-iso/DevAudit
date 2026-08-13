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

**robots.txt is a hard limit.** `cdn.s3waas.gov.in` serves
`User-agent: * / Disallow: /`, and every `.nic.in` district site publishes
through it — hundreds of bodies, uncollectable. This bounds what any corpus of
this kind can contain and belongs in the write-up's limitations.

## Running it

```bash
python collect.py --dry-run           # discover, download nothing
python collect.py --per-source 60     # random draw, capped per body
python audit_corpus.py                # audit into manifest, print report
python -m pytest tests/               # 17 calibration tests
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

## Next phase

Phase 0 — extraction schema and annotation guidelines — then Phase 2 ground
truth. Both are unblocked. Start them in a **fresh session**; this file is the
handoff.
