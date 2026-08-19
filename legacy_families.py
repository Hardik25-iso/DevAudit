#!/usr/bin/env python3
"""
legacy_families.py — group legacy observations into encoding families.

    python legacy_families.py --build          # cluster and store
    python legacy_families.py --report         # read it back
    python legacy_families.py --build --threshold 0.85

An encoding family is what a mapping table is keyed on, so this has to be right
before anything can be derived. Font names cannot supply it: 546 distinct names
cover 5,438 convicted observations and the commonest are `F1`-`F8` (subset IDs),
`Calibri`, `ArialMT` and `Mangal`. Same lesson Phase 1 learned for detection --
classify by output.

The signature is the character-frequency distribution of the font's extracted
text, digits and whitespace removed. Two fonts using the same legacy encoding
map Devanagari onto the same byte set with roughly the same frequencies,
because they are encoding the same language.

Reads excerpts from the manifest, so it does not need the external drive.
"""

import argparse
import json
import math
import re
import sqlite3
from collections import Counter
from datetime import datetime

import config
from phase4_schema import MAPPING_VERSION  # noqa: F401  (version pinning only)

# Below this a frequency profile is noise rather than a signature. 60 was the
# floor the design spike used and every convicted observation cleared it.
MIN_SIGNATURE_CHARS = 60

# Cosine floor for joining a cluster. 0.80 produced 27 clusters that are each
# label-pure -- no cluster mixes LEGACY_8BIT with LEGACY_ASCII -- which is the
# evidence that it is finding encodings rather than an artifact of the metric.
DEFAULT_THRESHOLD = 0.80

# Only these are convertible. CMAP_INVALID is deliberately excluded: those
# documents emit real Devanagari codepoints in impossible order, which is a
# reordering problem and not an encoding one (design §5).
CONVERTIBLE = ("LEGACY_8BIT", "LEGACY_ASCII", "LEGACY_SYMBOL")


def signature(text):
    """Character frequency profile, digits and whitespace removed."""
    body = re.sub(r"[\s\d]", "", text or "")
    if len(body) < MIN_SIGNATURE_CHARS:
        return None
    counts = Counter(body)
    total = sum(counts.values())
    return {ch: n / total for ch, n in counts.items()}


def cosine(a, b):
    shared = set(a) & set(b)
    num = sum(a[k] * b[k] for k in shared)
    da = math.sqrt(sum(v * v for v in a.values()))
    db = math.sqrt(sum(v * v for v in b.values()))
    return num / (da * db) if da and db else 0.0


def load_observations(conn):
    rows = conn.execute(f"""
        SELECT o.obs_id, o.sha256, o.font_name, o.detector_label AS label,
               GROUP_CONCAT(e.text, ' ') AS text
        FROM font_observation o
        JOIN excerpt e ON e.obs_id = o.obs_id
        WHERE o.detector_label IN ({','.join('?' * len(CONVERTIBLE))})
        GROUP BY o.obs_id
    """, CONVERTIBLE).fetchall()
    out = []
    for r in rows:
        sig = signature(r["text"])
        if sig:
            out.append({"obs_id": r["obs_id"], "sha256": r["sha256"],
                        "font_name": r["font_name"], "label": r["label"],
                        "text": r["text"], "sig": sig})
    return out


def cluster(items, threshold):
    """
    Greedy single-pass clustering against running centroids.

    Greedy rather than k-means or hierarchical for two reasons: the number of
    families is not known in advance, and a greedy pass is auditable -- every
    membership decision is one cosine against one centroid, which can be
    checked by hand. A clustering nobody can check by hand is not usable as the
    key for a conversion table.
    """
    clusters = []
    for it in items:
        for cl in clusters:
            sim = cosine(it["sig"], cl["centroid"])
            if sim >= threshold:
                cl["items"].append((it, sim))
                for ch, v in it["sig"].items():
                    cl["sum"][ch] = cl["sum"].get(ch, 0.0) + v
                n = len(cl["items"])
                cl["centroid"] = {c: v / n for c, v in cl["sum"].items()}
                break
        else:
            clusters.append({"items": [(it, 1.0)], "sum": dict(it["sig"]),
                             "centroid": dict(it["sig"])})
    clusters.sort(key=lambda c: -len(c["items"]))
    return clusters


