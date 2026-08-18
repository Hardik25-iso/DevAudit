# Phase 0 — Extraction Schema and Annotation Guidelines

Status: implemented and run once. **The two-pass protocol in §5 was not
carried out — see §5.5 for the deviation and what it costs.** Guideline
version **0.1**.
Written 2026-08-14, after Phase 1 answered GO.

Phase 1 asked *is this problem real*. Phase 2 asks *is the instrument right*.
That second question needs something Phase 1 never produced: labelled data at
the grain the instrument actually works at. This document defines that grain,
the fields stored at it, the labels a human applies to it, and how two passes
over the same item get reconciled into one defensible answer. That last part
is the design; §5.5 records that it was not followed.

---

## 1. The existing architecture, in plain English

Three stages, and the seam between the second and third is where Phase 0 has
work to do.

**Stage 1 — collection.** `collect.py` walks each body's site under
`robots.txt`, downloads PDFs to `D:\DevAudit-data\raw`, and writes provenance
into `manifest.sqlite` on the SSD: `discovered` (every URL seen and its fate)
and `documents` (one row per file, keyed by SHA-256, carrying source URL,
issuing body, doc type, size, timestamp). Identity is the checksum, so the same
PDF served from two portals is one row.

**Stage 2 — measurement.** `font_audit.audit(path)` opens one PDF with PyMuPDF
and produces one flat Python dict. It does four different things:

1. *Font inventory* — `collect_fonts()` walks every page and returns one record
   per font **xref**: base name (subset prefix stripped), encoding, embedded
   flag, and whether a `ToUnicode` CMap is present.
2. *Name matching* — `classify_fonts()` sorts those names against
   `LEGACY_PATTERNS` and `KNOWN_GOOD`. A name matching neither is "unknown",
   and only counts as suspicious when it is also embedded with no `ToUnicode`.
3. *Per-font output analysis* — `collect_font_text()` uses PyMuPDF's span-level
   font tagging to accumulate up to 2,500 characters of rendered text per font
   across the first 8 pages. `classify_font_output()` then judges each font on
   its own text: mojibake ratio, ASCII `k` frequency, symbol-inside-word rate,
   with an English stop-word gate. This is the part that mattered most, because
   the same measurements taken per document are diluted into uselessness.
4. *Document-level structure* — the first 30 pages are extracted as one string
   and tested with `INVALID_MATRA`, which fires on any dependent vowel sign not
   directly after a consonant. That single rule is the whole SUSPECT class.

**Stage 3 — persistence and bucketing.** `audit_corpus.py` runs stage 2 over
every row in `documents` and writes results into `audit`, one row per document.
`decide_verdict(row)` then assigns exactly one of five buckets, first match
wins: SCAN → LEGACY → SUSPECT → UNCLASSIFIED → CLEAN. Because that function
reads nothing but the row, `--rebucket` can re-apply thresholds straight from
the database.

### Where the architecture stops short

The measurement grain is the **font**. The storage grain is the **document**.
Everything stage 2 learned per font is flattened into delimited text columns on
the way into stage 3, and the live schema confirms it:

```
legacy_fonts        TEXT   -- 'DVBW-TTSurekh;DVBW-TTSurekh,Bold'
unknown_fonts       TEXT
all_fonts           TEXT   -- 'Calibri|0;Calibri|1;Book Antiqua,Bold|0;...'
legacy_by_output    TEXT   -- 'DVBW-TTSurekh=8bit(0.76);...'
n_legacy_by_output  INTEGER
```

Five consequences follow, and they are the reason Phase 0 exists:

1. **Only convictions survive; measurements do not.**
   `classify_font_output` returns the first rule that fired as a string —
   `8bit(0.76)`. A font that was measured and *cleared* leaves no record at
   all. There is nowhere in the database to read the mojibake ratio of a font
   the detector let through.

2. **`--rebucket` therefore only half works.** It can re-apply *name* patterns,
   because `all_fonts` preserves names. It cannot re-apply per-font
   *thresholds*: change `PERFONT_MOJIBAKE` from 0.15 to 0.12 and there is no
   stored number to compare against, so you are back to re-reading 1,602 PDFs.
   Phase 2's central activity is sweeping exactly those thresholds against
   labels, and the current schema cannot support it.

3. **`all_fonts` is a lossy multiset.** It is written per xref, not per name,
   so real rows read `Calibri|0;Calibri|0;Calibri|1;...` — the same face with
   contradictory flags, duplicated a dozen times. `rematch_fonts` collapses it
   into sets, which works for its purpose and is unqueryable for any other.

4. **Diagnostics are computed and thrown away.** `audit()` measures
   `word_initial_matras`, `adjacent_matras`, `virama_then_matra`,
   `mojibake_ratio`, `ascii_k_ratio`, `dev_digits`, `ascii_digits` — and
   `AUDIT_FIELDS` stores none of them. These are precisely the "which way did
   it break" numbers an annotator or a reader of the write-up would want.

5. **SUSPECT has no per-font evidence, by construction.**
   `classify_font_output` returns `None` as soon as a font shows more than 20
   real Devanagari characters, deferring to the document-level structural
   check. So for the 11.2 points of the finding that are wrong-CMap damage,
   nothing in the database says *which font* produced the invalid Devanagari.
   That is a third of the result resting on a document-level aggregate.

And one thing missing from all three stages: **no extracted text is stored
anywhere.** Annotation is reading text and judging it. Today that requires the
external drive, which means ground truth cannot be produced on a train, a
laptop, or by anyone who does not have the disk.

---

## 2. Proposed extraction schema

Implemented in `phase0_schema.py`. Nothing in `documents`, `discovered` or
`audit` is altered — the Phase 1 figure stays traceable to the rows that
produced it, and ground truth is additive evidence beside the audit.

### 2.1 `font_observation` — the unit of everything

