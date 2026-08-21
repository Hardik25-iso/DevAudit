# Reverse-encoding tables

688 rules across 5 legacy Devanagari encoding families,
derived by aligning extracted text against OCR of the same rendered page.

**These are partial and they are not accurate enough to trust unattended.** On
the best-covered family, held-out median agreement with OCR of the same page is
0.37 — roughly a third of characters. One family produces well-formed
Devanagari that is not the right Devanagari. Read `phase4-results.md` before
using them for anything.

Families are identified by **output signature, not font name**. Font names in
this corpus are useless for the purpose: 546 distinct names cover 5,438
convicted observations and the commonest are `F1`-`F8` and `Calibri`.

| column | meaning |
|---|---|
| `family_id` | encoding family, clustered by character-frequency signature |
| `source` | the byte sequence as extracted |
| `target` | the Devanagari it stands for |
| `origin` | `manual` = hand-authored seed, `derived` = learned by alignment |
| `n_attested` | occurrences supporting the rule |
| `n_documents` | distinct documents supporting it |
| `confidence` | share of alignments choosing this target |

Applying a table needs two reordering passes as well as substitution: `ि` moves
forward past its consonant cluster, and repha `र्` moves backward past the
cluster it sits on. `convert.py` implements both.

Contains no document text.
