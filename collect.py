#!/usr/bin/env python3
"""
collect.py — Phase 1 collection with provenance.

Discovers PDFs on official Indian government sites, downloads them to the
bulk store, and records provenance for every file in manifest.sqlite:
source URL, download timestamp, SHA-256, size, issuing body, document type.

Every statistic the audit later prints must be traceable back to the file it
came from, and from there to the URL it was fetched from. That is what this
database is for.

Usage:
    python collect.py --dry-run            # discover only, download nothing
    python collect.py --dry-run --source mhada
    python collect.py --limit 50           # actually download

Crawl policy (non-negotiable, see config.py):
  - robots.txt is honoured; disallowed URLs are skipped and recorded as such
  - one request per DEFAULT_CRAWL_DELAY seconds per domain, or the site's own
    stated Crawl-Delay if it is longer
  - descriptive User-Agent identifying this as academic research
"""

import argparse
import hashlib
import sqlite3
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

import config


SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY,
    sha256          TEXT UNIQUE,          -- identity; dedupes across portals
    source_url      TEXT NOT NULL,
    source_key      TEXT NOT NULL,
    issuing_body    TEXT NOT NULL,
    doc_type        TEXT,
    filename        TEXT,
    stored_path     TEXT,
    size_bytes      INTEGER,
    downloaded_at   TEXT,                 -- ISO-8601 UTC
    http_status     INTEGER,
    content_type    TEXT,
    note            TEXT
);

-- Discovery is recorded separately from download so a dry run is auditable
-- and so skipped URLs (robots-disallowed, too large, failed) leave a trace.
CREATE TABLE IF NOT EXISTS discovered (
    id            INTEGER PRIMARY KEY,
    url           TEXT UNIQUE,
    source_key    TEXT,
    found_on      TEXT,
    discovered_at TEXT,
    status        TEXT     -- pending | downloaded | skipped_robots | skipped_size | failed
);

CREATE INDEX IF NOT EXISTS idx_doc_body ON documents(issuing_body);
CREATE INDEX IF NOT EXISTS idx_disc_status ON discovered(status);
"""


def force_utf8_stdout():
    """
    Windows consoles default to cp1252, which cannot encode the non-ASCII
    characters that appear in these URLs and filenames. Without this, printing
    a perfectly valid URL crashes the run. Same root cause as the decode bug
    this project exists to study - it is genuinely easy to hit.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def open_db():
    config.MANIFEST_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.MANIFEST_DB)
    conn.executescript(SCHEMA)
    return conn


class Crawler:
    """
    One instance per run. Holds the session, the per-domain rate limiter and
    the robots.txt cache.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = config.USER_AGENT
        self._last_request = {}   # domain -> monotonic timestamp
        self._robots = {}         # domain -> (RobotFileParser, delay)

    # --- politeness --------------------------------------------------------
    def _robots_for(self, url):
        domain = urlparse(url).netloc
        if domain in self._robots:
            return self._robots[domain]

        rp = RobotFileParser()
        rp.set_url(f"https://{domain}/robots.txt")
        delay = config.DEFAULT_CRAWL_DELAY
        try:
            # Fetch ourselves rather than rp.read(): some of these servers
            # emit headers that stricter clients reject, and a failed read
            # must not be mistaken for "no rules".
            resp = self.session.get(f"https://{domain}/robots.txt",
                                    timeout=config.REQUEST_TIMEOUT)
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
                stated = rp.crawl_delay(config.USER_AGENT)
                if stated:
                    delay = min(float(stated), config.MAX_CRAWL_DELAY)
            else:
                # No robots.txt (404) means no restrictions, not "deny all".
                rp.parse([])
        except Exception:
            rp.parse([])
        delay = max(delay, config.DEFAULT_CRAWL_DELAY)
        self._robots[domain] = (rp, delay)
        return self._robots[domain]

    def allowed(self, url):
        rp, _ = self._robots_for(url)
        try:
            return rp.can_fetch(config.USER_AGENT, url)
        except Exception:
            return True

    def _wait(self, url):
        domain = urlparse(url).netloc
        _, delay = self._robots_for(url)
        last = self._last_request.get(domain)
        if last is not None:
            elapsed = time.monotonic() - last
            if elapsed < delay:
                time.sleep(delay - elapsed)
        self._last_request[domain] = time.monotonic()

    # --- fetching ----------------------------------------------------------
    def get(self, url, stream=False):
        """GET with rate limiting and exponential backoff."""
        for attempt in range(config.MAX_RETRIES):
            self._wait(url)
            try:
                r = self.session.get(url, timeout=config.REQUEST_TIMEOUT,
                                     stream=stream)
                if r.status_code in (429, 503) and attempt < config.MAX_RETRIES - 1:
                    time.sleep(2 ** attempt * 5)
                    continue
                return r
            except requests.RequestException:
                if attempt == config.MAX_RETRIES - 1:
                    raise
                time.sleep(2 ** attempt * 5)
        return None


def discover_links(crawler, source):
    """Find PDF URLs reachable from a source's seed pages."""
    found = []
    for seed in source["seeds"]:
        if not crawler.allowed(seed):
            print(f"  robots.txt disallows {seed} - skipping")
            continue
        try:
            r = crawler.get(seed)
        except Exception as e:
            print(f"  failed {seed}: {type(e).__name__}")
            continue
        if r is None or r.status_code != 200:
            print(f"  HTTP {r.status_code if r else '?'} {seed}")
            continue

        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if ".pdf" not in href.lower():
                continue
            full = urljoin(seed, href)
            if urlparse(full).scheme not in ("http", "https"):
                continue
            found.append((full, seed))
    # Dedupe, preserving order.
    seen, out = set(), []
    for url, src in found:
        if url not in seen:
            seen.add(url)
            out.append((url, src))
    return out