One row per **(document, font name)**. This is deliberately the same unit for
measurement, for annotation, and for evaluation; when those three differ you
end up comparing a label to a number that describes something else.

Grain note: a document embeds the same face under several xrefs. The defect is
a property of the face's *output*, not of the PDF object, so xrefs are counted
(`n_xrefs`) rather than given rows of their own. Font names are only unique
*within* a document — `TT313t00` means a different face in the next file — so
the key is `(sha256, font_name)` and never `font_name` alone.

| Group | Fields |
|---|---|
| identity | `obs_id`, `sha256`, `font_name`, `raw_font_name` |
| declared | `n_xrefs`, `embedded`, `has_tounicode`, `encoding`, `font_type`, `first_page`, `n_pages_seen`, `n_pages_declared` |
| sample size | `sampled_chars`, `dev_chars`, `latin_letters`, `n_tokens` |
| shipped signals | `mojibake_ratio`, `ascii_k_ratio`, `ascii_k_eligible`, `symbol_per_1k`, `english_ratio` |
| structural signals | `invalid_matras`, `invalid_rate_per_1k`, `word_initial_matras`, `adjacent_matras`, `virama_then_matra`, `detached_matras` |
| candidate signal | `invalid_matras_nospace` |
| detector output | `detector_label`, `detector_reason` |
| provenance | `signals_version`, `extracted_at` |

Three decisions inside that table are worth defending:

**Signals are stored for every font, convicted or not.** This is the change
that turns a threshold sweep into a SQL query and makes an ROC curve possible.
It is also the only way to see what the detector's silence costs, and this
instrument's documented failure mode is silence.

**Gates are reported, not applied.** `ascii_k_eligible` records whether the
200-letter floor was met rather than zeroing the ratio below it. That floor was
added to kill one false positive (a Myriad Pro signature block at 7.8% `k` over
154 characters); storing the gate separately keeps the floor itself open to
re-examination instead of baking it into the data.

**Structural counts are taken per font, always** — including for fonts where
`classify_font_output` defers. That closes gap 5 above: for the first time
there will be per-font evidence for the SUSPECT class.

**`n_pages_seen` and `n_pages_declared` are stored as a pair.** The first
counts pages inside the capped text sample, the second counts pages the font
is declared on across the whole document. The pair is what makes an
under-sampled font visible — see §8.2, where the first fixture tested turned
this from a precaution into a measured problem.

**`invalid_matras_nospace` is a candidate, not a detector.** It re-runs the
structural check with whitespace removed. If the violation count collapses, the
damage is a space the extractor inserted mid-word (`जमा बाज ू`) rather than a
reordered glyph stream — a different, milder defect. It is stored and wired
into no verdict. Three candidate detectors have already been measured and
rejected on this project; none should ship on plausibility.

### 2.2 `excerpt` — the text itself

`excerpt(obs_id, page, kind, char_start, text)`, several rows per observation.
Storing verbatim extracted text is what makes annotation possible with the
drive detached.

**It does not make the annotation set redistributable.** An earlier draft of
this section said it did — "short quoted fragments for research, not the source
documents" — and that is wrong. The excerpts are drawn from municipal
documents, and municipal documents contain people: of the 12,004 excerpts
extracted, 39 carry a date of birth and 60 name a caste category, alongside
the full names they belong to. Publishing them would republish personal data
about identifiable private individuals, aggregated and made searchable, which
is a different act from that data sitting in a PDF on a municipal portal.

`export_manifest.py` reads only `documents` today, so nothing is exposed — the
risk is that extending the release to cover excerpts looks like an obvious
convenience. It is not. Anything built on this table has to answer for the
personal data in it first; see §9.7.

Three `kind` values, and the reason for each:

- `head` — the first text the font renders. Cheap orientation.
- `random` — a seeded draw from the font's sampled text. Necessary because if
  excerpts were chosen by the detector, the annotator would only ever see what
  the detector already found, and the evaluation would flatter itself.
- `violation` — a window around a structural violation. The wrong-CMap class is
  invisible unless you are shown where it broke; without this kind, annotators
  systematically under-report CMAP_INVALID.

### 2.3 Gaps named, not invented

- **OCR text layers are not detected anywhere.** Phase 1 splits SCAN from
  not-SCAN on characters per page, so a scan carrying an OCR layer lands in
  whatever bucket its OCR output produces. OCR error and encoding error are
  different problems; today the corpus cannot separate them. Phase 0 handles
  this by making it a human judgement (`doc_annotation.text_layer`) rather than
  inventing a detector for it.
- **Page images are not stored.** Adjudication sometimes needs to see the
  rendered page. That needs the drive, and is accepted as an adjudication-time
  cost rather than a storage decision made now.
- **Language is not recorded.** Marathi and Hindi are not distinguished
  anywhere in the pipeline, though the two legacy font ecosystems differ.
  Named as a gap; not added, because nothing currently measures it.

---

## 3. Proposed annotation schema

### 3.1 Label vocabulary — font observations

Eight values. They map onto the three mechanisms Phase 1 actually found, plus
the abstentions without which annotators guess.

| Label | Means | Looks like |
|---|---|---|
| `CORRECT` | Extracted text is the right characters in the right order | `नाशिक महानगरपालिका` |
| `LEGACY_8BIT` | Glyphs mapped onto the Latin-1 supplement | `xÉÉÊ¶ÉEò ¨É½þÉxÉMÉ®ú` |
| `LEGACY_ASCII` | Kruti-Dev-style remap onto plain ASCII | `i'kq dY;k.k foHkkx` |
| `LEGACY_SYMBOL` | Remap using symbols between letters | `A«BC§D` |
| `CMAP_INVALID` | Real Devanagari codepoints, structurally impossible | `जानवे ारी`, `स्थालनक` |
| `PARTIAL` | Same font name, some text correct and some corrupt | subset merges |
| `NO_LINGUISTIC_TEXT` | Font renders digits, rules, logos — nothing to judge | `1 2 3 ▪ ▪` |
| `UNDECIDABLE` | Annotator cannot tell, or there is too little text | — |

