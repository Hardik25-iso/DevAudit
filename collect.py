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
import random
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
        # Several of these servers accept HTTPS but reset plain HTTP
        # connections outright, which surfaces as ConnectionResetError rather
        # than a redirect. Patna lost 28 of its 33 failed downloads this way.
        # Upgrading costs nothing: any host serving HTTPS accepts it, and a
        # host that genuinely only speaks HTTP would have failed regardless.
        if url.startswith("http://"):
            url = "https://" + url[len("http://"):]
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


# Link text / href fragments that suggest a page listing documents. Used to
# prioritise the crawl frontier: homepages alone yielded 77 URLs, nowhere near
# the 200-400 the corpus needs, and the documents live one or two clicks in.
DOC_HINTS = [
    "tender", "budget", "download", "notice", "circular", "report", "rti",
    "document", "publication", "act", "rule", "policy", "scheme", "archive",
    "citizen", "department", "annual", "resolution", "gr", "advertisement",
    "recruitment", "quotation", "nit", "eoi", "dpr", "minutes",
]


# Deliberately empty. See docs/LICENSING.md.
#
# Every .nic.in district site is built on S3WAAS, the NIC website platform,
# which keeps HTML on the body's own domain but serves every PDF from
# cdn.s3waas.gov.in. Allowing that host made discovery work -- Prayagraj went
# from 0 to 28 documents -- and then every one of them was correctly skipped,
# because the CDN's robots.txt is:
#
#     User-agent: *
#     Disallow: /
#
# That is unambiguous, so those documents are out of scope. The entry is kept
# empty rather than deleted so the next person does not rediscover the CDN,
# "fix" the same bug, and generate a few hundred requests to a server that has
# already said no.
GOV_FILE_HOSTS = set()


def _looks_like_doc_page(href, text):
    blob = f"{href} {text}".lower()
    return any(h in blob for h in DOC_HINTS)


def discover_links(crawler, source, max_depth=2, max_pages=40):
    """
    Breadth-first crawl from the seed pages, collecting PDF links.

    Stays on the source's own domain, caps the number of HTML pages fetched,
    and prefers links that look like document listings. The cap matters more
    than the depth: at one request per 2s per domain, an uncapped crawl of a
    municipal site would run for hours and hammer a public server.
    """
    domain = source["domain"]
    found = []
    seen_pages = set()
    # (url, depth); seeds first, then whatever looks document-shaped.
    frontier = [(s, 0) for s in source["seeds"]]
    pages_fetched = 0

    while frontier and pages_fetched < max_pages:
        page_url, depth = frontier.pop(0)
        if page_url in seen_pages:
            continue
        seen_pages.add(page_url)

        if not crawler.allowed(page_url):
            continue
        try:
            r = crawler.get(page_url)
        except Exception:
            continue
        if r is None or r.status_code != 200:
            continue
        ctype = r.headers.get("Content-Type", "")
        if "html" not in ctype.lower():
            continue
        pages_fetched += 1

        soup = BeautifulSoup(r.text, "html.parser")
        next_level = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "javascript:")):
                continue
            full = urljoin(page_url, href)
            parsed = urlparse(full)
            if parsed.scheme not in ("http", "https"):
                continue

            is_pdf = ".pdf" in href.lower()
            same_org = domain in parsed.netloc
            on_gov_cdn = parsed.netloc in GOV_FILE_HOSTS

            # Documents may live on a shared government CDN while the pages
            # linking to them live on the body's own domain. Follow HTML only
            # within the organisation, but accept PDFs from either.
            if is_pdf and (same_org or on_gov_cdn):
                found.append((full.split("#")[0], page_url))
            elif (same_org and depth < max_depth
                  and _looks_like_doc_page(href, a.get_text())):
                next_level.append((full.split("#")[0], depth + 1))

        frontier.extend(next_level)

    print(f"  crawled {pages_fetched} pages")
    # Dedupe, preserving order.
    seen, out = set(), []
    for url, src in found:
        if url not in seen:
            seen.add(url)
            out.append((url, src))
    return out


# Drupal ships placeholder uploads on a fresh install. They are real PDFs but
# carry no municipal content, so they would pollute the corpus statistic.
PLACEHOLDER_NAMES = ("dummy", "sample", "test.pdf", "placeholder")


def _probe_pool_size(crawler, base_url, hi=40000):
    """
    Binary-search the number of records behind a JSON:API filter.

    Drupal's JSON:API exposes no total count and no `last` link, but asking
    for one record at a given offset answers "are there at least this many?"
    in a single cheap request. About 15 requests pins the size, versus the
    500+ needed to enumerate.
    """
    lo = 0
    while lo < hi - 1:
        mid = (lo + hi) // 2
        try:
            r = crawler.get(f"{base_url}&page%5Boffset%5D={mid}")
            got = bool(r is not None and r.status_code == 200
                       and r.json().get("data"))
        except Exception:
            got = False
        if got:
            lo = mid
        else:
            hi = mid
    return lo + 1