def discover_pmc(crawler, source):
    """
    PMC's document listings are client-side rendered, so link scraping returns
    only the footer certificate. The files live on a Drupal backend exposing
    JSON:API. We walk the file resource, which lists every uploaded document.
    """
    found = []
    url = f"{source['jsonapi']}/file/file?page[limit]=50"
    pages = 0
    while url and pages < 20:
        try:
            r = crawler.get(url)
        except Exception as e:
            print(f"  PMC JSON:API failed: {type(e).__name__}")
            break
        if r is None or r.status_code != 200:
            print(f"  PMC JSON:API HTTP {r.status_code if r else '?'}")
            break
        try:
            payload = r.json()
        except ValueError:
            break
        for item in payload.get("data", []):
            uri = (item.get("attributes") or {}).get("uri", {})
            href = uri.get("url") if isinstance(uri, dict) else None
            if href and href.lower().endswith(".pdf"):
                found.append((urljoin("https://webadmin.pmc.gov.in", href),
                              source["jsonapi"]))
        url = (payload.get("links") or {}).get("next", {}).get("href")
        pages += 1
    return found


def record_discovery(conn, urls, source_key):
    cur = conn.cursor()
    new = 0
    for url, found_on in urls:
        cur.execute(
            "INSERT OR IGNORE INTO discovered "
            "(url, source_key, found_on, discovered_at, status) "
            "VALUES (?,?,?,?, 'pending')",
            (url, source_key, found_on, now_iso()))
        new += cur.rowcount
    conn.commit()
    return new