`UNDECIDABLE` is not a failure of the annotator. It is the same design choice
as `UNCLASSIFIED` being a bucket rather than an error: an honest abstention is
information, a coerced guess is noise wearing a label.

**One value is detector-only: `NO_EVIDENCE`.** When the detector clears a font
it has not established that the font is `CORRECT`, only that nothing fired —
which is the distinction the whole project rests on, since this instrument's
documented failure mode is silence. An annotator may say `CORRECT`; the
detector may not. Keeping the two words apart is what stops "we found nothing"
being read later as "there is nothing there".

### 3.2 Document-level labels

Only what is genuinely document-level goes here (`doc_annotation`):

- `text_layer` ∈ {`NONE`, `DIGITAL`, `OCR`, `MIXED`}
- `primary_script` ∈ {`deva`, `latin`, `mixed`, `other`}
- `doc_usable` ∈ {`YES`, `PARTIAL`, `NO`} — could a downstream consumer use
  this text layer as-is?

Everything else about a document is **derived** from its font labels by the
same first-match-wins rule the detector uses, so document-level ground truth
comes free and no annotator labels the same thing twice:

```
text_layer = NONE                         -> SCAN
any font LEGACY_8BIT|ASCII|SYMBOL         -> LEGACY
else any font CMAP_INVALID                -> SUSPECT
else any font UNDECIDABLE                 -> UNCLASSIFIED
else                                      -> CLEAN
```

### 3.3 Storage

`annotation` is **append-only**: one row per `(obs_id, annotator, round)`,
never updated in place. Agreement can only be computed from labels as
originally given; overwriting a first pass with an adjudicated one destroys the
evidence that adjudication was needed. `adjudication` then holds exactly one
final label per observation, with `basis` recording how it became final.

`annotation.saw_detector_output` is a per-row flag, not a protocol-level
promise. Any row where it is 1 is excluded from evaluation sets.

Every annotation carries `guideline_version`. A label is only interpretable
against the definitions in force when it was given, so changing this document's
meaning bumps the version rather than silently reinterpreting old labels.

### 3.4 `annotation_sample` — the frame

Ground truth will be drawn **stratified**, which means the labelled proportion
is not the corpus proportion. `selection_prob` is stored per row so a corpus
estimate can be recovered by inverse-probability weighting. Sampling has bitten
this project three times; the fix is to record the frame before drawing, not to
reconstruct it afterwards.

Proposed strata — the second axis exists to break a circularity. Phase 1
validated the per-font detector using fonts identifiable *by name* as ground
truth. That is fine for what it claimed, but it cannot tell you how the
detector performs on the 32% of legacy documents where no name matched. Ground
truth must oversample exactly that cell.

| | name matches LEGACY | name matches KNOWN_GOOD | name uninformative |
|---|---|---|---|
| detector says legacy | 50 | 50 | **100** |
| detector says clean | 50 | 100 | **100** |

Roughly 450 font observations, ~40% of them in the cells where names tell you
nothing. Plus 150 documents for `text_layer`, stratified on SCAN / not-SCAN.
At ~30 seconds an observation that is around five hours of annotation, which is
a real number a single person can finish.

---

## 4. Annotation guidelines (v0.1)

### 4.1 The two rules that override everything

**Never label from the font name.** The name is displayed only so the finished
data can be analysed by name later. `TT313t00` and `Z@RAF1C.tmp` are real font
"names" in this corpus — the second is a leaked Windows temp filename. A
respectable name means nothing: the entire CMAP_INVALID class occurs in Mangal,
Aparajita and Adobe Devanagari.

**Never label from the rendered page.** The page looks *correct* — that is the
whole phenomenon. Use the rendered page only to establish what the text was
supposed to say, never as evidence that the text layer is fine.

### 4.2 Decision procedure

1. **Is there anything to judge?** Digits, rules, bullets, a logo → 
   `NO_LINGUISTIC_TEXT`. Under ~200 characters → `UNDECIDABLE`.
2. **Does the text contain Devanagari codepoints?**
   - Yes → step 3.
   - No, and it is dense in accented Latin-1 (`É Ê ¶ ½ þ ®`) → `LEGACY_8BIT`.
   - No, plain ASCII only → step 4.
3. **Read the Devanagari.** Does it form real Marathi/Hindi words?
   - Yes → `CORRECT`.
   - No, and the defects are structural — a matra opening a word, two matras
     adjacent, a matra after a virama → `CMAP_INVALID`.
   - The only defect is a space in the wrong place, and closing the space
     yields a real word → `CORRECT`, note `#spurious-space` (see 4.4).
4. **Read the ASCII.** Does it read as English, or as names/transliteration?
   - Yes → `CORRECT`.
   - No, and it is dense in `k j ; ' [ ]` with word shapes like
     `i'kq dY;k.k foHkkx` → `LEGACY_ASCII`.
   - No, and symbols appear *inside* words (`A«B`, `C§D`) → `LEGACY_SYMBOL`.
   - No, and you cannot tell which → `UNDECIDABLE`.
5. **Both correct and corrupt text under one font name** → `PARTIAL`.

Record `confidence` 1–3 on every row. A 1 is not a defect; it is what makes the
low-confidence subset findable later.

### 4.3 Legitimate PDF and font behaviour that is *not* corruption

This is the list that keeps precision honest. None of these, alone, is
evidence of a defect:

