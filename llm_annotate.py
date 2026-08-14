#!/usr/bin/env python3
"""
llm_annotate.py — the second annotation pass, run by a model.

Pass B of the protocol in docs/phase0-schema.md §5.2. Sends each sampled
observation's stored excerpts to Claude with the annotation guidelines, and
writes the answers into `annotation` as a separate annotator. Touches no PDFs.

    python llm_annotate.py --submit     # build and send the batch
    python llm_annotate.py --status     # is it done yet
    python llm_annotate.py --collect    # write results into `annotation`

Why the Batches API rather than a loop of live calls: 434 observations at half
price, and the pass is not latency-sensitive. Why a separate script rather than
a flag on annotate.py: this writes rows nobody typed, and that should be
obvious from the command that produced them.

**This is a second opinion, not a second annotator.** It sees the same
excerpts under the same guidelines and is blind to the font name and the
detector's verdict, but it is not independent of the human pass in the way
inter-annotator agreement normally assumes — both are Claude-adjacent
judgements of the same text. Its disagreements with the human pass are worth
adjudicating; the agreement number it produces is not a reliability figure and
the write-up must not report it as one.
"""

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

import config
from annotate import LABELS, SCRIPTS
from phase0_schema import GUIDELINE_VERSION

MODEL = "claude-opus-5"
ANNOTATOR = f"llm:{MODEL}"
BATCH_ID_FILE = config.REPO_ROOT / "data" / "llm_batch_id.txt"

# The guidelines, condensed from docs/phase0-schema.md §4. Kept in one string so
# it is a stable cache prefix across all 434 requests -- the excerpts are the
# only thing that varies, and they go last.
GUIDELINES = """\
You are labelling the extracted text layer of Indian government PDFs, to build
ground truth for a detector that finds documents whose text extracts without
error and is linguistically wrong.

The cause is legacy non-Unicode Devanagari fonts that map glyphs onto ASCII or
Latin-1 codepoints in visual order, and Unicode fonts with broken ToUnicode
CMaps. The PDF renders correctly on screen; the text underneath is scrambled.

You are shown the text one font produced in one document. Label that font.

LABELS
  CORRECT             right characters in the right order
  LEGACY_8BIT         glyphs on the Latin-1 supplement: "xÉÉÊ¶ÉEò ¨É½þÉxÉMÉ®ú"
  LEGACY_ASCII        Kruti-Dev-style plain-ASCII remap: "i'kq dY;k.k foHkkx"
  LEGACY_SYMBOL       remap using symbols between letters: "A«BC§D"
  CMAP_INVALID        real Devanagari codepoints, structurally impossible --
                      a matra opening a word, two matras adjacent, a matra
                      after a virama: "जानवे ारी", "स्थालनक"
  PARTIAL             some text correct and some corrupt under one font
  NO_LINGUISTIC_TEXT  digits, rules, bullets, a logo -- nothing to judge
  UNDECIDABLE         you cannot tell, or there is too little text

DECISION PROCEDURE
1. Nothing to judge -> NO_LINGUISTIC_TEXT. Under ~200 chars -> UNDECIDABLE.
2. Devanagari present? If it forms real Marathi/Hindi words -> CORRECT. If the
   defects are structural and it does not read -> CMAP_INVALID.
3. No Devanagari, dense in accented Latin-1 (É Ê ¶ ½ þ ®) -> LEGACY_8BIT.
4. Plain ASCII that reads as English, names, or transliteration -> CORRECT.
   Plain ASCII dense in k j ; ' [ ] that does not read -> LEGACY_ASCII.
   Symbols inside words -> LEGACY_SYMBOL. Cannot tell which -> UNDECIDABLE.
5. Both correct and corrupt text under one font -> PARTIAL.

NOT CORRUPTION -- do not label any of these as damage:
  - ligatures (fi fl), ZWJ/ZWNJ in conjuncts, decomposed nukta forms
  - Devanagari digits, Latin digits mixed into Devanagari
  - English headers inside a Marathi document; language mixing is normal here
  - soft hyphens, currency and unit symbols
  - reading-order scrambling: every word correct, the order wrong. This is an
    extraction-order defect, not an encoding one -> CORRECT, note
    "#reading-order"
  - a spurious space detaching a matra ("जमा बाज ू") where closing the space
    yields a real word. The glyph mapping is right and the extractor's word
    boundary is wrong -> CORRECT, note "#spurious-space"

You are deliberately not told the font name, the detector's verdict, or the
issuing body. Judge only the text you are shown.

Abstain rather than guess. UNDECIDABLE is a useful answer; a coerced label is
noise. Set confidence 1 when unsure, 3 when certain. Put brief evidence in the
note -- what you read, or what made it undecidable."""

SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": LABELS},
        "script": {"type": "string", "enum": SCRIPTS},
        "confidence": {"type": "integer", "enum": [1, 2, 3]},
        "note": {"type": "string",
                 "description": "Brief evidence. Include #spurious-space or "
                                "#reading-order when they apply."},
    },
    "required": ["label", "script", "confidence", "note"],
    "additionalProperties": False,
}


def card(conn, obs_id):
    """The same blind card annotate.py prints, as one user message."""
    chars, seen, declared = conn.execute(
        "SELECT sampled_chars, n_pages_seen, n_pages_declared "
        "FROM font_observation WHERE obs_id=?", (obs_id,)).fetchone()
    parts = [f"{chars} characters sampled over {seen} page(s); "
             f"this font is declared on {declared} page(s) of the document."]
    for page, kind, text in conn.execute(
            "SELECT page, kind, text FROM excerpt WHERE obs_id=? "
            "ORDER BY CASE kind WHEN 'head' THEN 0 WHEN 'violation' THEN 1 "
            "ELSE 2 END, char_start", (obs_id,)):
        parts.append(f"\n--- {kind}, page {page} ---\n{text[:600]}")
    parts.append("\nLabel this font.")
    return "\n".join(parts)


