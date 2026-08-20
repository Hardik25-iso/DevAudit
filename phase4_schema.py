#!/usr/bin/env python3
"""
phase4_schema.py — create the Phase 4 converter tables.

Phase 3 established that no extractor recovers legacy-encoded text, so the
recovery problem has to be solved by reversing the encoding. These are the
tables that hold the reverse mappings, the family assignments they are keyed
on, and the converted output.

    python phase4_schema.py                 # create in data/manifest.sqlite
    python phase4_schema.py --db other.db

Additive only, the rule every phase since Phase 0 has followed: nothing here
writes to a Phase 1, 2 or 3 table, so every earlier figure stays traceable to
the rows that produced it.
"""

import argparse
import sqlite3

import config

# Bump when the derivation algorithm or the applier changes meaning. A mapping
# table is only interpretable against the code that produced it, the same
# reasoning as annotation.guideline_version.
MAPPING_VERSION = "p4-0.1"


SCHEMA = """
-- ---------------------------------------------------------------------------
-- font_family — an encoding family, identified by output signature.
--
-- NOT by font name. 546 distinct names cover 5,438 convicted observations and
-- the commonest are F1-F8 (subset IDs), Calibri and ArialMT. Phase 1 learned
-- this lesson for detection; it applies unchanged to conversion, because the
-- thing a table must be keyed on is the encoding, and the name does not name it.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS font_family (
    family_id       TEXT PRIMARY KEY,       -- e.g. 'fam-01-dhruv'
    label           TEXT,                   -- the detector class it is made of
    n_observations  INTEGER,
    n_documents     INTEGER,
    -- The character-frequency centroid the cluster was formed around, JSON.
    -- Stored so membership for a NEW observation is a comparison rather than a
    -- re-clustering, and so a later run can tell whether the family drifted.
    centroid        TEXT,
    -- Representative font names, comma separated. Diagnostic only -- never a
    -- membership test, for the reason in the table comment above.
    example_fonts   TEXT,
    example_text    TEXT,
    threshold       REAL,                   -- cosine floor used to form it
    created_at      TEXT
);

-- ---------------------------------------------------------------------------
-- family_member — which observations belong to which family.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS family_member (
    family_id   TEXT NOT NULL REFERENCES font_family(family_id),
    obs_id      INTEGER NOT NULL REFERENCES font_observation(obs_id),
    similarity  REAL,                       -- cosine to the centroid
    PRIMARY KEY (family_id, obs_id)
);
CREATE INDEX IF NOT EXISTS idx_fammem_obs ON family_member(obs_id);

-- ---------------------------------------------------------------------------
-- mapping_entry — one reverse-mapping rule. The converter IS this table.
--
-- Applied longest-first: multi-character sequences must be tried before single
-- characters or 'E' matches before 'Eo' and everything after it is wrong.
-- `source` is therefore stored with its length available for ordering.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mapping_entry (
    family_id   TEXT NOT NULL REFERENCES font_family(family_id),
    source      TEXT NOT NULL,      -- the garbage sequence, e.g. 'Eò'
    target      TEXT NOT NULL,      -- the Devanagari it means, e.g. 'क'
    -- How the rule was established. 'derived' rules came from the EM aligner
    -- and carry evidence counts; 'manual' rules were authored by hand and are
    -- how the applier was proven correct before the deriver was trusted.
    origin      TEXT NOT NULL,      -- derived | manual
    n_attested  INTEGER,            -- occurrences supporting it
    n_documents INTEGER,            -- DISTINCT documents supporting it; the
                                    -- floor is on this, not on n_attested, so
                                    -- one repetitive document cannot author a
                                    -- rule by itself
    confidence  REAL,
    mapping_version TEXT,
    note        TEXT,
    PRIMARY KEY (family_id, source)
);

-- ---------------------------------------------------------------------------
-- conversion — one row per (document, page, family) converted.
--
-- Stores the measures of design §4 beside the output, so the three-way report
-- (structural validity, OCR agreement, negative control) is a query.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversion (
    conversion_id   INTEGER PRIMARY KEY,
    run_id          TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    page            INTEGER NOT NULL,
    family_id       TEXT,
    -- train | test. Recorded per row because the split must be fixed BEFORE
    -- derivation; a split decided afterwards is not a split.
    split           TEXT,

    n_chars_before  INTEGER,
    n_chars_after   INTEGER,
    -- share of characters the table actually matched. A conversion that
    -- rewrote 3% of the page is not a conversion, and without this it would
    -- score well on every other measure by simply not doing anything.
    coverage        REAL,

    -- --- primary measure: structural validity, independent of OCR ----------
    invalid_rate_before REAL,
    invalid_rate_after  REAL,
    dev_share_after     REAL,

    -- --- secondary: agreement with OCR of the same page --------------------
    -- Corroboration only. The tables were LEARNED from OCR, so this cannot be
    -- the primary measure without scoring the answer against its own source.
    ocr_similarity      REAL,

    text_after      TEXT,           -- first ~600 chars, so review works with
                                    -- the external drive detached
    mapping_version TEXT,
    converted_at    TEXT,
    UNIQUE(run_id, sha256, page, family_id)
);
CREATE INDEX IF NOT EXISTS idx_conv_run ON conversion(run_id);

-- ---------------------------------------------------------------------------
-- page_text — full page text for deriving mapping tables.
--
-- extraction.text_sample holds 600 characters, which is a field designed for
-- eyeballing a page with the drive detached. Training a ~300-rule substitution
-- cipher on it means learning from ~600KB of parallel text across the corpus,
-- and the held-out evaluation showed what that buys: coverage 0.684 and
-- structural validity 0.000.
--
-- This table holds the whole page, for the two arms training needs. Separate
-- from `extraction` because the grain is the same but the purpose is not: that
-- table is the Phase 3 benchmark record and its rows should not be rewritten
-- to serve Phase 4.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS page_text (
    sha256      TEXT NOT NULL,
    page        INTEGER NOT NULL,
    arm         TEXT NOT NULL,          -- pymupdf | ocr
    text        TEXT,
    n_chars     INTEGER,                -- non-whitespace
    dev_share   REAL,
    error       TEXT,
    extracted_at TEXT,
    PRIMARY KEY (sha256, page, arm)
);
CREATE INDEX IF NOT EXISTS idx_ptext_arm ON page_text(arm);
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--db", default=str(config.MANIFEST_DB))
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    before = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    conn.executescript(SCHEMA)
    conn.commit()
    after = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    conn.close()

    created = sorted(after - before)
    print(f"db: {args.db}")
    print(f"  created: {', '.join(created) if created else 'nothing (already present)'}")
    print(f"  mapping_version: {MAPPING_VERSION}")


if __name__ == "__main__":
    main()
