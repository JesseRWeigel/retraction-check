"""Optional local copy of the Retraction Watch database.

Crossref publishes the Retraction Watch dataset as a public CSV under CC0 at
api.labs.crossref.org/data/retractionwatch. It needs no credentials, only a
contact address in the query string. Downloading it is the supported access
route, so this is a download and not a scrape.

The CSV is roughly 65 MB and around 71,000 rows, so it is never committed. Fetch
it with `retraction-check --update-db --mailto you@example.com`.
"""

from __future__ import annotations

import csv
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .crossref import default_cache_dir, user_agent

DOWNLOAD_URL = "https://api.labs.crossref.org/data/retractionwatch"

NATURE_TO_KIND = {
    "retraction": "retraction",
    "expression of concern": "expression_of_concern",
    "correction": "correction",
    "reinstatement": "reinstatement",
    "removal": "removal",
    "withdrawal": "withdrawal",
}


@dataclass
class RWRecord:
    kind: str
    nature: str
    date: str
    journal: str
    reason: str
    retraction_doi: str
    record_id: str


def db_path(cache_dir: pathlib.Path | None = None) -> pathlib.Path:
    base = pathlib.Path(cache_dir) if cache_dir else default_cache_dir()
    return base / "retractionwatch.csv"


def download(dest: pathlib.Path, mailto: str, timeout: float = 300.0) -> int:
    """Fetch the CSV to dest. Returns bytes written."""
    url = f"{DOWNLOAD_URL}?{urllib.parse.quote(mailto)}"
    req = urllib.request.Request(url, headers={"User-Agent": user_agent(mailto)})
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    written = 0
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as fh:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
            written += len(chunk)
    if written < 1_000_000:
        tmp.unlink(missing_ok=True)
        raise OSError(f"download looks truncated ({written} bytes)")
    tmp.replace(dest)
    return written


class RetractionWatchDB:
    """DOI and PMID index over the Retraction Watch CSV."""

    def __init__(self, path: pathlib.Path):
        self.path = pathlib.Path(path)
        self.by_doi: dict[str, list[RWRecord]] = {}
        self.by_pmid: dict[str, list[RWRecord]] = {}
        self.rows = 0
        self._load()

    def _load(self) -> None:
        csv.field_size_limit(min(sys.maxsize, 10**7))
        with open(self.path, encoding="utf-8", errors="replace", newline="") as fh:
            for row in csv.DictReader(fh):
                self.rows += 1
                nature = (row.get("RetractionNature") or "").strip()
                rec = RWRecord(
                    kind=NATURE_TO_KIND.get(nature.lower(), nature.lower() or "unknown"),
                    nature=nature or "Unknown",
                    # Stored as "6/29/2026 0:00", and sometimes blank.
                    date=((row.get("RetractionDate") or "").strip().split() or [""])[0],
                    journal=(row.get("Journal") or "").strip(),
                    reason=(row.get("Reason") or "").strip().strip("+;"),
                    retraction_doi=(row.get("RetractionDOI") or "").strip(),
                    record_id=(row.get("Record ID") or "").strip(),
                )
                doi = (row.get("OriginalPaperDOI") or "").strip().lower()
                if doi.startswith("10."):
                    self.by_doi.setdefault(doi, []).append(rec)
                pmid = (row.get("OriginalPaperPubMedID") or "").strip()
                if pmid.isdigit() and pmid != "0":
                    self.by_pmid.setdefault(pmid, []).append(rec)

    def lookup(self, doi: str | None = None, pmid: str | None = None) -> list[RWRecord]:
        out: list[RWRecord] = []
        if doi:
            out += self.by_doi.get(doi.lower(), [])
        if pmid:
            for rec in self.by_pmid.get(pmid, []):
                if rec not in out:
                    out.append(rec)
        return out


def load_if_available(
    explicit: str | None, cache_dir: pathlib.Path | None, enabled: bool = True
) -> tuple[RetractionWatchDB | None, str]:
    """Return (db, note). A missing database is normal, not an error."""
    if not enabled:
        return None, "Retraction Watch database disabled (--no-rw)"
    candidate = pathlib.Path(explicit) if explicit else db_path(cache_dir)
    if not candidate.is_file():
        return None, (
            f"no local Retraction Watch database at {candidate}; "
            "using Crossref API signals only (run --update-db to add it)"
        )
    try:
        db = RetractionWatchDB(candidate)
    except (OSError, ValueError) as exc:
        return None, f"could not read Retraction Watch database: {exc}"
    age_days = (time.time() - os.path.getmtime(candidate)) / 86400
    return db, f"Retraction Watch database: {db.rows} rows, {age_days:.0f} days old"