def pending(conn, sample_id):
    return [r[0] for r in conn.execute(
        "SELECT s.obs_id FROM annotation_sample s WHERE s.sample_id=? "
        "AND s.obs_id NOT IN (SELECT obs_id FROM annotation "
        "                     WHERE annotator=? AND round=1) ORDER BY s.obs_id",
        (sample_id, ANNOTATOR))]


def submit(conn, client, sample_id, limit=None):
    obs_ids = pending(conn, sample_id)[:limit]
    if not obs_ids:
        print(f"nothing pending in '{sample_id}' for {ANNOTATOR}")
        return

    requests = [
        Request(
            custom_id=f"obs-{obs_id}",
            params=MessageCreateParamsNonStreaming(
                model=MODEL,
                max_tokens=4000,
                # The guidelines are identical across every request, so they
                # are the cache prefix; only the excerpts vary, and they go
                # last. Cheap here because the batch is large.
                system=[{"type": "text", "text": GUIDELINES,
                         "cache_control": {"type": "ephemeral"}}],
                output_config={
                    "format": {"type": "json_schema", "schema": SCHEMA},
                    # Classification against a fixed rubric, not open-ended
                    # reasoning. Low effort keeps the pass cheap; raise it only
                    # if the labels turn out to be poor.
                    "effort": "low",
                },
                messages=[{"role": "user", "content": card(conn, obs_id)}],
            ),
        )
        for obs_id in obs_ids
    ]

    batch = client.messages.batches.create(requests=requests)
    BATCH_ID_FILE.write_text(batch.id, encoding="utf-8")
    print(f"submitted {len(requests)} observations as {batch.id}")
    print(f"batch id saved to {BATCH_ID_FILE}")
    print("check with --status, then --collect")


def batch_id(explicit=None):
    if explicit:
        return explicit
    if BATCH_ID_FILE.exists():
        return BATCH_ID_FILE.read_text(encoding="utf-8").strip()
    sys.exit("no batch id: pass --batch-id, or run --submit first")


def status(client, bid):
    batch = client.messages.batches.retrieve(bid)
    counts = batch.request_counts
    print(f"{bid}  {batch.processing_status}")
    print(f"  succeeded {counts.succeeded}  errored {counts.errored}  "
          f"processing {counts.processing}  canceled {counts.canceled}  "
          f"expired {counts.expired}")
    return batch.processing_status == "ended"


def collect(conn, client, bid, sample_id):
    if not status(client, bid):
        print("\nbatch has not ended; nothing collected")
        return

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = skipped = failed = 0
    for result in client.messages.batches.results(bid):
        obs_id = int(result.custom_id.removeprefix("obs-"))
        if result.result.type != "succeeded":
            failed += 1
            continue
        msg = result.result.message
        if msg.stop_reason == "refusal":
            # A refusal is not a label. Left unwritten so it shows up as
            # pending rather than as a judgement nobody made.
            failed += 1
            continue
        text = next((b.text for b in msg.content if b.type == "text"), None)
        if not text:
            failed += 1
            continue
        answer = json.loads(text)
        try:
            conn.execute(
                "INSERT INTO annotation (obs_id, sample_id, annotator, round, "
                " label, script, confidence, saw_detector_output, "
                " saw_font_name, guideline_version, note, annotated_at) "
                "VALUES (?,?,?,1,?,?,?,0,0,?,?,?)",
                (obs_id, sample_id, ANNOTATOR, answer["label"],
                 answer["script"], answer["confidence"], GUIDELINE_VERSION,
                 answer.get("note"), now))
            written += 1
        except sqlite3.IntegrityError:
            skipped += 1
    conn.commit()

    print(f"\nwrote {written} annotations as {ANNOTATOR}")
    if skipped:
        print(f"{skipped} already present, left alone (append-only)")
    if failed:
        print(f"{failed} returned no usable label and stay pending")
    for label, n in conn.execute(
            "SELECT label, COUNT(*) FROM annotation WHERE annotator=? "
            "GROUP BY label ORDER BY 2 DESC", (ANNOTATOR,)):
        print(f"  {label:20} {n}")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--sample-id", default="gt-v1")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--batch-id", default=None)
    ap.add_argument("--limit", type=int, default=None,
                    help="submit only the first N, for a cheap trial run")
    ap.add_argument("--preview", action="store_true",
                    help="print one card and the request shape, send nothing")
    args = ap.parse_args()

    conn = sqlite3.connect(config.MANIFEST_DB)

    if args.preview:
        obs_ids = pending(conn, args.sample_id)
        if not obs_ids:
            sys.exit("nothing pending")
        print(f"{len(obs_ids)} observations pending, model {MODEL}\n")
        print(card(conn, obs_ids[0]))
        return

    client = anthropic.Anthropic()
    if args.submit:
        submit(conn, client, args.sample_id, args.limit)
    elif args.status:
        status(client, batch_id(args.batch_id))
    elif args.collect:
        collect(conn, client, batch_id(args.batch_id), args.sample_id)
    else:
        sys.exit("pass one of --preview, --submit, --status, --collect")


if __name__ == "__main__":
    main()
