"""Crossref REST client with an on-disk cache.

Only the fields the checker actually reads are cached, which keeps a bibliography
sized cache in the low hundreds of kilobytes instead of tens of megabytes.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.crossref.org/works/"
VERSION = "0.1.0"

# Setting a mailto puts requests in Crossref's polite pool, which gets better rate
# limits. No default address is baked in because this repo is public.
MAILTO_ENV = "RETRACTION_CHECK_MAILTO"

KEEP_FIELDS = (
    "DOI", "title", "type", "subtype", "updated-by", "update-to", "relation",
    "container-title", "publisher", "issued", "author", "abstract-license",
)

FOUND = "found"
NOT_FOUND = "not-found"
ERROR = "error"


def default_cache_dir() -> pathlib.Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return pathlib.Path(base) / "retraction-check"


def user_agent(mailto: str | None) -> str:
    ua = f"retraction-check/{VERSION} (+https://github.com/JesseRWeigel/retraction-check)"
    if mailto:
        ua += f" (mailto:{mailto})"
    return ua


def _prune(message: dict) -> dict:
    out = {k: message[k] for k in KEEP_FIELDS if k in message}
    # The author list is only used for display, so one name is enough.
    if isinstance(out.get("author"), list):
        out["author"] = out["author"][:1]
    return out


class CrossrefClient:
    def __init__(
        self,
        cache_dir: pathlib.Path | None = None,
        mailto: str | None = None,
        max_age_days: float = 30.0,
        offline: bool = False,
        use_cache: bool = True,
        timeout: float = 30.0,
        sleep_between: float = 0.0,
    ):
        self.cache_dir = pathlib.Path(cache_dir) if cache_dir else default_cache_dir() / "crossref"
        self.mailto = mailto or os.environ.get(MAILTO_ENV) or None
        self.max_age = max_age_days * 86400
        self.offline = offline
        self.use_cache = use_cache
        self.timeout = timeout
        self.sleep_between = sleep_between
        self.hits = 0
        self.misses = 0
        self.errors: list[str] = []

    def _path(self, doi: str) -> pathlib.Path:
        digest = hashlib.sha1(doi.encode("utf-8")).hexdigest()
        return self.cache_dir / digest[:2] / f"{digest}.json"

    def _read_cache(self, doi: str) -> dict | None:
        if not self.use_cache:
            return None
        p = self._path(doi)
        if not p.is_file():
            return None
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if rec.get("status") == ERROR:
            return None
        if not self.offline and self.max_age >= 0:
            age = time.time() - rec.get("fetched", 0)
            if self.max_age == 0 or age > self.max_age:
                return None
        return rec

    def _write_cache(self, doi: str, rec: dict) -> None:
        if not self.use_cache:
            return
        p = self._path(doi)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(rec, sort_keys=True), encoding="utf-8")
            tmp.replace(p)
        except OSError as exc:
            self.errors.append(f"cache write failed for {doi}: {exc}")

    def fetch(self, doi: str) -> dict:
        """Return {"status": found|not-found|error, "message": {...}}."""
        doi = doi.lower()
        cached = self._read_cache(doi)
        if cached is not None:
            self.hits += 1
            return cached
        if self.offline:
            self.misses += 1
            return {"status": ERROR, "error": "offline and not in cache", "doi": doi}

        url = API + urllib.parse.quote(doi, safe="")
        req = urllib.request.Request(url, headers={"User-Agent": user_agent(self.mailto)})
        rec: dict
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            rec = {
                "status": FOUND,
                "doi": doi,
                "fetched": time.time(),
                "message": _prune(payload.get("message", {})),
            }
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                rec = {"status": NOT_FOUND, "doi": doi, "fetched": time.time()}
            else:
                rec = {"status": ERROR, "doi": doi, "error": f"HTTP {exc.code}"}
                self.errors.append(f"{doi}: HTTP {exc.code}")
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            rec = {"status": ERROR, "doi": doi, "error": str(exc)}
            self.errors.append(f"{doi}: {exc}")

        self.misses += 1
        if rec["status"] in (FOUND, NOT_FOUND):
            self._write_cache(doi, rec)
        if self.sleep_between:
            time.sleep(self.sleep_between)
        return rec