- **Subset prefixes.** `ABCDEF+Arial` is how every producer embeds a subset.
- **Opaque or absurd font names.** `TT313t00`, `Z@RAF1C.tmp`. Judge the output.
- **Missing `ToUnicode` on a standard-encoded Latin font.** Text still extracts
  correctly from WinAnsiEncoding. Missing `ToUnicode` is a *risk factor* the
  pipeline uses to decide what to look at, never a verdict.
- **Ligatures.** `ﬁ ﬂ ﬀ` as single codepoints is correct behaviour.
- **ZWJ / ZWNJ** (U+200D / U+200C) inside conjuncts. These are *required* in
  correct Devanagari and must never be read as junk.
- **Decomposed nukta forms.** `क` + `़` and precomposed `क़` are both valid.
- **Devanagari digits** `०-९`, and Latin digits mixed into Devanagari text.
- **English inside a Marathi document.** Headers, tender numbers, unit names.
  Language mixing is normal in these documents.
- **Soft hyphens** (U+00AD), currency and unit symbols (`₹ ° %`).
- **Reading-order scrambling.** Columns interleaved, a footer landing
  mid-paragraph. Every *word* is correct; the order is not. This is an
  extraction-order defect, not an encoding defect → `CORRECT`, note
  `#reading-order`. Phase 3 benchmarks extractors on exactly this.
- **Rotated or vertical text** extracting in an odd sequence. Same reasoning.

### 4.4 The one boundary call this document makes

**Spurious spaces are `CORRECT` with a note, not corruption.**

`जमा बाज ू` — the matra is detached by a space. Every glyph maps to the right
codepoint; what is wrong is the extractor's word-boundary heuristic, which
inserted a space at a kerning gap. The *font* is fine. Since the unit of
annotation is the font, labelling this as font corruption would attribute the
defect to the wrong component and would make the detector look wrong when it is
right.

It is still a real defect for any downstream consumer, so it is captured as a
mandatory `#spurious-space` note plus the `invalid_matras_nospace` signal, and
it is reported separately in the write-up.

**Settled 2026-08-15 by the author: `CORRECT` with the note, as written above.**
The guidelines are frozen at **v0.1** and annotation may begin. Every
`annotation` row carries `guideline_version`, so if this is ever revisited the
cost is re-labelling the rows the change touches, not starting over.

### 4.5 What an annotator is shown

The excerpts, how much text they were drawn from, and the rendered page on
request. **Not** shown: the font name, the detector's verdict, the signal
values, the Phase 1 bucket, or the issuing body.

An earlier draft of this section showed the font name. That was wrong, and
`annotate.py` hides it. §4.1 tells the annotator never to label from the name;
displaying it anyway asks them to un-see a strong prior — `DVBW-TTSurekh` is
as much of a hint as a verdict would be. It also breaks the strata: the whole
point of the `name_uninformative` cell is to measure the detector where names
say nothing, which cannot be done if the annotator was reading names.
`--show-name` exists for adjudication and sets `annotation.saw_font_name`, and
rows where it is 1 are excluded from any name-blind claim.

---

## 5. Agreement and adjudication

### 5.1 The honest constraint

This is a one-person project. There is no second independent human annotator,
and pretending otherwise would be the sort of thing that quietly inflates a
reliability figure. The protocol below is designed around that and states its
own weakness rather than hiding it.

### 5.2 Protocol

1. **Pass A — human, blind.** The author labels every sampled observation.
   `annotator='hardik'`, `round=1`, `saw_detector_output=0`.
2. **Pass B — LLM, blind, independent prompt.** The same excerpts labelled by a
   model with the guidelines and no access to signals, names excluded.
   `annotator='llm:<model-id>'`, `round=1`. This is a *second opinion*, not a
   second human; it is not independent in the way inter-annotator agreement
   normally assumes, and the write-up must say so.
3. **Pass C — intra-annotator reliability.** A 15% random subsample,
   re-labelled by the author **blind, at least 7 days later**, order reshuffled.
   `round=2`. Cohen's κ between rounds 1 and 2 is the honest reliability figure
   for this project — it measures whether the guidelines are stable enough for
   one person to apply consistently.
4. **Adjudication.** Every A/B disagreement gets a third look with the rendered
   page beside the text. The resolution is written to `adjudication` with
   `basis='adjudicated'` and a note giving the reason. Agreements are written
   with `basis='unanimous'`. Nothing in `annotation` is edited.

### 5.3 Reporting rules

- Report **Cohen's κ** (round 1 vs round 2) and raw percent agreement, with n.
- Report the **A/B disagreement rate** and, per class, which way it went. A
  class where the model and the human systematically disagree is a guideline
  defect, not an annotator defect — fix the guidelines, bump the version, and
  re-annotate that class.
- κ below **0.7 on any class** blocks that class from evaluation use until the
  guidelines are revised. Fixed here, in advance, so it cannot be relaxed later
  to make a result work.
- Observations with `basis='single'` may appear in the released data but never
  in an agreement calculation.

### 5.4 Pre-registration

The strata, the sizes, the seed, the κ threshold and the label definitions are
fixed **before** the first label is written, in the same spirit as the 15%
go/no-go threshold that was fixed before any data existed. Changing any of them
afterwards means bumping `GUIDELINE_VERSION` and saying so in the write-up.

### 5.5 Deviation from this protocol, decided 2026-08-15

**The two-pass design in §5.2 was not carried out, and the κ floor in §5.3 was
not met. The author has decided to proceed anyway.** This section records that
as a deviation rather than absorbing it into the design, because §5.4
pre-registered the floor precisely so it could not be relaxed once the data
existed. Relaxing it is exactly what has happened, and a reader is entitled to
know that rather than to infer it.

**What exists.** One model pass (`llm:claude-opus-5-interactive`) covering all
434 observations, and 13 human labels, 12 of which agreed. `adjudication`
therefore holds 421 rows at `basis='single'`, 12 `unanimous`, 1 `adjudicated`.

**What this costs, stated plainly:**

