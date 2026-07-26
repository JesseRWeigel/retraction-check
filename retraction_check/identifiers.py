"""Pull citation identifiers out of markdown, BibTeX, and plain lists.

Everything downstream works on DOIs, so arXiv IDs and PubMed IDs are carried
alongside as secondary identifiers rather than being converted here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# A DOI is "10." then a registrant code then a slash then an opaque suffix. The
# suffix character class is deliberately broad because publishers put brackets,
# semicolons and parentheses in there. Trailing junk is trimmed by normalize_doi.
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:<>\[\]A-Za-z0-9]+")

ARXIV_NEW_RE = re.compile(r"(?:arxiv[:\s/]+|abs/|pdf/)(\d{4}\.\d{4,5})(v\d+)?", re.I)
ARXIV_OLD_RE = re.compile(
    r"(?:arxiv[:\s/]+|abs/|pdf/)([a-z-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?", re.I
)
PMID_URL_RE = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d{4,9})")
PMID_LABEL_RE = re.compile(r"\bPMID:?\s*(\d{4,9})\b", re.I)

# Publisher URLs hang a view mode off the end of the DOI path. These are not part
# of the DOI, and leaving them on turns a resolvable citation into a 404.
URL_TAIL_SEGMENTS = {
    "full", "abstract", "pdf", "epdf", "meta", "references", "citedby",
    "summary", "html", "fulltext", "abstract;", "full-text",
}

TRAILING_JUNK = ".,;:'\"”’)]}>\\ \t"


@dataclass
class Citation:
    """One identifier found in one place in one file."""

    raw: str
    doi: str | None = None
    arxiv: str | None = None
    pmid: str | None = None
    source: str = ""          # "file.md:12" or a BibTeX entry key
    labels: list[str] = field(default_factory=list)
    via: str = ""             # how a DOI was obtained, when not read from the text

    @property
    def key(self) -> str:
        return self.doi or (f"arXiv:{self.arxiv}" if self.arxiv else "") or (
            f"PMID:{self.pmid}" if self.pmid else ""
        ) or self.raw


def normalize_doi(raw: str) -> str | None:
    """Trim a DOI-looking string down to the DOI itself, or return None."""
    s = raw.strip()
    if not s:
        return None
    s = s.replace("\\n", " ").split()[0] if "\\n" in s else s
    # doi.org/ and dx.doi.org/ prefixes, with or without a scheme.
    s = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    s = s.split("#", 1)[0].split("?", 1)[0]
    s = s.strip(TRAILING_JUNK)

    # Markdown wraps links in parens, so a DOI ending in an unmatched ")" almost
    # always borrowed that paren from the surrounding text.
    while s.endswith(")") and s.count(")") > s.count("("):
        s = s[:-1].rstrip(TRAILING_JUNK)
    while s.endswith("]") and s.count("]") > s.count("["):
        s = s[:-1].rstrip(TRAILING_JUNK)

    parts = s.split("/")
    while len(parts) > 2 and parts[-1].lower() in URL_TAIL_SEGMENTS:
        parts.pop()
    s = "/".join(parts)

    if not DOI_RE.fullmatch(s):
        m = DOI_RE.search(s)
        if not m:
            return None
        s = m.group(0).strip(TRAILING_JUNK)
    return s.lower()


def normalize_arxiv(raw: str) -> str:
    return raw.strip().lower().removeprefix("arxiv:").strip()


def _dedupe(cites: list[Citation]) -> list[Citation]:
    """Collapse repeats of the same identifier, keeping every source location."""
    out: dict[str, Citation] = {}
    for c in cites:
        if not c.key:
            continue
        if c.key in out:
            existing = out[c.key]
            if c.source and c.source != existing.source and c.source not in existing.labels:
                existing.labels.append(c.source)
        else:
            out[c.key] = c
    return list(out.values())


def extract_from_text(text: str, filename: str = "") -> list[Citation]:
    """Regex sweep for DOIs, arXiv IDs and PMIDs. Used for markdown and prose."""
    found: list[Citation] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        where = f"{filename}:{lineno}" if filename else f"line {lineno}"
        seen_spans: list[tuple[int, int]] = []
        for m in DOI_RE.finditer(line):
            doi = normalize_doi(m.group(0))
            if doi:
                found.append(Citation(raw=m.group(0), doi=doi, source=where))
                seen_spans.append(m.span())
        for rx in (ARXIV_NEW_RE, ARXIV_OLD_RE):
            for m in rx.finditer(line):
                # An arXiv DOI (10.48550/arXiv.x) already matched as a DOI above.
                if any(s <= m.start() < e for s, e in seen_spans):
                    continue
                found.append(
                    Citation(raw=m.group(0), arxiv=normalize_arxiv(m.group(1)), source=where)
                )
        for rx in (PMID_URL_RE, PMID_LABEL_RE):
            for m in rx.finditer(line):
                found.append(Citation(raw=m.group(0), pmid=m.group(1), source=where))
    return _dedupe(found)


BIB_ENTRY_RE = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)\n\}", re.S)
BIB_FIELD_RE = re.compile(r"(\w+)\s*=\s*(\{(?:[^{}]|\{[^{}]*\})*\}|\"[^\"]*\"|[^,\n]+)")


def extract_from_bibtex(text: str, filename: str = "") -> list[Citation]:
    """Parse @entry blocks and read doi / eprint fields, with a regex fallback."""
    found: list[Citation] = []
    for m in BIB_ENTRY_RE.finditer(text):
        key, body = m.group(2), m.group(3)
        where = f"{filename}:{key}" if filename else key
        fields = {
            f.group(1).lower(): f.group(2).strip().strip("{}\"").strip()
            for f in BIB_FIELD_RE.finditer(body)
        }
        doi = normalize_doi(fields.get("doi", ""))
        if not doi:
            for candidate in (fields.get("url", ""), fields.get("note", ""), body):
                hit = DOI_RE.search(candidate)
                if hit:
                    doi = normalize_doi(hit.group(0))
                    if doi:
                        break
        arxiv = None
        if fields.get("archiveprefix", "").lower() == "arxiv" and fields.get("eprint"):
            arxiv = normalize_arxiv(fields["eprint"])
        elif fields.get("eprint", "").lower().startswith("arxiv"):
            arxiv = normalize_arxiv(fields["eprint"])
        pmid = fields.get("pmid") or None
        if pmid and not pmid.isdigit():
            pmid = None
        if doi or arxiv or pmid:
            found.append(Citation(raw=key, doi=doi, arxiv=arxiv, pmid=pmid, source=where))
    if not found:
        # Not a parseable .bib after all. Better to find the DOIs than to give up.
        return extract_from_text(text, filename)
    return _dedupe(found)


def extract_from_list(text: str, filename: str = "") -> list[Citation]:
    """One identifier per line, blank lines and # comments ignored."""
    found: list[Citation] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.split("#", 1)[0].strip() if line.lstrip().startswith("#") else line.strip()
        if not line:
            continue
        where = f"{filename}:{lineno}" if filename else f"line {lineno}"
        doi = normalize_doi(line)
        if doi:
            found.append(Citation(raw=line, doi=doi, source=where))
            continue
        m = ARXIV_NEW_RE.search(line) or ARXIV_OLD_RE.search(line)
        if m:
            found.append(Citation(raw=line, arxiv=normalize_arxiv(m.group(1)), source=where))
            continue
        m = PMID_URL_RE.search(line) or PMID_LABEL_RE.search(line)
        if m:
            found.append(Citation(raw=line, pmid=m.group(1), source=where))
            continue
        if line.isdigit() and 4 <= len(line) <= 9:
            found.append(Citation(raw=line, pmid=line, source=where))
    return _dedupe(found)


def detect_format(filename: str, text: str) -> str:
    lower = filename.lower()
    if lower.endswith((".bib", ".bibtex")):
        return "bibtex"
    if lower.endswith((".md", ".markdown", ".mdx", ".rst", ".html", ".htm", ".json")):
        return "markdown"
    if "@article{" in text or "@inproceedings{" in text or "@misc{" in text:
        return "bibtex"
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if lines and all(len(ln.split()) == 1 for ln in lines):
        return "list"
    return "markdown"


def extract(text: str, filename: str = "", fmt: str = "auto") -> list[Citation]:
    if fmt == "auto":
        fmt = detect_format(filename, text)
    if fmt == "bibtex":
        return extract_from_bibtex(text, filename)
    if fmt == "list":
        return extract_from_list(text, filename)
    return extract_from_text(text, filename)
