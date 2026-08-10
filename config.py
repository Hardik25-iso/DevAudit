"""
Paths and collection policy for DevAudit.

Kept in one file so the storage layout is a single decision, not a constant
scattered through the codebase.
"""

from pathlib import Path

# --- storage ---------------------------------------------------------------
# Bulk PDFs go on the external HDD: write-once, large, replaceable.
DATA_ROOT = Path(r"D:\DevAudit-data")
RAW_DIR = DATA_ROOT / "raw"
CACHE_DIR = DATA_ROOT / "cache"      # HF_HOME / TORCH_HOME, from Phase 3

# The manifest stays on the SSD. SQLite does many small random writes, which
# are slow on external USB storage and corruptible if the drive is unplugged
# mid-write. The PDFs can be re-downloaded; the provenance that makes them
# traceable cannot.
REPO_ROOT = Path(__file__).resolve().parent
MANIFEST_DB = REPO_ROOT / "data" / "manifest.sqlite"

# --- crawl policy ----------------------------------------------------------
USER_AGENT = (
    "DevAudit-research/0.1 (academic research on PDF text-layer quality; "
    "contact hardikglt2507@gmail.com)"
)
DEFAULT_CRAWL_DELAY = 2.0    # floor, per domain
MAX_CRAWL_DELAY = 30.0       # cap, so a hostile robots.txt cannot stall us
REQUEST_TIMEOUT = 60
MAX_RETRIES = 3
MAX_PDF_MB = 100             # skip pathological files during triage

# --- sources ---------------------------------------------------------------
# Only official .gov.in and official body domains. No commercial tender
# aggregators and no Scribd: unclear licensing makes a dataset unpublishable.
#
# `issuing_body` is recorded per document rather than inferred from the domain,
# because aggregators (OpenCity) republish documents issued by many different
# corporations. Getting this wrong would silently violate the 6-body spread
# requirement while appearing to satisfy it.
SOURCES = [
    {
        "key": "mhada",
        "issuing_body": "Maharashtra Housing and Area Development Authority",
        "domain": "mhada.gov.in",
        "seeds": ["https://www.mhada.gov.in/"],
        "doc_type": "notice/tender",
    },
    {
        "key": "punemetro",
        "issuing_body": "Maharashtra Metro Rail Corporation (Pune Metro)",
        "domain": "punemetrorail.org",
        "seeds": ["https://www.punemetrorail.org/"],
        "doc_type": "tender",
    },
    {
        "key": "nmc_nashik",
        "issuing_body": "Nashik Municipal Corporation",
        "domain": "nmc.gov.in",
        "seeds": ["https://nmc.gov.in/"],
        "doc_type": "municipal",
    },
    {
        "key": "nmc_nagpur",
        "issuing_body": "Nagpur Municipal Corporation",
        "domain": "nmcnagpur.gov.in",
        "seeds": ["https://www.nmcnagpur.gov.in/"],
        "doc_type": "municipal",
    },
    {
        "key": "pcmc",
        "issuing_body": "Pimpri-Chinchwad Municipal Corporation",
        "domain": "pcmcindia.gov.in",
        "seeds": ["https://www.pcmcindia.gov.in/"],
        "doc_type": "municipal",
    },
    {
        "key": "pmc",
        "issuing_body": "Pune Municipal Corporation",
        "domain": "pmc.gov.in",
        # PMC's listings are client-side rendered, so scraping the HTML returns
        # only a static shell. The documents live on a Drupal backend that
        # exposes JSON:API. Handled by a dedicated collector, not link scraping.
        "seeds": [],
        "jsonapi": "https://webadmin.pmc.gov.in/jsonapi",
        "doc_type": "budget/circular",
    },
]
