# Phase 1 Recon — Audit Tool Findings and Source Survey

Reconnaissance pass before any collection code is written. Two questions:
does the audit instrument work, and are enough sources reachable?

---

## Part 1 — The audit instrument

The Phase 1 triage tool (`font_audit.py`) was reviewed before use. Three
defects were found, all of which bias the go/no-go statistic **downward** —
toward a false decision to abandon the project.

### 1.1 Missing dependencies

The tool shells out to `pdfinfo`, `pdffonts` and `pdftotext` (poppler-utils).
On the development machine only `pdftotext` was present. `pdffonts` and
`pdfinfo` carry the font logic, which is the core of the tool.

### 1.2 Silent encoding corruption

```python
subprocess.run(cmd, capture_output=True, text=True, timeout=120)
```

`text=True` decodes using the locale encoding. On Windows this is `cp1252`;
`pdftotext` emits UTF-8. Devanagari therefore arrives as mojibake, and every
downstream counter — `dev_chars`, `dev_digits`, `detached_matras` — is silently
wrong.

Because UTF-8 Devanagari decoded as cp1252 produces Latin garbage, the
Devanagari regexes match nothing. The corruption counters read **zero**, and
the tool reports a clean corpus.

> A tool for detecting silent encoding corruption, silently corrupting
> encoding. It raises no error — it reports a low corruption rate and
> recommends abandoning the project.

### 1.3 The verdict cascade has a directional false negative

`fonts_no_unicode` is computed and written to CSV but never used in the
verdict. The cascade is:

```
scan -> legacy font-name match -> detached matras > 5 -> devanagari -> "Latin/clean"
```

An **unknown** legacy font — one absent from `LEGACY_PATTERNS` — has no
ToUnicode CMap, extracts as ASCII gibberish, yields `dev_chars == 0`, matches
no pattern, and lands in `"Latin/clean"`.

The failure is directional: it suppresses the measured corruption rate on
exactly the fonts whose discovery is the research contribution.

**"No ToUnicode" alone is not the signal.** Verified on a real tender PDF:

```
xref 267  Arial            enc=WinAnsiEncoding  ToUnicode=null   <- extracts fine
xref 273  Times New Roman  enc=Identity-H       ToUnicode=xref
```

Standard Latin fonts routinely lack ToUnicode and extract correctly. The real
signal is a conjunction:

> embedded/subset font **and** no ToUnicode **and** non-standard encoding
> **and** name not in a known-good list -> `UNCLASSIFIED`

### 1.4 Resolution — rewrite on PyMuPDF

PyMuPDF replaces all three poppler binaries in-process, and makes the encoding
bug structurally impossible since `get_text()` returns an already-decoded
`str`.

| poppler | PyMuPDF |
|---|---|
| `pdfinfo` | `doc.page_count`, `doc.metadata` |
| `pdffonts` (name, encoding) | `doc.get_page_fonts(p)` |
| `pdffonts` `uni` column | `doc.xref_get_key(xref, "ToUnicode")` |
| `pdftotext` | `page.get_text()` |

`xref_get_key` returns `('null','null')` when absent and `('xref', 'N 0 R')`
when present — a direct equivalent of the `uni` column.

### 1.5 Five buckets, not four

```
SCAN | LEGACY | SUSPECT | UNCLASSIFIED | CLEAN
```

Mutually exclusive so percentages sum. Raw per-file signals are stored in
SQLite so buckets can be recomputed without re-downloading.

`UNCLASSIFIED` is a first-class outcome, not an error state. Its size is itself
a finding — it measures how incomplete the legacy-font list is.

Two smaller corrections:

- `is_scan` was decided on zero-fonts *before* text extraction. A PDF with
  fonts but no extractable characters is also effectively a scan. Should be
  `n_fonts == 0 or chars < threshold`.
- The summary omitted **clean Unicode %**, which the Phase 1 definition of done
  requires.

---

## Part 2 — A second corruption mechanism

Probing a 77-page municipal budget presentation turned up corruption that the
font-name approach **cannot detect at all**.

| Check | Count | Validity |
|---|---|---|
| Word-initial `ि` (U+093F) | 98 | impossible in Devanagari |
| `ि` directly after another matra | 334 | impossible |
| Detached matras (original regex) | 29 | — |

A vowel sign cannot begin a word, and two vowel signs cannot be adjacent. 432
structural violations is hard evidence of corruption.

Yet the document's fonts are clean by every font-level test:

```
17 fonts. Devanagari set in Mangal + Aparajita — standard Unicode fonts.
Every Devanagari font: enc=Identity-H, ToUnicode present.
No legacy font name anywhere -> LEGACY_PATTERNS matches nothing.
producer = Neevia PDFcompress v4.1
```