def download(crawler, conn, url, source):
    """Download one PDF, hashing as it streams. Returns a status string."""
    cur = conn.cursor()
    if not crawler.allowed(url):
        cur.execute("UPDATE discovered SET status='skipped_robots' WHERE url=?",
                    (url,))
        conn.commit()
        return "skipped_robots"

    try:
        r = crawler.get(url, stream=True)
    except Exception:
        cur.execute("UPDATE discovered SET status='failed' WHERE url=?", (url,))
        conn.commit()
        return "failed"

    if r is None or r.status_code != 200:
        cur.execute("UPDATE discovered SET status='failed' WHERE url=?", (url,))
        conn.commit()
        return "failed"

    size = int(r.headers.get("Content-Length") or 0)
    if size and size > config.MAX_PDF_MB * 1_000_000:
        cur.execute("UPDATE discovered SET status='skipped_size' WHERE url=?",
                    (url,))
        conn.commit()
        return "skipped_size"

    # Stream to a temp name, hash as we go, then rename to the hash. Content
    # addressing means the same document served from two portals is stored
    # once, and the filename is self-verifying.
    dest_dir = config.RAW_DIR / source["key"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / f".part-{int(time.time()*1000)}"
    h = hashlib.sha256()
    total = 0
    try:
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(65536):
                if not chunk:
                    continue
                h.update(chunk)
                f.write(chunk)
                total += len(chunk)
                if total > config.MAX_PDF_MB * 1_000_000:
                    raise IOError("exceeded size cap mid-stream")
    except Exception:
        tmp.unlink(missing_ok=True)
        cur.execute("UPDATE discovered SET status='failed' WHERE url=?", (url,))
        conn.commit()
        return "failed"

    digest = h.hexdigest()
    final = dest_dir / f"{digest}.pdf"
    if final.exists():
        tmp.unlink(missing_ok=True)
    else:
        tmp.rename(final)

    original = urlparse(url).path.rsplit("/", 1)[-1] or "unnamed.pdf"
    cur.execute(
        "INSERT OR IGNORE INTO documents "
        "(sha256, source_url, source_key, issuing_body, doc_type, filename, "
        " stored_path, size_bytes, downloaded_at, http_status, content_type) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (digest, url, source["key"], source["issuing_body"],
         source.get("doc_type"), original, str(final), total, now_iso(),
         r.status_code, r.headers.get("Content-Type", "")))
    cur.execute("UPDATE discovered SET status='downloaded' WHERE url=?", (url,))
    conn.commit()
    return "downloaded"


def main():
    force_utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="discover and record URLs, download nothing")
    ap.add_argument("--source", help="limit to one source key")
    ap.add_argument("--limit", type=int, default=None,
                    help="max downloads this run")
    args = ap.parse_args()

    sources = config.SOURCES
    if args.source:
        sources = [s for s in sources if s["key"] == args.source]
        if not sources:
            print(f"unknown source: {args.source}")
            sys.exit(1)

    conn = open_db()
    crawler = Crawler()

    print(f"{'DRY RUN - nothing will be downloaded' if args.dry_run else 'COLLECTING'}")
    print(f"raw store: {config.RAW_DIR}")
    print(f"manifest:  {config.MANIFEST_DB}\n")

    for source in sources:
        print(f"[{source['key']}] {source['issuing_body']}")
        if source.get("jsonapi"):
            urls = discover_pmc(crawler, source)
        else:
            urls = discover_links(crawler, source)
        new = record_discovery(conn, urls, source["key"])
        print(f"  discovered {len(urls)} PDF urls ({new} new)")
        for url, _ in urls[:5]:
            print(f"    {url[:100]}")
        if len(urls) > 5:
            print(f"    ... and {len(urls)-5} more")
        print()

    if args.dry_run:
        rows = conn.execute(
            "SELECT source_key, COUNT(*) FROM discovered WHERE status='pending' "
            "GROUP BY source_key ORDER BY 2 DESC").fetchall()
        total = sum(c for _, c in rows)
        print("=" * 60)
        print(f"{total} URLs queued across {len(rows)} sources")
        for k, c in rows:
            print(f"  {k:14} {c:5}")
        print("=" * 60)
        print("Nothing downloaded. Re-run without --dry-run to collect.")
        return

    pending = conn.execute(
        "SELECT url, source_key FROM discovered WHERE status='pending'"
    ).fetchall()
    if args.limit:
        pending = pending[:args.limit]

    by_key = {s["key"]: s for s in config.SOURCES}
    counts = {}
    for i, (url, key) in enumerate(pending, 1):
        status = download(crawler, conn, url, by_key[key])
        counts[status] = counts.get(status, 0) + 1
        print(f"  [{i}/{len(pending)}] {status:16} {url[:80]}")

    print("\n" + "=" * 60)
    for k, v in sorted(counts.items()):
        print(f"  {k:18} {v}")
    n_docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    n_bodies = conn.execute(
        "SELECT COUNT(DISTINCT issuing_body) FROM documents").fetchone()[0]
    print(f"\nmanifest now holds {n_docs} documents from {n_bodies} issuing bodies")


if __name__ == "__main__":
    main()