1. **There is no inter-annotator reliability figure.** κ over 13 overlapping
   items is not an estimate of anything. Any per-class κ the tool prints should
   be read as a diagnostic, never as a reliability claim.
2. **The labels are one automated reading of the excerpts.** The detector and
   the labeller work by different mechanisms — byte-level heuristics against
   the raw text versus a language model reading it — so the comparison is not
   circular. But an error both share is invisible to it. The `LEGACY_SYMBOL`
   result is the live example: the detector scores 0.000 recall against labels
   that a single labeller assigned, and nothing here can distinguish "the
   detector misses this class" from "both parties scope this class the same
   wrong way".
3. **The pass was not blind to the stored signals.** Contrary to §4.5, the
   labelling interface used for the bulk of the pass displayed `sampled_chars`
   and `dev_chars` alongside the excerpts, and for the first handful of items
   the stratum name — which encodes the detector's verdict. `saw_detector_output`
   is recorded as 0 on those rows and is, to that extent, wrong. The blind batch
   pass in `llm_annotate.py` does not have this defect; it remains unrun.

**What the figures may therefore be called.** "The detector's agreement with a
single model-assisted labelling pass over a stratified sample." Not ground
truth, not validated precision and recall, and not a corpus rate with error
bars in the sense §6 promised — the interval reflects sampling variance only,
and says nothing about label error.

**What would lift the limitation**, in increasing order of cost: run
`llm_annotate.py --submit` for a genuinely blind second pass; complete a human
pass over the 434; or draw a fresh smaller sample and label it twice properly.
Any of the three restores a real κ and returns §5.3's gate to force.

---

---

## 6. How this supports detector evaluation

Once `ground_truth` is populated, four things become queries instead of
projects:

1. **Per-class precision and recall** at the font grain, straight from
   `ground_truth`, comparing `detector_label` to `final_label`. Phase 1's
   "precision 1.000" was measured against name-identifiable fonts; this is the
   first measurement that includes the fonts whose names say nothing.
2. **Threshold sweeps.** Every signal is stored for every font, so the full
   PR curve for `PERFONT_MOJIBAKE`, `PERFONT_ASCII_K`, `PERFONT_SYMBOL` and
   `SUSPECT_RATE_PER_1K` can be drawn without opening a single PDF. The
   currently-shipped values can then be shown to sit inside a gap, or not.
3. **Recall on the silent side.** The `detector says clean` strata are the only
   place the instrument's documented failure mode is visible at all.
4. **A corpus estimate with an interval.** Inverse-probability weighting from
   `selection_prob` turns labelled counts into a corpus-level rate with a
   confidence interval — the first version of the headline figure that has
   error bars rather than a caveat paragraph.

And one candidate gets its first fair test: `invalid_matras_nospace` either
separates spurious-space from genuine reordering against the labels, or it is
rejected like the three before it.

---

## 7. Implementation

Test suite went from 17 to **41 passing**. The original 17 pass unchanged at
every step, which is the check that matters: no Phase 1 number moves.

**`font_audit.py` — measuring split from deciding.**
`measure_font_text(text)` returns every signal unconditionally as a dict.
`classify_font_output(text, m=None)` is reimplemented on top of it with
identical thresholds, identical ordering and byte-identical reason strings, and
accepts pre-computed measurements so the extractor does not measure twice. It
additionally computes per-font structural counts (which the classifier still
defers on, exactly as before) and the candidate `invalid_matras_nospace`.
`collect_font_spans()` replaces `collect_font_text()` as the primitive,
recording the page and offset of every span it keeps — `collect_font_text()`
remains as a thin wrapper so `audit()` is untouched. `collect_fonts()` now
accumulates the page set per xref. Nothing that feeds a verdict changed.

**`phase0_schema.py` — the tables.** Creates `font_observation`, `excerpt`,
`annotation_sample`, `annotation`, `adjudication`, `doc_annotation` and the
`ground_truth` view, plus a `MIGRATIONS` list for columns added later (the same
pattern `audit_corpus.py` uses). Verified on a copy first, then **run against
the live `data/manifest.sqlite`**, which was backed up to
`data/manifest.pre-phase0.sqlite` beforehand. The three Phase 1 tables are
unaltered and still hold 1,602 rows each.

**`extract_observations.py` — the extractor.** One drive-attached pass writing
`font_observation` and `excerpt`. Also `--verify`, which reads no PDFs and
reconciles per-font extraction against the per-document audit: the count of
output-convicted fonts per document must equal `audit.n_legacy_by_output`, and
a mismatch means the extractor and the auditor are not measuring the same
thing. `--dry-run` and `--limit` for cheap checks; resumable by default.

It emits a per-font `CMAP_INVALID` label by applying the existing
**document-level** SUSPECT rule per font. This is new and unvalidated, and it
is flagged as such in the code, in `detector_reason`, and here. It feeds no
verdict — `decide_verdict()` is untouched — and exists so Phase 2 has something
to measure for the class that currently has no per-font evidence at all.

**`draw_annotation_sample.py` — the frame.** Pure `plan_strata()` plus a thin
DB wrapper. Records stratum size, draw size and selection probability per row,
takes small strata whole at probability 1.0 rather than silently under-filling,
and sorts before shuffling so the draw does not depend on SQLite row order.

**`tests/test_phase0_extraction.py` — 24 new tests.** The load-bearing ones:
the classifier gives identical answers with and without reused measurements;
signals survive for fonts the classifier clears; structural counts exist even
where it defers; the draw is reproducible from its seed and invariant to row
order; and the Myriad Pro signature block that produced Phase 1's one false
positive still abstains.

**Run so far.** The three SSD fixtures only. `extract_observations.py` has not
been run over the corpus — that needs the external drive.

---

## 8. Assumptions, and one that did not survive testing

### 8.1 Still assumed