> A PDF post-processor mangled a document that was Unicode-clean when authored.
> Font-name matching cannot see this. Neither can "no ToUnicode" — the CMaps
> are all present, they are simply wrong.

**Caveat: n = 1.** This is a hypothesis to measure in Phase 1, not a result.

### Consequence — detect by output validity, not font name

The detached-matra regex under-counts badly here: 29 hits against 432 real
violations. Replace it with structural Devanagari validity:

```
word-initial matra          (?<![ऀ-ॿ])[ा-ौ]
two adjacent matras         [ा-ौ][ा-ौ]
virama followed by matra    ्[ा-ौ]
detached matra              (original rule, retained)
```

Rate-normalise per 1000 Devanagari characters so long documents are not
penalised.

This also answers "flag fonts you cannot classify": classification by **output
validity** generalises where classification by **font name** cannot, and it
catches both corruption mechanisms with one test.

---

## Part 3 — Source reachability survey

Checked `robots.txt`, crawl-delay and whether PDFs are reachable by direct link
without form navigation.

| Issuing body | robots.txt | Crawl-Delay | Direct PDFs on homepage | Notes |
|---|---|---|---|---|
| Pune Municipal Corporation (`pmc.gov.in`) | `Disallow:` — allow all | none stated | 1 | **Listings are JS-rendered.** See below. |
| Pimpri-Chinchwad MC (`pcmcindia.gov.in`) | Googlebot group only, no `*` group | none stated | 8 | Permissive by default |
| MHADA (`mhada.gov.in`) | Drupal default; `/core/`, `/admin/`, `/search/` disallowed | none stated | 37 | Server-rendered, richest source |
| Pune Metro (`punemetrorail.org`) | allow all except `/cgi-bin/` | none stated | 12 | Sitemap available |
| Nashik MC (`nmc.gov.in`) | 404 — no restrictions | none stated | 11 | Identity confirmed via page title |
| Nagpur MC (`nmcnagpur.gov.in`) | allow all except adult-content patterns | none stated | 9 | |
| OpenCity (`data.opencity.in`) | allows; `/api/`, `/revision/` disallowed | **10s** | — | Aggregator, not an issuing body |
| data.gov.in | — | — | — | **HTTP 403** to automated fetch; needs API key |

### Gate result: PASSED

**Six issuing bodies are reachable** with permissive robots.txt and PDFs
linked directly, with no form navigation required. The corpus design in the
brief is viable.

### Qualifications

1. **Homepage PDF counts are a reachability signal, not a corpus size.** Deeper
   per-site enumeration is still required to confirm 200–400 documents.

2. **PMC needs a different collection path.** Every PMC page returns the same
   single footer PDF, because document listings are client-side rendered. The
   files live on a Drupal backend at `webadmin.pmc.gov.in`, which exposes
   **JSON:API** (`/jsonapi`, HTTP 200). Enumerate through the API rather than
   scraping rendered HTML. This matters — PMC is the source of the motivating
   example.

3. **OpenCity is an aggregator.** Provenance must record the *originating
   issuing body*, not OpenCity, or the department breakdown becomes
   meaningless and the 6-body spread requirement is silently violated.

4. **`data.gov.in` terms must be logged at registration**, not just the API
   key. If redistribution is prohibited, documents sourced through it cannot
   enter a released dataset. Establish this before they are in the corpus.

### Crawl policy

Honour each site's stated `Crawl-Delay` where present (OpenCity: 10s), with a
2s floor elsewhere, plus exponential backoff. Descriptive User-Agent
identifying the work as academic research.

---

## Next

1. Rewrite the audit on PyMuPDF with the five buckets and validity-based
   `SUSPECT` detection.
2. Calibrate on a hand-picked set before any scraper exists — if the heuristics
   are wrong, that costs an afternoon on 20 files and a week on 400.
3. Build the scraper and `manifest.sqlite` provenance layer last.

### Calibration set

| File | Must land in |
|---|---|
| Known Shree-Dev municipal budget | `LEGACY` |
| Same file, `shree-dev` removed from `LEGACY_PATTERNS` | `UNCLASSIFIED` |
| 77-page budget presentation (Part 2) | `SUSPECT` |
| Clean Unicode Devanagari | `CLEAN` |
| Real scan | `SCAN` |
| Latin-only | `CLEAN` |

The second row is the most important assertion in the suite. `UNCLASSIFIED`
exists solely to fix the bias in §1.3; shipping it untested leaves the fix
unverified. Monkey-patching the pattern list reproduces the exact
false-negative scenario — a real legacy font absent from the list — as a
held-out test on a file already understood.

Regression assertions must check the **specific** corruptions (`जानवे ारी`,
`बाज ू`) rather than `dev_chars > 0`. The weak assertion passes even when the
vowel reordering is absent, which is the thing being measured.
