# Source Licensing and Redistribution

Written during Phase 1, before collection reached scale, because the brief
requires the reasoning to exist *before* publication rather than during
write-up. This is a record of what each source states, and what follows from
it — not legal advice.

## Summary

**The sources do not share a licence.** At least one explicitly restricts
redistribution while another explicitly permits it. A corpus that mixes them
cannot be republished wholesale under any single licence without
misrepresenting some of its contents.

**Consequence: we release the manifest, not the documents.**

## What each source states

| Source | Stated position | Redistribution |
|---|---|---|
| Nashik Municipal Corporation | Website Policy: content owned by NMC; attribution required as "Nashik Municipal Corporation" | **Restricted.** "No part of this website shall be reproduced or distributed for commercial purposes, including on other websites, without obtaining prior written permission." Official logos and emblems prohibited without approval. |
| data.gov.in | [GODL-India](https://www.data.gov.in/Godl) | **Permitted**, worldwide, royalty-free, commercial and non-commercial, with attribution and no implication of endorsement. |
| MHADA, PCMC, Nagpur MC, Pune Metro, PMC | No copyright or terms page discoverable from the homepage at time of survey | **Unknown.** Absence of a stated permission is not permission. |

The "unknown" row is the important one. Four of six bodies publish no
machine-discoverable terms, so their position has to be treated as unresolved
rather than permissive.

## Why this rules out uploading the PDFs

Considered and rejected: mirroring the corpus to Kaggle (or any public host)
for convenience and machine-independence.

1. **It is republication.** Kaggle requires the uploader to assert a licence
   and a right to distribute. We cannot make that assertion for a mixed corpus
   where one source restricts distribution "on other websites" and four are
   silent.
2. **Kaggle is a commercial platform.** Whether hosting there constitutes a
   "commercial purpose" under the NMC policy is genuinely ambiguous — and
   ambiguity is the thing that makes a dataset unpublishable later.
3. **It is effectively irreversible.** Public datasets are indexed and cached.
   Deleting the upload does not undo the distribution.
4. **It front-runs a Phase 5 decision** that the project has already committed
   to making on Hugging Face, with the licensing reasoning written down.

## What we release instead

The **manifest**: source URL, SHA-256, size, issuing body, document type,
retrieval timestamp, and every audit measurement, plus the fetch script.

This is the standard resolution for web-derived research corpora, and it is
sound here for a specific reason: the manifest contains *our own
measurements*, which are unambiguously ours to license. The documents remain
with their publishers, and anyone can reconstruct the exact corpus because the
checksums make a re-fetch verifiable.

## The one thing this does not solve

Link rot. Indian government sites re-path and remove PDFs frequently, so a
re-fetch in a year may not reproduce the corpus even with correct URLs.

Mitigation, in order of preference:

1. Keep a **local** archive. Private storage is not distribution, so no
   licensing question arises.
2. Record retrieval timestamps and checksums so a partial reproduction is at
   least *detectable* rather than silent.
3. For the eventual release, consider seeking written permission from the
   bodies whose documents matter most to the results — the restriction is on
   redistribution *without* permission, not redistribution as such.

## Open items

- [ ] Register for a `data.gov.in` API key and record the terms accepted at
      registration, not just the key.
- [ ] Re-check MHADA, PCMC, Nagpur MC, Pune Metro and PMC for terms pages that
      are not linked from the homepage (many are reachable only via sitemap or
      footer rendered client-side).
- [ ] Decide before Phase 5 whether to seek written permission from NMC.

## Sources

- [Government Open Data License - India](https://www.data.gov.in/Godl)
- [Template:GODL-India — Wikipedia](https://en.wikipedia.org/wiki/Template:GODL-India)
- Nashik Municipal Corporation Website Policy —
  <https://nmc.gov.in/home/getfrontpage/237>
