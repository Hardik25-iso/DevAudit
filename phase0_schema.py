#!/usr/bin/env python3
"""
phase0_schema.py — create the Phase 0 extraction and annotation tables.

Phase 1 measures per font and stores per document. The audit table squashes
every font in a document into four delimited text columns, so the grain that
made the instrument work is thrown away at the moment of writing. This adds
the missing grain and the tables that hang off it. It reads no PDFs, touches
no existing table, and is safe to re-run.

    python phase0_schema.py                 # create in data/manifest.sqlite
    python phase0_schema.py --db other.db   # or somewhere else

Why the Phase 1 tables are left alone: the 36.5% / 48.4% figure is traceable
to those rows exactly as they stand. Ground truth is additive evidence beside
the audit, never an edit to it.
"""

import argparse
import sqlite3

import config


# The guidelines the labels were applied under. Bump this whenever
# docs/phase0-schema.md changes what a label means, because a label is only
# interpretable against the definition in force when it was given. Stored on
# every annotation so a definition change is visible rather than silent.
GUIDELINE_VERSION = "0.1"


SCHEMA = """
-- ---------------------------------------------------------------------------
-- font_observation — one row per (document, font). The unit of measurement,
-- of annotation, and of detector evaluation, all the same unit.
--
-- Grain: font, not font object. A document embeds the same face several times
-- under separate xrefs; the corruption is a property of the face's output, so
-- the xrefs are counted (n_xrefs) rather than given rows of their own.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS font_observation (
    obs_id          INTEGER PRIMARY KEY,
    sha256          TEXT NOT NULL,        -- -> documents.sha256
    font_name       TEXT NOT NULL,        -- subset prefix stripped
    raw_font_name   TEXT,                 -- as embedded, 'ABCDEF+' intact

    -- what the PDF declares about the font
    n_xrefs         INTEGER,
    embedded        INTEGER,              -- 0/1
    has_tounicode   INTEGER,              -- 0/1
    encoding        TEXT,
    font_type       TEXT,
    first_page      INTEGER,
    n_pages_seen    INTEGER,   -- pages inside the capped text sample
    -- Pages the font is declared on across the WHOLE document. The pair is
    -- the point: a font declared on 24 pages that contributes 24 characters
    -- of sampled text is under-sampled, and the detector's abstention on it
    -- is an artifact of the 8-page cap rather than a fact about the font.
    -- Measured on the first fixture tried, so this is observed, not feared.
    n_pages_declared INTEGER,

    -- how much text this row's numbers were computed from. Without it a
    -- signal cannot be weighted, and the one false positive Phase 1 shipped
    -- was a 154-character signature block.
    sampled_chars   INTEGER,
    dev_chars       INTEGER,
    latin_letters   INTEGER,
    n_tokens        INTEGER,

    -- raw signals, stored for every font whether it was convicted or not.
    -- This is the change that makes a threshold sweep a query. Phase 1 keeps
    -- only the rule that fired, so cleared fonts leave no evidence and no
    -- ROC curve can be drawn without re-reading 1,602 PDFs.
    mojibake_ratio      REAL,
    ascii_k_ratio       REAL,
    ascii_k_eligible    INTEGER,   -- sample-size floor met; a gate, reported
    symbol_per_1k       REAL,
    english_ratio       REAL,

    -- structural signals, taken per font. The document-level check cannot say
    -- WHICH font produced invalid Devanagari, which is why SUSPECT (11.2
    -- points of the finding) has no per-font evidence today.
    invalid_matras          INTEGER,
    invalid_rate_per_1k     REAL,
    word_initial_matras     INTEGER,
    adjacent_matras         INTEGER,
    virama_then_matra       INTEGER,
    detached_matras         INTEGER,
    invalid_matras_nospace  INTEGER,   -- candidate signal, feeds no verdict

    -- what the detector said, kept beside the signals but never shown to an
    -- annotator (see annotation.saw_detector_output)
    detector_label  TEXT,     -- same vocabulary as annotation.label
    detector_reason TEXT,     -- e.g. '8bit(0.76)'

    signals_version TEXT,     -- code version that produced these numbers
    extracted_at    TEXT,
    UNIQUE(sha256, font_name)
);
CREATE INDEX IF NOT EXISTS idx_obs_sha ON font_observation(sha256);
CREATE INDEX IF NOT EXISTS idx_obs_detector ON font_observation(detector_label);

-- ---------------------------------------------------------------------------
-- excerpt — verbatim extracted text, so annotation works with the external
-- drive detached. An annotator cannot label a number; storing the text is
-- what makes the annotation task independent of the corpus.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS excerpt (
    excerpt_id  INTEGER PRIMARY KEY,
    obs_id      INTEGER NOT NULL REFERENCES font_observation(obs_id),
    page        INTEGER,
    -- 'head'      first text this font renders — cheap orientation
    -- 'random'    seeded draw, so the excerpt set is not chosen by the
    --             detector and cannot flatter it
    -- 'violation' window around a structural violation, for CMAP_INVALID;
    --             a class you cannot see without being shown where it broke
    kind        TEXT NOT NULL,
    char_start  INTEGER,          -- offset into this font's sampled text
    text        TEXT NOT NULL,
    UNIQUE(obs_id, kind, page, char_start)
);

-- ---------------------------------------------------------------------------
-- annotation_sample — the sampling frame, recorded before annotating.
--
-- Ground truth will be drawn stratified (the CLEAN stratum matters most: it
-- is the only place recall can be measured, and it is where a detector that
-- fails toward silence hides its misses). Stratified draws mean the labelled
-- proportion is NOT the corpus proportion, so the selection probability has
-- to be stored or no corpus estimate can be recovered from the labels.
-- Sampling has already bitten this project three times.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS annotation_sample (
    sample_id       TEXT NOT NULL,      -- names one frozen draw
    obs_id          INTEGER NOT NULL REFERENCES font_observation(obs_id),
    stratum         TEXT NOT NULL,
    stratum_size    INTEGER NOT NULL,   -- observations in the stratum
    drawn           INTEGER NOT NULL,   -- drawn from it
    selection_prob  REAL NOT NULL,      -- drawn / stratum_size
    seed            INTEGER NOT NULL,
    created_at      TEXT,
    PRIMARY KEY (sample_id, obs_id)
);

-- ---------------------------------------------------------------------------
-- annotation — append-only. One row per (observation, annotator, round).
--
-- Never updated in place: agreement can only be computed from the labels as
-- originally given, and overwriting a first pass with an adjudicated one
-- destroys the evidence that the adjudication was needed.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS annotation (
    annotation_id   INTEGER PRIMARY KEY,
    obs_id          INTEGER NOT NULL REFERENCES font_observation(obs_id),
    sample_id       TEXT,
    annotator       TEXT NOT NULL,      -- 'hardik' | 'llm:<model>' | ...
    round           INTEGER NOT NULL DEFAULT 1,   -- 2 = blind re-annotation
    label           TEXT NOT NULL,
    script          TEXT,               -- deva | latin | mixed | other | none
    confidence      INTEGER,            -- 1 low, 2 medium, 3 high
    -- Blindness is a property of the annotation, not of the protocol, so it
    -- is recorded per row. An unblinded label may not enter an evaluation set.
    saw_detector_output INTEGER NOT NULL DEFAULT 0,
    guideline_version   TEXT NOT NULL,
    note            TEXT,
    seconds_spent   INTEGER,
    annotated_at    TEXT,
    UNIQUE(obs_id, annotator, round)
);
CREATE INDEX IF NOT EXISTS idx_ann_obs ON annotation(obs_id);

-- ---------------------------------------------------------------------------
-- adjudication — the single final label per observation, and why it is final.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adjudication (
    obs_id          INTEGER PRIMARY KEY REFERENCES font_observation(obs_id),
    final_label     TEXT NOT NULL,
    -- 'unanimous'  independent passes agreed
    -- 'adjudicated' they did not; resolved on a third look
    -- 'single'     only one pass exists; usable as ground truth only if the
    --              write-up says so, and never for measuring agreement
    basis           TEXT NOT NULL,
    adjudicator     TEXT,
    note            TEXT,
    guideline_version TEXT NOT NULL,
    decided_at      TEXT
);

-- ---------------------------------------------------------------------------
-- doc_annotation — the few judgements that are genuinely document-level.
--
-- Chiefly: is this text layer digital or OCR? Phase 1 splits on characters
-- per page, which sends an OCR'd scan into whatever bucket its OCR output
-- happens to produce. OCR errors and encoding errors are different problems
-- with different fixes, and the corpus cannot separate them today.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS doc_annotation (
    sha256          TEXT NOT NULL,      -- -> documents.sha256
    annotator       TEXT NOT NULL,
    round           INTEGER NOT NULL DEFAULT 1,
    text_layer      TEXT NOT NULL,      -- NONE | DIGITAL | OCR | MIXED
    primary_script  TEXT,               -- deva | latin | mixed | other
    doc_usable      TEXT,               -- YES | PARTIAL | NO
    guideline_version TEXT NOT NULL,
    note            TEXT,
    annotated_at    TEXT,
    PRIMARY KEY (sha256, annotator, round)
);

-- ---------------------------------------------------------------------------
-- ground_truth — the evaluation set: final label beside the raw signals.
-- A view, not a table, so it can never drift from the rows underneath it.
-- ---------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS ground_truth AS
SELECT o.obs_id, o.sha256, o.font_name,
       adj.final_label, adj.basis,
       o.detector_label, o.detector_reason,
       o.sampled_chars, o.dev_chars, o.latin_letters,
       o.mojibake_ratio, o.ascii_k_ratio, o.ascii_k_eligible,
       o.symbol_per_1k, o.english_ratio,
       o.invalid_matras, o.invalid_rate_per_1k, o.invalid_matras_nospace,
       s.sample_id, s.stratum, s.selection_prob
FROM font_observation o
JOIN adjudication adj ON adj.obs_id = o.obs_id
LEFT JOIN annotation_sample s ON s.obs_id = o.obs_id;
"""


# Columns added after the table has been created somewhere. CREATE TABLE IF
# NOT EXISTS will not add them, so they are applied explicitly — the same
# pattern audit_corpus.py already uses, for the same reason.
MIGRATIONS = [
    ("font_observation", "n_pages_declared", "INTEGER"),
    # Whether the annotator was shown the font name. Same reasoning as
    # saw_detector_output: blindness is a property of the individual
    # judgement, and a name like DVBW-TTSurekh is as much of a prior as a
    # verdict is. Rows where this is 1 cannot support any claim about how the
    # detector performs on fonts whose names say nothing.
    ("annotation", "saw_font_name", "INTEGER"),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--db", default=str(config.MANIFEST_DB))
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    before = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    conn.executescript(SCHEMA)
    for table, col, coltype in MIGRATIONS:
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
            print(f"  migrated: {table}.{col}")
    conn.commit()
    after = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    conn.close()

    print(f"{args.db}")
    print(f"  created: {', '.join(sorted(after - before)) or '(nothing new)'}")
    print(f"  existing, untouched: {', '.join(sorted(before))}")


if __name__ == "__main__":
    main()