1. **PyMuPDF's span-level font attribution is correct** — that the font tagged
   on a span is the font that rendered it. The entire per-font design rests on
   this and it has never been verified.
2. **Excerpts suffice for most labelling.** Some fraction will need the
   rendered page; that fraction is unknown until annotation starts.
3. **The author reads Marathi and Hindi well enough** to judge whether
   extracted Devanagari forms real words. Where not, the correct action is
   `UNDECIDABLE`, not a guess.
4. **Font name identity is document-local.** The same string in two PDFs may be
   two faces, which is why observations key on `(sha256, font_name)`.
5. **Devanagari only**, as in Phase 1. Nothing here transfers unchanged to
   Tamil, Telugu, Kannada or Malayalam.
6. **The corpus is fixed for Phase 2.** Ground truth is drawn from the 1,602
   documents already collected; no new collection is assumed.

### 8.2 The 8-page cap is not representative — measured, not feared

This started as an assumption to check later. It failed on the first fixture
tested, so it is a finding instead.

`PERFONT_MAX_PAGES = 8` caps per-font text accumulation at the first 8 pages.
On the 43-page PMC budget — the document the calibration suite labels
"Shree-Dev, legacy" — the per-font view comes out like this:

| font | declared on | sampled | detector says |
|---|---|---|---|
| ArialUnicodeMS | 43 pages | 2,178 chars | `CMAP_INVALID`, 23.9 invalid matras /1k |
| ArialMT | 41 pages | 12 chars | `UNDECIDABLE` |
| Shree-Dev-0708 | 24 pages | 24 chars | `UNDECIDABLE` |

Two things follow, and the second is the uncomfortable one.

**The cap starves sparsely-used fonts.** `Shree-Dev-0708` renders a little text
on 24 of 43 pages — a heading or label face. Over the whole document it
accumulates 369 characters; over the first 8 pages, 24. It never reaches the
200-character floor, so the detector abstains, and that abstention is an
artifact of the sampling window rather than a fact about the font.

**This document is bucketed LEGACY on a name match against a font that renders
almost nothing.** The body text carrying the actual corruption is
`ArialUnicodeMS` — a respectable Unicode face with a broken CMap and 51
structural violations. The Phase 1 verdict is correct, and correct for the
wrong reason. That is exactly what per-font ground truth exists to catch, and
it surfaced in the first document looked at through this lens.

The mitigation shipped makes it **visible rather than fixed**:
`n_pages_declared` beside `sampled_chars` turns "declared on 24 pages, sampled
24 characters" into a queryable red flag, and `draw_annotation_sample.py`
reports how many below-floor observations are declared on 5+ pages instead of
quietly dropping them. Raising the cap is not the obvious fix — it costs a
full-document `get_text("dict")` per file, which is what the cap was protecting
against — and the right size for it is a question the corpus-wide distribution
of that pair can answer in Phase 2.

### 8.3 Reading more text can un-convict a font

The targeted `--deep` pass (163 documents, every page) was expected to be a
strict superset of the 8-page pass. It was not: two documents convicted
*fewer* fonts. Both are dilution, and they point opposite ways.

**A false positive that more text corrected.** `TimesNewRoman,Bold` measured
`k=0.058` over 8 pages — above the 0.05 threshold, convicted as ASCII-remap.
Over the whole document it measures `k=0.019`. A genuine Latin face, convicted
by a small sample of name-dense English. The same failure mode as the Myriad
Pro signature block, and the sample-size floor did not catch this one because
the sample was large enough — just unrepresentative.

**A false negative the dilution created.** In the other document the
`AkrutiDev*` family fell from `k=0.056` to `k=0.029–0.037`, with mojibake at
0.08 against a 0.15 threshold and symbol rate at 2–3 against 10. Just below
every line. Reading its stored excerpt settles what the numbers could not:

```
AkrutiDevBharatiBold:  "cne[e De@ke�� 1976 - me#ece He�eefOeke�ejer ..."
                     = "म्हाडा अधिनियम 1976 - सक्षम प्राधिकारी ..."
```

**Akruti is a fifth undocumented legacy family**, and it is absent from
`LEGACY_PATTERNS`. It sits in the gap between the two encoding styles the
detector knows: too little Latin-1 for the Marathi 8-bit rule, too little `k`
for the Kruti Dev rule. Corpus-wide it is 11 observations in 1 document, and
that document is already `LEGACY` on another font, so nothing in the Phase 1
figure moves. It was confirmed by reading extracted text, exactly as the
previous four families were — and, for the first time, without opening a PDF.

The design conclusion is the uncomfortable one: **a bigger sampling window is
not uniformly better.** The dilution argument that motivated per-font
measurement reappears *within* a font across a long document. Cap size is
therefore a real parameter with error in both directions, not an obvious
"more is better" knob, and it belongs in the Phase 2 sweep alongside the
thresholds rather than being set by hand now.

---

## 9. Unresolved questions

1. ~~**Is spurious-space really `CORRECT`?**~~ **Resolved 2026-08-15:**
   `CORRECT` with a mandatory `#spurious-space` note, per the reasoning in
   §4.4 — the glyph mapping is right, so the defect does not belong to the
   font, which is the unit being labelled. Kept here rather than deleted
   because a pre-registered protocol should show what was asked and when it
   was answered, not only the answer. Guidelines frozen at v0.1.
2. **Should `PARTIAL` exist, or should the unit be finer?** `PARTIAL` is an
   admission that (document, font) is occasionally too coarse. The alternative
   is annotating spans, which multiplies the workload. Keep `PARTIAL`, measure
   how often it fires, revisit only if it is common.
3. **Where does OCR-layer detection come from?** Annotation gives labels for
   150 documents. Whether a *detector* follows is a Phase 2 decision, and it
   should be made from those labels rather than in advance.