def family_id(index, cl):
    """Stable, readable id: rank plus the commonest font name, slugged."""
    names = Counter(it["font_name"] for it, _ in cl["items"])
    top = names.most_common(1)[0][0]
    slug = re.sub(r"[^a-z0-9]+", "", top.lower())[:12] or "unnamed"
    return f"fam-{index:02d}-{slug}"


def build(conn, threshold, top_n):
    items = load_observations(conn)
    print(f"convicted observations with a usable signature: {len(items)}")
    clusters = cluster(items, threshold)
    print(f"clusters at cosine >= {threshold}: {len(clusters)}")

    kept = clusters[:top_n] if top_n else clusters
    covered = sum(len(c["items"]) for c in kept)
    print(f"keeping top {len(kept)}: {covered}/{len(items)} = "
          f"{covered/max(len(items),1):.1%} of convicted observations\n")

    conn.execute("DELETE FROM family_member")
    conn.execute("DELETE FROM font_family")
    now = datetime.now().isoformat(timespec="seconds")

    print(f"{'family_id':26}{'obs':>5}{'docs':>6}{'label':14}{'example fonts':32}")
    for i, cl in enumerate(kept, 1):
        fid = family_id(i, cl)
        obs = [it for it, _ in cl["items"]]
        labels = Counter(o["label"] for o in obs)
        names = Counter(o["font_name"] for o in obs)
        docs = len({o["sha256"] for o in obs})
        # Longest excerpt: the most useful sample for a human checking the
        # family by eye before trusting a table derived from it.
        example = max((o["text"] or "" for o in obs), key=len)[:200]
        conn.execute("""INSERT INTO font_family
            (family_id, label, n_observations, n_documents, centroid,
             example_fonts, example_text, threshold, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (fid, labels.most_common(1)[0][0], len(obs), docs,
             json.dumps(cl["centroid"], ensure_ascii=False),
             ", ".join(n for n, _ in names.most_common(4)),
             example, threshold, now))
        conn.executemany(
            "INSERT OR REPLACE INTO family_member (family_id, obs_id, similarity) "
            "VALUES (?,?,?)",
            [(fid, it["obs_id"], sim) for it, sim in cl["items"]])
        purity = labels.most_common(1)[0][1] / len(obs)
        flag = "" if purity == 1.0 else f"  MIXED purity={purity:.2f}"
        print(f"{fid:26}{len(obs):>5}{docs:>6}{labels.most_common(1)[0][0]:14}"
              f"{', '.join(n for n,_ in names.most_common(2))[:30]:32}{flag}")
    conn.commit()

    tail = len(items) - covered
    if tail:
        print(f"\nunconverted tail: {tail} observations in {len(clusters)-len(kept)} "
              f"clusters, reported rather than dropped")


def report(conn):
    fams = conn.execute(
        "SELECT * FROM font_family ORDER BY n_observations DESC").fetchall()
    if not fams:
        print("no families built yet — run with --build")
        return
    print(f"{'family_id':26}{'obs':>5}{'docs':>6}{'label':14}{'rules':>7}")
    for f in fams:
        n = conn.execute("SELECT COUNT(*) FROM mapping_entry WHERE family_id=?",
                         (f["family_id"],)).fetchone()[0]
        print(f"{f['family_id']:26}{f['n_observations']:>5}{f['n_documents']:>6}"
              f"{f['label']:14}{n:>7}")
    print()
    for f in fams:
        print(f"--- {f['family_id']}   fonts: {f['example_fonts']}")
        print(f"    {' '.join((f['example_text'] or '').split())[:88]!r}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--top", type=int, default=5,
                    help="families to keep; 0 = all (design §2 scopes this to 5)")
    ap.add_argument("--db", default=str(config.MANIFEST_DB))
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    if args.build:
        build(conn, args.threshold, args.top)
    if args.report or not args.build:
        report(conn)
    conn.close()


if __name__ == "__main__":
    main()
