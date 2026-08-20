# Phase 5 — write-up and release

Started 2026-08-20. **Four decisions are open and are the author's to make**;
they are recorded in §4 rather than guessed at, because each one shapes a
public artifact.

What is settled is the groundwork in §1–§3, which is measurement rather than
choice.

---

## 1. What already exists

`release/` was generated 2026-08-12, at the end of Phase 1, and has not been
touched since. It predates Phases 2, 3 and 4 entirely:

| file | what | status |
|---|---|---|
| `manifest.csv` | 1,602 rows, Phase 1 audit columns | stale |
| `summary.json` | Phase 1 headline figures | stale |
| `rebuild_corpus.py` | re-fetch by URL, verify SHA-256 | still correct |
| `LICENSING.md` | source-by-source redistribution terms | still correct |

The corpus itself is not redistributable — sources have incompatible licences
and at least one forbids it outright. What can be released is the manifest plus
a rebuild script that verifies against checksums, which makes a reproduction
rather than an approximation. That reasoning is unchanged.

## 2. The released CSV is clean — verified, not assumed

The export carries no text columns: checksums, URLs, issuing body, document
type, filename, size, timestamps, and numeric measurements. No excerpt, no page
text.

Three things were checked rather than trusted:

- **100 filenames and 100 URLs match a date pattern.** All are publication
  dates (`FINAL_APPROVED_PRODUCT_LIST_of_civil_dtd-10-08-2026.pdf`), not birth
  dates.
- **7 filenames matched a personal-honorific pattern.** All false positives:
  the pattern caught `-mr.pdf`, which is the Marathi language suffix.
- **`producer` and `creator`** hold software identifiers — `Microsoft® Office
  Word 2007`, `HP Scan`, `Adobe PageMaker 6.52` — not author names.

So the existing release artifact is safe to publish. Its problem is that it is
three phases out of date, not that it leaks anything.

## 3. §9.7 re-measured: the exposure grew ~30× and nobody checked

`phase0-schema.md` §9.7 recorded, in Phase 0: *"39 excerpts carry a DOB pattern
and 60 a caste category, across 11 documents."* That was true when written.
Phases 2, 3 and 4 each added tables holding extracted document text, and none
of them re-ran the measurement:

| table | added in | rows | characters | DOB matches | caste matches |
|---|---|---|---|---|---|
| `excerpt` | Phase 0/2 | 12,004 | 1,928,045 | 702 | 111 |
| `extraction` | Phase 3 | 11,824 | 5,041,406 | 1,759 | 216 |
| `page_text` | Phase 4 | 4,310 | 3,537,429 | 559 | 33 |
| `conversion` | Phase 4 | 1,310 | 598,330 | 396 | 2 |

**~11.1M characters of extracted government document text, with 3,416 date-of-
birth pattern matches and 362 caste-category matches.**

Two things follow, and neither is a decision:

- §9.7's instruction — *"treat `manifest.sqlite` as a file containing personal
  data: do not publish it, and keep it out of anything shared"* — now covers
  roughly thirty times what it did when written. It was already the right rule;
  it is now a much more load-bearing one.
- **A working converter makes this worse, by design.** Phase 4's whole purpose
  is turning text that was practically unreadable into clean, searchable,
  indexable Devanagari. `conversion.text_after` already holds 396 DOB matches in
  converted form. That is the phase succeeding, and it is also why §9.7 can no
  longer be deferred.

The three §9.7 decisions remain unmade: whether excerpts may ever be released,
whether to redact before annotation, and what the write-up says about personal
data collected incidentally from public portals under India's DPDP Act 2023.

## 4. The four open decisions

Recorded, not chosen.

**4.1 The public headline.** `README.md` leads with Phase 1's 36.5% macro /
48.4% pooled. Phase 3's script concordance put the corrected figure at 45.4% /
56.7% and established Phase 1's as a floor; it was decided then that
`phase1-results.md` stays traceable to its own rows. What the *front page* says
is a separate question. The corrected figure rests on a single OCR engine and a
page-1-only comparison against a document-level verdict, both stated
limitations.

**4.2 What ships.** At minimum a refreshed `manifest.csv` and `summary.json`,
or the released numbers stay three phases out of date. Candidates beyond that:
the five Phase 4 mapping tables (novel, and character mappings only — no
document text), and the 6,572 font observations with their signals (would let
others re-run threshold sweeps, and needs a column-by-column check first).

**4.3 §9.7.** A release forces it. The minimum is a stated position in the
write-up. An automated refusal in `export_manifest.py` would make it enforced
rather than remembered — the same reasoning as Phase 3's loss gate. Whether
outside legal advice is wanted before publishing is the author's call; nothing
in this repo constitutes legal advice, and "already public" does not obviously
settle DPDP 2023.

**4.4 The form of the write-up.** The five results documents already hold the
findings. A single consolidated report reading problem → method → results →
limitations would give a reader one door in, with the phase documents as the
detailed record behind it. The alternative is polishing the repo as the
deliverable and writing no new document.

## 5. What is true regardless

- The corpus is not redistributed; the manifest and rebuild script are.
- `manifest.sqlite` is never published.
- No released artifact carries document text.
- Phase 1 results stay traceable to the rows that produced them; corrections
  are reported additively, the way Phases 0, 3 and 4 already do.