4. **What n makes κ defensible?** 15% of 450 is ~68 items. That is thin for
   per-class κ on the rarer classes. Either raise the re-annotation fraction
   for rare classes or report κ only in aggregate, and say which.
5. **Does the `k`-frequency threshold generalise** to Kruti Dev variants not
   observed in this corpus? The mechanism argues yes; nothing measures it.
6. **External validity is bounded by robots.txt.** Every S3WAAS-published body
   is uncollectable, so ground truth describes self-hosting bodies only. This
   belongs in the write-up's limitations, not in a schema.
7. **The excerpt table holds personal data, and nothing decides what happens
   to it.** Names, dates of birth, and caste categories of identifiable private
   individuals — measured, not feared: 39 excerpts carry a DOB pattern and 60 a
   caste category, across 11 documents. Three decisions follow, none of them
   made: whether excerpts may ever be released (§2.2 says no by default);
   whether to redact before annotation, which risks removing the very defects
   being labelled; and what the write-up says about handling personal data
   collected incidentally from public portals. India's DPDP Act 2023 is the
   relevant regime and it is not obvious that "already public" settles it.
   Until then, treat `manifest.sqlite` as a file containing personal data:
   do not publish it, and keep it out of anything shared.
8. **Should `audit` gain the discarded diagnostic columns?** They are computed
   and thrown away today. Adding them is a one-line change to `AUDIT_FIELDS`
   but requires a re-audit to populate, so it should ride along with the
   extractor pass rather than be a run of its own.

---

## 10. Exact next step for Phase 2

Steps 1–3 are done. Step 4 is the next action and it is the only one that
needs the external drive.

1. ~~Create the tables~~ — done, live, backed up first.
2. ~~Write the extractor~~ — done, `extract_observations.py`, 24 tests.
3. ~~Write the sample drawer~~ — done, `draw_annotation_sample.py`.

4. **Attach the drive and extract.** Sanity-check on a handful first, since
   nothing has yet seen a real corpus document:

   ```bash
   python extract_observations.py --dry-run --limit 5
   ```

   Then the full pass, logged rather than printed — it emits a line per 25
   documents and the tail is the only part worth reading:

   ```bash
   python extract_observations.py > data/extract.log 2>&1 && tail -20 data/extract.log
   ```

   Budget roughly the 385 seconds a full re-audit took: same single pass over
   the same files, plus excerpt writing.

5. **Reconcile before believing any of it.** Runs automatically at the end of
   step 4, and separately with `--verify` (no drive needed). The count of
   output-convicted fonts per document must equal `audit.n_legacy_by_output`.
   Any mismatch means the extractor and the auditor are not measuring the same
   thing, and no label written against those rows would mean anything.

6. **Look at the sampling-cap distribution** before drawing, now that §8.2
   says the cap distorts abstentions. `draw_annotation_sample.py --dry-run`
   prints how many below-floor observations are declared on 5+ pages. If that
   number is large, raising `PERFONT_MAX_PAGES` and re-running step 4 is the
   cheaper decision to make *before* annotating than after.

7. **Draw the sample.** No drive needed:

   ```bash
   python draw_annotation_sample.py --sample-id gt-v1 --seed 20260814
   ```

8. **Resolve §9.1, freeze the guidelines, annotate.** No drive needed — that
   is the entire point of storing excerpts. An annotation UI does not exist
   yet; the smallest thing that works is a script that prints one observation's
   excerpts and writes one `annotation` row.

Unrelated and still open from Phase 1: `check_detector_overlap.py` has never
run. It also needs the drive, and it is a separate question that should not be
folded into the same pass.

---

## 11. Interim run, 2026-08-15 — the pipeline works, the numbers do not count

Every step from §10 has now been executed once, end to end. This section
records what that established and what it did not, because the figures it
produced are easy to mistake for results.

### 11.1 What was actually run

All 434 sampled observations were labelled by `llm:claude-opus-5-interactive`
— a model pass performed card by card in a working session. `hardik` labelled
4. `adjudicate.py` therefore settled 3 as `unanimous`, left 1 disagreement
open, and wrote the remaining 430 at `basis='single'`.

**A single-pass label is one opinion, not ground truth.** Per §5.3 those rows
are barred from agreement calculations, and the κ over the handful of
overlapping items estimates nothing. The author has since decided to proceed on
this basis regardless; **§5.5 records that decision, and it governs how every
figure below may be described.** Nothing in §11.3 is quotable as validated
precision, recall, or a corpus rate.

The interactive pass is also **not** the blind second opinion the protocol
calls for: it saw stratum labels and had session context the batch pass does
not. `llm_annotate.py --submit` remains unrun, and only its output belongs in
an agreement figure.

### 11.2 What the run did establish

- **The chain holds.** `annotation` → `adjudication` → `ground_truth` →
  `evaluate.py` produced all four reports without manual repair.
- **§2.2's premise is validated.** All 434 were labelled from stored excerpts
  with the external drive detached and no PDF opened. Annotation is genuinely
  portable, which was the point of storing text.
- **The strata contain what they were drawn to contain.** The
  `no_evidence/*` cells produced real positives — the detector's silences are
  visible, which is the only way recall can be measured at all.

### 11.3 Numbers, for orientation only

| | |
|---|---|
| binary precision / recall | 0.975 / 0.830 |
| false negatives vs false positives | 40 vs 5 |
| corpus rate, reweighted | 58.1% ± 2.6 |

The 40:5 asymmetry is the documented fail-toward-silence behaviour, measured
for the first time rather than asserted. The corpus rate is over **font
observations, not documents**, and is not comparable to Phase 1's 36.5%/48.4%.

Three places to look once real labels exist:

1. **`LEGACY_ASCII` recall 0.348** — 12 of 23 Kruti-Dev-family fonts were
   called `NO_EVIDENCE`. If this survives, it is the largest known hole in the
   instrument, and it sits precisely on the family the README calls out as
   invisible to a detector built for the Marathi case.