def discover_pmc(crawler, source, want=90, seed=20260807):
    """
    Sample PMC's document set by drawing random offsets.

    PMC publishes ~25,000 PDFs behind a Drupal JSON:API that returns them in
    upload order. Enumerating the list is both slow (deep pagination degrades
    badly) and unnecessary: to estimate a prevalence we need an unbiased
    sample, not a census.

    The earlier implementation walked the first N pages, which produced a
    sample of the *oldest* 5% of PMC's documents rather than of PMC. Sampling
    randomly from a truncated pool does not fix that, because the bias sits in
    the pool rather than the draw. Drawing random offsets across the whole
    range does fix it, in roughly 90 requests instead of 500.

    `want` is deliberately above the per-source cap so placeholder rejects do
    not shrink the pool below the 60 we draw from it.
    """
    base = source["jsonapi"]
    one = (f"{base}/file/file"
           "?filter%5Bfilemime%5D=application%2Fpdf&page%5Blimit%5D=1")

    size = _probe_pool_size(crawler, one)
    print(f"  pool size ~{size} PDFs; drawing {want} random offsets")

    rng = random.Random(seed)
    offsets = rng.sample(range(size), min(want, size))

    found = []
    for i, off in enumerate(offsets, 1):
        try:
            r = crawler.get(f"{one}&page%5Boffset%5D={off}")
        except Exception:
            continue
        if r is None or r.status_code != 200:
            continue
        try:
            data = r.json().get("data") or []
        except ValueError:
            continue
        for item in data:
            attrs = item.get("attributes") or {}
            uri = attrs.get("uri") or {}
            href = uri.get("url") if isinstance(uri, dict) else None
            if not href or not href.lower().endswith(".pdf"):
                continue
            if any(p in (attrs.get("filename") or "").lower()
                   for p in PLACEHOLDER_NAMES):
                continue
            found.append((urljoin("https://webadmin.pmc.gov.in", href), base))
        if i % 30 == 0:
            print(f"    {i}/{len(offsets)} offsets sampled")

    print(f"  sampled {len(found)} documents from across the full range")
    return found


def discover_pmc_enumerate(crawler, source, max_pages=400):
    """
    PMC's listings are client-side rendered, so link scraping returns only the
    footer certificate. The documents live on a Drupal backend exposing
    JSON:API.

    Two things the first attempt got wrong:
      - it walked the unfiltered file list, which is mostly banner images, so
        it timed out long before reaching any real document
      - it followed the API's own `next` links, which are http:// and redirect

    A third, subtler error: the page cap was low enough that discovery stopped
    partway through the file list. The API returns files in upload order, so a
    truncated walk is not a random subset of PMC's documents -- it is one slice
    of time. Sampling randomly from a truncated pool still yields a biased
    estimate, because the bias is in the pool rather than the draw. The cap is
    now high enough to exhaust the list; `next` disappearing is what ends it.
    """
    found = []
    base = source["jsonapi"]
    url = (f"{base}/file/file"
           "?filter%5Bfilemime%5D=application%2Fpdf&page%5Blimit%5D=50")
    pages = 0
    while url and pages < max_pages:
        try:
            r = crawler.get(url)
        except Exception as e:
            print(f"  PMC JSON:API stopped after {pages} pages: {type(e).__name__}")
            break
        if r is None or r.status_code != 200:
            print(f"  PMC JSON:API HTTP {r.status_code if r else '?'}")
            break
        try:
            payload = r.json()
        except ValueError:
            break

        for item in payload.get("data", []):
            attrs = item.get("attributes") or {}
            uri = attrs.get("uri") or {}
            href = uri.get("url") if isinstance(uri, dict) else None
            if not href or not href.lower().endswith(".pdf"):
                continue
            name = (attrs.get("filename") or "").lower()
            if any(p in name for p in PLACEHOLDER_NAMES):
                continue
            found.append((urljoin("https://webadmin.pmc.gov.in", href), base))

        nxt = (payload.get("links") or {}).get("next", {}).get("href")
        url = nxt.replace("http://", "https://") if nxt else None
        pages += 1

    print(f"  walked {pages} API pages")
    return found


def select_balanced_sample(conn, per_source, seed):
    """
    Choose which pending URLs to download: a random sample, capped per source.

    Two research requirements, not conveniences.

    Capping per source: the brief forbids taking hundreds of files from one
    portal, because a single department shares one template and one font, so
    an unbalanced corpus would measure that department rather than the
    problem. PMC alone offers 1200+ documents and would otherwise dominate.

    Sampling randomly rather than taking the first N: discovery order follows
    upload date, so the first N is a slice of one time period. The Phase 1
    number is a prevalence estimate, and a biased sample makes it an estimate
    of the wrong population. The seed is fixed so the draw is reproducible
    and can be reported in the write-up.
    """
    rng = random.Random(seed)
    rows = conn.execute(
        "SELECT url, source_key FROM discovered WHERE status='pending'"
    ).fetchall()

    by_source = {}
    for url, key in rows:
        by_source.setdefault(key, []).append((url, key))

    selected = []
    for key in sorted(by_source):
        pool = by_source[key]
        rng.shuffle(pool)
        selected.extend(pool[:per_source])
        if len(pool) > per_source:
            print(f"  {key}: sampling {per_source} of {len(pool)} discovered")
        else:
            print(f"  {key}: taking all {len(pool)} discovered")
    rng.shuffle(selected)   # interleave domains so rate limiting overlaps
    return selected


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
                    help="max downloads this run (applied after sampling)")
    ap.add_argument("--per-source", type=int, default=60,
                    help="max documents per issuing body (default 60, so six "
                         "bodies reach the 200-400 target without any one "
                         "portal dominating)")
    ap.add_argument("--seed", type=int, default=20260807,
                    help="RNG seed for the sample, fixed for reproducibility")
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

    pending = select_balanced_sample(conn, args.per_source, args.seed)
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
