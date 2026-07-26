"""Human and machine readable output."""

from __future__ import annotations

import json
import sys

from .check import RANK, Result

MARK = {
    "retracted": "RETRACTED",
    "concern": "CONCERN",
    "correction": "CORRECTION",
    "notice": "NOTICE",
    "unchecked": "UNCHECKED",
    "clean": "ok",
}

COLOR = {
    "retracted": "\033[1;31m",
    "concern": "\033[1;33m",
    "correction": "\033[0;36m",
    "notice": "\033[0;34m",
    "unchecked": "\033[0;35m",
    "clean": "\033[0;32m",
}
RESET = "\033[0m"


def _use_color(stream, force: str) -> bool:
    if force == "never":
        return False
    if force == "always":
        return True
    return bool(getattr(stream, "isatty", lambda: False)())


def text_report(
    results: list[Result],
    notes: list[str],
    show_clean: bool = False,
    color: str = "auto",
    stream=None,
) -> None:
    # Resolved at call time, not at import, so tests can redirect stdout.
    stream = stream or sys.stdout
    tint = _use_color(stream, color)

    def paint(status: str, text: str) -> str:
        return f"{COLOR[status]}{text}{RESET}" if tint else text

    ordered = sorted(results, key=lambda r: (RANK[r.status], r.citation.key))
    flagged = [r for r in ordered if r.status != "clean"]
    shown = ordered if show_clean else flagged

    for note in notes:
        print(f"  {note}", file=stream)
    if notes:
        print("", file=stream)

    if not shown:
        print(f"{len(results)} citations checked, nothing flagged.", file=stream)
        return

    for r in shown:
        label = paint(r.status, f"[{MARK[r.status]}]")
        print(f"{label} {r.citation.key}", file=stream)
        if r.title:
            print(f"    {r.title[:100]}", file=stream)
        if r.container:
            print(f"    in: {r.container[:80]}", file=stream)
        if r.citation.via:
            print(f"    {r.citation.via}", file=stream)
        where = [r.citation.source] + r.citation.labels
        where = [w for w in where if w]
        if where:
            extra = f" (+{len(where) - 3} more)" if len(where) > 3 else ""
            print(f"    cited at: {', '.join(where[:3])}{extra}", file=stream)
        for sig in r.signals:
            print(f"    - {sig.source}: {sig.detail}", file=stream)
        if r.note:
            print(f"    note: {r.note}", file=stream)
        print("", file=stream)

    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    parts = [
        f"{counts[s]} {MARK[s].lower()}"
        for s in ("retracted", "concern", "correction", "notice", "unchecked", "clean")
        if counts.get(s)
    ]
    print(f"{len(results)} citations checked: {', '.join(parts)}", file=stream)


def json_report(results: list[Result], notes: list[str], stream=None) -> None:
    stream = stream or sys.stdout
    payload = {
        "notes": notes,
        "summary": {},
        "results": [],
    }
    for r in results:
        payload["summary"][r.status] = payload["summary"].get(r.status, 0) + 1
        payload["results"].append(
            {
                "identifier": r.citation.key,
                "doi": r.citation.doi,
                "arxiv": r.citation.arxiv,
                "pmid": r.citation.pmid,
                "status": r.status,
                "resolved_via": r.citation.via,
                "title": r.title,
                "container": r.container,
                "sources": [s for s in [r.citation.source] + r.citation.labels if s],
                "signals": [
                    {"status": s.status, "source": s.source, "detail": s.detail}
                    for s in r.signals
                ],
                "note": r.note,
            }
        )
    payload["summary"]["total"] = len(results)
    json.dump(payload, stream, indent=2, sort_keys=False)
    stream.write("\n")
