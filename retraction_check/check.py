"""Combine several independent retraction signals into one verdict per citation.

Coverage of any single signal is uneven. A paper can be marked retracted in the
Retraction Watch feed but carry a clean Crossref title, or carry a "RETRACTED:"
title prefix with no structured update record. So every signal is collected and
the worst one decides the verdict, with all of them shown in the report.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from . import crossref
from .identifiers import Citation

# Ordered worst first. Index doubles as the severity rank.
STATUSES = ("retracted", "concern", "correction", "notice", "unchecked", "clean")
RANK = {s: i for i, s in enumerate(STATUSES)}

RETRACTION_TYPES = {"retraction", "withdrawal", "removal", "partial_retraction"}
CONCERN_TYPES = {"expression_of_concern", "concern"}
CORRECTION_TYPES = {"correction", "erratum", "corrigendum", "addendum", "clarification"}
# A newer version existing is worth mentioning but is not a correctness problem,
# so it stays out of the exit code. Cochrane reviews trip this constantly.
VERSION_TYPES = {"new_edition", "new_version"}

TITLE_PATTERNS = (
    (re.compile(r"^\s*(\[)?retracted\b", re.I), "retracted"),
    (re.compile(r"^\s*retraction(\s+notice)?\s*[:\-]", re.I), "notice"),
    (re.compile(r"^\s*withdrawn\b", re.I), "retracted"),
    (re.compile(r"^\s*(this article has been )?withdrawn\b", re.I), "retracted"),
    (re.compile(r"^\s*expression of concern\b", re.I), "concern"),
    (re.compile(r"^\s*(temporary\s+)?removal\b", re.I), "retracted"),
)

RELATION_KEYS = {
    "retraction": "retracted",
    "is-retracted-by": "retracted",
    "has-retraction": "retracted",
    "is-withdrawn-by": "retracted",
    "has-expression-of-concern": "concern",
    "is-expression-of-concern-of": "notice",
    "has-correction": "correction",
    "is-correction-of": "notice",
    "is-retraction-of": "notice",
}


@dataclass
class Signal:
    status: str
    source: str      # crossref-updated-by, crossref-title, retraction-watch, ...
    detail: str


@dataclass
class Result:
    citation: Citation
    status: str = "clean"
    title: str = ""
    container: str = ""
    signals: list[Signal] = field(default_factory=list)
    note: str = ""

    @property
    def rank(self) -> int:
        return RANK[self.status]

    def add(self, sig: Signal) -> None:
        self.signals.append(sig)
        if RANK[sig.status] < RANK[self.status]:
            self.status = sig.status


def _type_to_status(update_type: str) -> str | None:
    t = (update_type or "").strip().lower().replace("-", "_").replace(" ", "_")
    if t in RETRACTION_TYPES:
        return "retracted"
    if t in CONCERN_TYPES:
        return "concern"
    if t in CORRECTION_TYPES:
        return "correction"
    if t in VERSION_TYPES:
        return "superseded"
    return None


def _scan_crossref(res: Result, message: dict) -> None:
    # Crossref returns HTML entities in titles, e.g. "Obstetrics &amp; Gynecology".
    titles = message.get("title") or []
    res.title = html.unescape((titles[0] if titles else "").strip())
    containers = message.get("container-title") or []
    res.container = html.unescape((containers[0] if containers else "").strip())

    # 1. Structured update records pointing at this work. This is the strongest
    #    signal and is where the Retraction Watch feed surfaces inside Crossref.
    for upd in message.get("updated-by") or []:
        status = _type_to_status(upd.get("type", ""))
        if not status:
            continue
        when = "-".join(
            str(p) for p in (upd.get("updated", {}).get("date-parts") or [[]])[0]
        )
        src = upd.get("source", "crossref")
        if status == "superseded":
            res.add(
                Signal(
                    status="notice",
                    source=f"crossref-updated-by/{src}",
                    detail=f"a newer version exists: {upd.get('DOI', 'n/a')}",
                )
            )
            continue
        res.add(
            Signal(
                status=status,
                source=f"crossref-updated-by/{src}",
                detail=f"{upd.get('label') or upd.get('type')} {when} via {upd.get('DOI', 'n/a')}",
            )
        )

    # 2. This record updates something else, so the cited item is itself a notice.
    #    Citing a retraction notice on purpose is legitimate, so this is only info.
    for upd in message.get("update-to") or []:
        status = _type_to_status(upd.get("type", ""))
        if status in ("retracted", "concern", "correction"):
            res.add(
                Signal(
                    status="notice",
                    source="crossref-update-to",
                    detail=(
                        f"this DOI is itself a notice ({upd.get('type')}) for "
                        f"{upd.get('DOI', 'an unnamed work')}"
                    ),
                )
            )

    # 3. The relation graph, which some publishers populate instead of update-to.
    for key, status in RELATION_KEYS.items():
        for rel in message.get("relation", {}).get(key, []) or []:
            res.add(
                Signal(
                    status=status,
                    source="crossref-relation",
                    detail=f"{key} -> {rel.get('id', '?')}",
                )
            )

    # 4. Title prefixes, which catch records with no structured update at all.
    for pattern, status in TITLE_PATTERNS:
        if res.title and pattern.search(res.title):
            res.add(
                Signal(
                    status=status,
                    source="crossref-title",
                    detail=f'title begins "{res.title[:60]}"',
                )
            )
            break


def _scan_rw(res: Result, records: list) -> None:
    reinstated = any(r.kind == "reinstatement" for r in records)
    for rec in records:
        status = {
            "retraction": "retracted",
            "removal": "retracted",
            "withdrawal": "retracted",
            "expression_of_concern": "concern",
            "correction": "correction",
        }.get(rec.kind)
        if not status:
            continue
        detail = f"{rec.nature} {rec.date}".strip()
        if rec.reason:
            detail += f" ({rec.reason[:80]})"
        res.add(Signal(status=status, source="retraction-watch-db", detail=detail))
    if reinstated:
        res.note = (
            "Retraction Watch also records a reinstatement for this paper. "
            "Read the notices before acting on the verdict."
        )
        res.signals.append(
            Signal(status="notice", source="retraction-watch-db", detail="Reinstatement on record")
        )


def check_one(cit: Citation, client, rw_db) -> Result:
    res = Result(citation=cit)
    checked_anything = False

    if cit.doi:
        rec = client.fetch(cit.doi)
        if rec.get("status") == crossref.FOUND:
            checked_anything = True
            _scan_crossref(res, rec.get("message", {}))
        elif rec.get("status") == crossref.NOT_FOUND:
            res.note = "not in Crossref"
        else:
            res.note = f"Crossref lookup failed: {rec.get('error', 'unknown error')}"
    elif cit.arxiv:
        res.note = (
            "arXiv identifier. arXiv DOIs are registered with DataCite, not Crossref, "
            "so the Crossref lookup does not apply."
        )
    elif cit.pmid:
        res.note = "PubMed ID with no DOI. Only the Retraction Watch database can match it."

    if rw_db is not None:
        records = rw_db.lookup(doi=cit.doi, pmid=cit.pmid)
        if cit.doi or cit.pmid:
            checked_anything = True
        _scan_rw(res, records)

    if not checked_anything and res.status == "clean":
        res.status = "unchecked"
    return res


def check_all(citations: list[Citation], client, rw_db, progress=None) -> list[Result]:
    results = []
    for i, cit in enumerate(citations, 1):
        results.append(check_one(cit, client, rw_db))
        if progress:
            progress(i, len(citations))
    return results


def exit_code(
    results: list[Result],
    strict: bool = False,
    allow_unchecked: bool = False,
) -> int:
    """0 clean, 1 retraction found, 2 concern or correction only, 3 cannot vouch.

    An unchecked citation fails by DEFAULT. This tool exists to answer one question, "is
    anything you cite retracted", and a citation it could not resolve is a citation it cannot
    answer for. Returning 0 there means a Crossref outage produces a green build containing
    retracted work, which is the single worst thing this tool could do, and it is exactly what
    it used to do: the documented CI workflow omitted --strict, so the safe behaviour was
    opt-in and nobody opted in.

    `allow_unchecked` exists for the reading list full of books and blog posts that have no
    DOI and never will. It has to be asked for, because "I know some of these are
    unresolvable" is a claim only the author can make.
    """
    statuses = {r.status for r in results}
    if "retracted" in statuses:
        return 1
    if "unchecked" in statuses and not allow_unchecked:
        return 3
    if statuses & {"concern", "correction"}:
        return 2
    return 0
