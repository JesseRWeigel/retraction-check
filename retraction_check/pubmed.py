"""Resolve PubMed IDs to DOIs via NCBI E-utilities.

Health and life-science reading lists cite by PubMed link far more often than by
DOI, and a bare PMID cannot be looked up in Crossref. Without this step the
Crossref half of the checker simply does not run on most of a clinical
bibliography, which is how a retracted paper gets reported as only an expression
of concern. See the Benoist 2014 case in the README.

esummary needs no key and allows 3 requests per second, so IDs are batched.
"""

from __future__ import annotations

import json
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

from .crossref import default_cache_dir, user_agent

ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
BATCH = 100
THROTTLE = 0.4  # seconds between batches, comfortably inside NCBI's 3/sec limit


class PubMedResolver:
    def __init__(
        self,
        cache_dir: pathlib.Path | None = None,
        mailto: str | None = None,
        offline: bool = False,
        use_cache: bool = True,
        timeout: float = 30.0,
    ):
        self.cache_dir = pathlib.Path(cache_dir) if cache_dir else default_cache_dir() / "pubmed"
        self.mailto = mailto
        self.offline = offline
        self.use_cache = use_cache
        self.timeout = timeout
        self.resolved = 0
        self.errors: list[str] = []

    def _path(self, pmid: str) -> pathlib.Path:
        return self.cache_dir / f"{pmid}.json"

    def _read(self, pmid: str) -> dict | None:
        if not self.use_cache:
            return None
        p = self._path(pmid)
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write(self, pmid: str, rec: dict) -> None:
        if not self.use_cache:
            return
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._path(pmid).write_text(json.dumps(rec, sort_keys=True), encoding="utf-8")
        except OSError as exc:
            self.errors.append(f"pubmed cache write failed for {pmid}: {exc}")

    def resolve(self, pmids: list[str]) -> dict[str, str | None]:
        """Map each PMID to a DOI, or to None when PubMed has no DOI for it."""
        out: dict[str, str | None] = {}
        todo: list[str] = []
        for pmid in dict.fromkeys(pmids):
            rec = self._read(pmid)
            if rec is not None:
                out[pmid] = rec.get("doi")
            else:
                todo.append(pmid)
        if not todo or self.offline:
            for pmid in todo:
                out[pmid] = None
            return out

        for i in range(0, len(todo), BATCH):
            chunk = todo[i : i + BATCH]
            params = {"db": "pubmed", "retmode": "json", "id": ",".join(chunk)}
            if self.mailto:
                params["email"] = self.mailto
                params["tool"] = "retraction-check"
            url = f"{ESUMMARY}?{urllib.parse.urlencode(params)}"
            req = urllib.request.Request(url, headers={"User-Agent": user_agent(self.mailto)})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                result = payload.get("result", {})
            except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
                self.errors.append(f"PubMed lookup failed for {len(chunk)} ids: {exc}")
                for pmid in chunk:
                    out[pmid] = None
                continue

            for pmid in chunk:
                entry = result.get(pmid) or {}
                doi = None
                for aid in entry.get("articleids", []) or []:
                    if aid.get("idtype") == "doi" and aid.get("value", "").startswith("10."):
                        doi = aid["value"].strip().lower()
                        break
                out[pmid] = doi
                if doi:
                    self.resolved += 1
                self._write(pmid, {"pmid": pmid, "doi": doi, "fetched": time.time()})
            if i + BATCH < len(todo):
                time.sleep(THROTTLE)
        return out
