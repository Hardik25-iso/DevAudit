#!/usr/bin/env python3
"""
phase3_schema.py — create the Phase 3 extractor-benchmark tables.

Phase 2 stores one row per (document, font). Phase 3 cannot use that grain:
three attempts to carry it forward were measured and all three failed, because
these documents mix fonts within a single page and because the extractors
produce *different* garbage from the same bytes, which destroys any alignment
key. See docs/phase3-design.md §2.1 and §2.2.

So the grain here is (document, page, extractor arm). It reads no PDFs, touches
no existing table, and is safe to re-run.

    python phase3_schema.py                 # create in data/manifest.sqlite
    python phase3_schema.py --db other.db

Additive only, the same rule Phase 0 followed: the 36.5% / 48.4% figure stays
traceable to the rows that produced it, and nothing here writes to them.
"""

import argparse
import sqlite3

import config


# Bump when a measurement in extractors.py changes meaning. Stored on every
# extraction row for the same reason annotation stores guideline_version: a
# number is only interpretable against the code that produced it.
SIGNALS_VERSION = "p3-0.1"


SCHEMA = """
-- ---------------------------------------------------------------------------
-- extraction_run — one row per benchmark pass.
--
-- The sampling frame is recorded here rather than reconstructed later.
-- Sampling has produced a wrong answer three times in this project; a run that
-- cannot say which documents it drew, under which seed and which per-body cap,
-- cannot be checked afterwards for the same mistake.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS extraction_run (
    run_id          TEXT PRIMARY KEY,      -- e.g. 'tier1-20260819'
    tier            TEXT NOT NULL,         -- labelled | coverage | ocr
    arms            TEXT NOT NULL,         -- comma-separated, as run
    seed            INTEGER,
    per_body_cap    INTEGER,
    max_pages       INTEGER NOT NULL,
    n_documents     INTEGER,
    signals_version TEXT NOT NULL,
    note            TEXT,
    started_at      TEXT,
    finished_at     TEXT
);

-- ---------------------------------------------------------------------------
-- extraction_sample — which documents a run drew, and with what probability.
--
-- Separate from extraction_run because a run over a stratified draw needs the
-- per-document selection probability to reweight a sample rate into a corpus
-- rate. annotation_sample exists for exactly this reason and this is its twin.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS extraction_sample (
    run_id          TEXT NOT NULL REFERENCES extraction_run(run_id),
    sha256          TEXT NOT NULL,          -- -> documents.sha256
    issuing_body    TEXT,                   -- denormalised: every report macro-
                                            -- averages by body, so it is joined
                                            -- on every single query otherwise
    stratum         TEXT,
    stratum_size    INTEGER,
    drawn           INTEGER,
    selection_prob  REAL,
    PRIMARY KEY (run_id, sha256)
);

-- ---------------------------------------------------------------------------
-- extraction — one row per (run, document, page, arm). The unit of everything
-- in this phase.
--
-- OCR is stored as an arm here rather than in a table of its own. That is the
-- design's load-bearing simplification: script concordance (design §3.2) then
-- becomes a self-join of this table on (sha256, page) between arm='ocr' and a
-- text arm, so the reference is a query rather than a second pipeline. It also
-- means OCR is measured by the same battery as everything else, and cannot
-- quietly acquire a privileged status it has not earned.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS extraction (
    extraction_id   INTEGER PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES extraction_run(run_id),
    sha256          TEXT NOT NULL,
    page            INTEGER NOT NULL,       -- 1-based
    arm             TEXT NOT NULL,          -- pdftotext|pymupdf|pdfplumber|pypdf|ocr

    -- Did the arm run at all? An arm that throws is a result, not a gap: a
    -- library that crashes on 5% of government PDFs has told you something.
    -- NULL error means it completed.
    error           TEXT,
    elapsed_ms      INTEGER,

    -- --- loss (design §3.1) ------------------------------------------------
    -- Measured first and gating everything below, because every metric under
    -- this heading is a rate and a rate flatters an empty numerator. pdftotext
    -- scores 0.001 mojibake on documents where it emits 43.7% U+FFFD.
    n_chars         INTEGER,    -- non-whitespace
    n_chars_raw     INTEGER,    -- including whitespace; the gap is metric §3.4
    replacement_chars   INTEGER,    -- U+FFFD
    control_chars       INTEGER,    -- C0 excluding tab/LF/FF
    replacement_ratio   REAL,
    control_ratio       REAL,

    -- --- the Phase 1 battery, unchanged, applied per page ------------------
    -- Deliberately identical to font_audit.measure_font_text() output. If
    -- these were redefined here, Phase 3 could not be compared to Phase 1 at
    -- all, and the comparison is most of the point.
    dev_chars           INTEGER,
    dev_share           REAL,       -- dev_chars / n_chars; drives §3.2
    latin_letters       INTEGER,
    n_tokens            INTEGER,
    mojibake_ratio      REAL,
    ascii_k_ratio       REAL,
    symbol_per_1k       REAL,
    english_ratio       REAL,
    invalid_matras          INTEGER,
    invalid_rate_per_1k     REAL,
    invalid_matras_nospace  INTEGER,

    -- --- the text itself ---------------------------------------------------
    -- text_hash is over whitespace-stripped text, so two arms that agree on
    -- characters but differ on spacing hash the same. That is what makes
    -- "did these arms produce identical output?" a GROUP BY rather than a
    -- pairwise string comparison over 15,000 rows.
    text_hash       TEXT,
    text_sample     TEXT,       -- first ~600 chars, so §3.4 and eyeballing
                                -- work with the external drive detached

    signals_version TEXT,
    extracted_at    TEXT,
    UNIQUE(run_id, sha256, page, arm)
);
CREATE INDEX IF NOT EXISTS idx_extr_doc  ON extraction(sha256, page);
CREATE INDEX IF NOT EXISTS idx_extr_arm  ON extraction(run_id, arm);
CREATE INDEX IF NOT EXISTS idx_extr_hash ON extraction(text_hash);

-- ---------------------------------------------------------------------------
-- page_pair — script concordance, materialised (design §3.2).
--
-- A view would be honest but unusably slow: the self-join is over ~15k rows
-- per arm pair and every report needs it. It is rebuilt from `extraction` by
-- evaluate_extractors.py rather than maintained incrementally, so it can drift
-- only by being stale, never by being wrong -- and `built_from_run` says which
-- run it was built from so staleness is detectable.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS page_pair (
    run_id          TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    page            INTEGER NOT NULL,
    arm             TEXT NOT NULL,
    dev_share_arm   REAL,
    dev_share_ocr   REAL,
    -- match | script_mismatch | script_excess | no_reference | loss
    concordance     TEXT,
    built_from_run  TEXT,
    PRIMARY KEY (run_id, sha256, page, arm)
);
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
    print(f"  signals_version: {SIGNALS_VERSION}")


if __name__ == "__main__":
    main()
