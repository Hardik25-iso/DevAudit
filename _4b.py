import sqlite3, config, derive_mapping as dm, evaluate_conversion as ec
c=sqlite3.connect(str(config.MANIFEST_DB)); c.row_factory=sqlite3.Row
fams=[r["family_id"] for r in c.execute(
    "SELECT family_id FROM font_family ORDER BY n_observations DESC")]
# training volume after the 4b extraction, for the record
print("=== training volume per family ===", flush=True)
for f in fams:
    n=len(dm.page_pairs(c,f))
    ch=c.execute("""SELECT COALESCE(SUM(t.n_chars),0) FROM page_text t
      WHERE t.arm='pymupdf' AND t.sha256 IN (SELECT o.sha256 FROM family_member m
      JOIN font_observation o ON o.obs_id=m.obs_id WHERE m.family_id=?)""",(f,)).fetchone()[0]
    print(f"  {f:26}{n:>6} paired pages{ch:>12,} chars", flush=True)
for f in fams:
    print("\n" + "="*66, flush=True)
    try:
        ec.evaluate(c, f, f"4b-{f}", 4)
        t,_=dm.derive(c,f,4,verbose=False); dm.store(c,f,t)
        print(f"  stored {len(t)} rules", flush=True)
        ec.negative_control(c, f)
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {e}", flush=True)
print("\nPHASE 4B EVALUATION COMPLETE", flush=True)