2. **`LEGACY_SYMBOL` recall 0.000** on 8 observations, while 3 ASCII-remap
   fonts *were* labelled `LEGACY_SYMBOL` by the detector. That pattern reads
   as a mis-scoped rule rather than an insensitive one.
3. **4 false positives, all `CMAP_INVALID` on text that reads correctly.**
   Small, but the only place the instrument over-fires.

### 11.4 The label classes are coarser than the mechanisms

Reading 434 excerpts made one thing plain that the schema does not capture:
three of the labels each cover several distinct mechanisms.

- **`CMAP_INVALID`** spans at least four: i-matra stored in visual order
  (`नािशक` for `नाशिक`); systematic consonant substitution (`वलबाग` for
  `विभाग`, `आयोग्म` for `आरोग्य`); Latin or IPA-extension characters standing
  in for conjuncts (`नाTशक`, `ना ͧ शक`); and Devanagari-Extended codepoints
  doing the same (`ᳲ पपरी`).
- **`LEGACY_8BIT`** spans at least five byte mappings — the Nashik
  `xÉÉÊ¶ÉEò` family, APS `‚ã. ‰ãŠ.`, `­ÖÖ×¿Ö eú`, the `=`-separated
  `îLXâX=`, and the Akruti hybrid `cne[e De@keÌì` of §8.3.
- **`LEGACY_ASCII`** spans Kruti Dev (`Jh HkSjo`), transliteration-style
  (`kaoToSana naaoiTsa`), and ISM (`qnnar-qMMdS>`).

Evaluation will report each as one class and will therefore average over
mechanisms that may behave very differently. Whether to split them is a
decision for after real labelling — splitting now would fragment classes that
are already thin — but the write-up should not describe any of the three as a
single phenomenon.

### 11.5 The corpus is more English than the Phase 1 framing suggests

`CORRECT` was the largest label at 142 of 434, most of it English tender and
consultancy documents (Pune Metro, the Lucknow sanitation plan, Patna audit
reports). This is a stratified draw, so it says nothing directly about corpus
proportions — but it is a reminder that the bodies publishing least in Indic
scripts contribute a large share of *fonts*, and per-font rates will read
differently from per-document ones.

### 11.6 The sweep, and what it says about each signal

The threshold sweep is the report §6 promised and the reason the schema stores
every signal for every font. Running it turned up a flaw in the tool first:
`--sweep` scored each signal against *all* corrupt labels, which caps a
signal's recall at its target class's share of them. `mojibake_ratio` cannot
detect a Kruti Dev font at any threshold, so scored that way a well-tuned
threshold looked broken. It now scores against the class each signal targets
(`SIGNAL_TARGET` in `evaluate.py`).

Scored correctly, at the shipped thresholds:

| signal | target | shipped | precision | recall |
|---|---|---|---|---|
| `mojibake_ratio` | `LEGACY_8BIT` | 0.15 | 0.974 | 0.949 |
| `invalid_rate_per_1k` | `CMAP_INVALID` | 2.0 | 0.943 | 0.953 |
| `ascii_k_ratio` | `LEGACY_ASCII` | 0.05 | 0.900 | 0.391 |
| `symbol_per_1k` | `LEGACY_SYMBOL` | 10.0 | 0.000 | 0.000 |

The first two are well placed. `invalid_rate_per_1k` is flat from 0.5 to 2.0
and decays slowly above it, so 2.0 sits comfortably inside a plateau rather
than on an edge — which is what a pre-registered threshold should look like
when it turns out to be right.

**`LEGACY_ASCII` is a coverage problem, not a tuning problem.** This is the
correction to §11.3, which read the 0.348 recall as the instrument's largest
hole and implied a threshold fix. Of the 15 missed observations:

- **12 have `ascii_k_ratio` at or near 0.000** with 800–1,500 ASCII letters
  sampled. They are not Kruti Dev. They are ISM (`qnnar-qMMdS> _hmZJanm{bH$m`),
  transliteration-style (`kaoToSana naaoiTsa mahanagarpailaka`), and a third
  family (`qrqTI, Trrq{`), whose mappings send nothing in particular to `k`.
  The `k` signal works because Kruti Dev maps `k` onto ा, the commonest
  character in written Hindi — a property of *one* encoding. **No threshold on
  this signal can ever catch the others.** Lowering it trades precision away
  for no recall: the sweep is flat at ~0.39–0.48 recall from 0.0125 upward.
- **1 is a near-miss on the sample-size gate.** obs 1974 has `ascii_k` 0.184,
  far above the threshold, and 196 ASCII letters against the 200-letter floor.
  It was missed by four letters. The floor was added to kill a Myriad Pro
  signature-block false positive and it still earns its place, but it has now
  cost one true detection, which is worth knowing when it is next revisited.
- **4 were caught as `LEGACY_SYMBOL` instead.** They are not silent failures —
  the binary "is this wrong" call was right and only the mechanism was wrong.

So the honest statement is: the ASCII-remap detector covers one family well
and three families not at all. Fixing it means a new signal per family, or a
family-agnostic one, not a knob.

**`symbol_per_1k` does not work at any threshold.** Against the 9 observations
labelled `LEGACY_SYMBOL` it peaks at F1 0.125 around 2.5–4.9 and is 0.000 at
the shipped 10.0. Phase 1 validated this rule at precision 1.000 against fonts
identifiable *by name*; against labels it does not survive. Two readings fit —
the rule is mis-scoped, or `LEGACY_SYMBOL` as defined in §3.1 does not carve
the corpus at a joint — and 9 observations cannot separate them. It should be
the first thing a real labelling pass re-examines.

All of the above rests on single-pass labels (§5.5) and is a lead, not a result.
