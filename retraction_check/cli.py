"""Command line entry point.

Exit codes, which are the point of the tool in CI:

    0  every citation checked out clean
    1  at least one cited paper has been retracted
    2  expressions of concern or corrections only, no retractions
    3  the tool could not do its job (bad input, network failure with --strict)
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

from . import crossref, pubmed, report, rwdb
from .check import check_all, exit_code
from .identifiers import Citation, extract

EXIT_TOOL_ERROR = 3


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="retraction-check",
        description=(
            "Cross-reference every citation in a reading list against Crossref "
            "retraction data. Exits nonzero when a cited paper has been retracted."
        ),
        epilog=(
            "exit codes: 0 clean, 1 retraction found, 2 concern or correction only, "
            "3 tool error"
        ),
    )
    p.add_argument("paths", nargs="*", help="files to check, or - for stdin")
    p.add_argument(
        "--format",
        choices=("auto", "markdown", "bibtex", "list"),
        default="auto",
        help="input format (default: auto-detect per file)",
    )
    p.add_argument("--doi", action="append", default=[], help="check a single DOI (repeatable)")
    p.add_argument("--json", action="store_true", help="machine readable output")
    p.add_argument("--show-clean", action="store_true", help="list passing citations too")
    p.add_argument("--quiet", action="store_true", help="suppress progress on stderr")
    p.add_argument(
        "--color", choices=("auto", "always", "never"), default="auto", help="colorize output"
    )
    p.add_argument(

        "--allow-unchecked",

        action="store_true",

        help=(

            "accept citations that could not be resolved, and lookup failures, "

            "as passing. Off by default: a citation this tool cannot resolve is "

            "one it cannot vouch for, so reporting success would let a Crossref "

            "outage produce a green build containing retracted work. Use it for a "

            "reading list of books and blog posts that genuinely have no DOI."

        ),

    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="exit 3 if any citation could not be checked at all",
    )
    p.add_argument(
        "--mailto",
        default=None,
        help=(
            "contact address for Crossref's polite pool, which gets better rate "
            f"limits. Defaults to ${crossref.MAILTO_ENV}."
        ),
    )
    p.add_argument("--cache-dir", default=None, help="where to cache Crossref responses")
    p.add_argument(
        "--max-age-days",
        type=float,
        default=30.0,
        help="refetch cached records older than this (default 30, 0 to always refetch)",
    )
    p.add_argument("--no-cache", action="store_true", help="do not read or write the cache")
    p.add_argument("--offline", action="store_true", help="use only cached data, never the network")
    p.add_argument(
        "--no-pubmed",
        action="store_true",
        help="do not resolve bare PubMed IDs to DOIs via NCBI E-utilities",
    )
    p.add_argument("--rw-db", default=None, help="path to a Retraction Watch CSV")
    p.add_argument("--no-rw", action="store_true", help="ignore the local Retraction Watch database")
    p.add_argument(
        "--update-db",
        action="store_true",
        help="download the Retraction Watch CSV from Crossref and exit",
    )
    p.add_argument("--version", action="version", version=f"retraction-check {crossref.VERSION}")
    return p


def gather_citations(args, errors: list[str]) -> list[Citation]:
    cites: list[Citation] = []
    for raw in args.doi:
        cites += extract(raw, filename="--doi", fmt="list")
    for path in args.paths:
        if path == "-":
            text = sys.stdin.read()
            cites += extract(text, filename="<stdin>", fmt=args.format)
            continue
        p = pathlib.Path(path)
        if not p.exists():
            errors.append(f"no such file: {path}")
            continue
        if p.is_dir():
            errors.append(f"{path} is a directory; pass files, or use a shell glob")
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            errors.append(f"could not read {path}: {exc}")
            continue
        cites += extract(text, filename=str(p), fmt=args.format)

    return merge(cites)


def merge(cites: list[Citation]) -> list[Citation]:
    """The same work can appear in several files. Check it once, keep every site."""
    merged: dict[str, Citation] = {}
    for c in cites:
        if c.key in merged:
            keep = merged[c.key]
            for site in [c.source] + c.labels:
                if site and site != keep.source and site not in keep.labels:
                    keep.labels.append(site)
            keep.pmid = keep.pmid or c.pmid
            keep.arxiv = keep.arxiv or c.arxiv
        else:
            merged[c.key] = c
    return list(merged.values())


def do_update_db(args) -> int:
    mailto = args.mailto or os.environ.get(crossref.MAILTO_ENV)
    if not mailto:
        print(
            "--update-db needs a contact address. Pass --mailto you@example.com or set "
            f"{crossref.MAILTO_ENV}. Crossref requires it on the dataset endpoint.",
            file=sys.stderr,
        )
        return EXIT_TOOL_ERROR
    cache = pathlib.Path(args.cache_dir) if args.cache_dir else crossref.default_cache_dir()
    dest = pathlib.Path(args.rw_db) if args.rw_db else rwdb.db_path(cache)
    print(f"downloading Retraction Watch CSV to {dest} ...", file=sys.stderr)
    try:
        size, source = rwdb.download(dest, mailto)
    except Exception as exc:  # network, disk, truncation
        print(f"download failed: {exc}", file=sys.stderr)
        return EXIT_TOOL_ERROR
    print(f"wrote {size / 1e6:.1f} MB from {source}", file=sys.stderr)
    if source == "labs-deprecated":
        # Silence here would be the bug. Crossref Labs stopped updating, so this file is
        # missing every case newer than the shutdown and a clean report against it means less
        # than it appears to.
        print(
            "WARNING: the current GitLab source was unreachable, so this came from the "
            "deprecated Crossref Labs endpoint, which no longer updates. Cases newer than "
            "its shutdown are absent. Re-run --update-db when GitLab is reachable.",
            file=sys.stderr,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.update_db:
        return do_update_db(args)

    if not args.paths and not args.doi:
        build_parser().print_usage(sys.stderr)
        print("nothing to check: pass a file, - for stdin, or --doi", file=sys.stderr)
        return EXIT_TOOL_ERROR

    errors: list[str] = []
    citations = gather_citations(args, errors)
    if errors:
        for e in errors:
            print(f"error: {e}", file=sys.stderr)
        return EXIT_TOOL_ERROR
    if not citations:
        print("no DOIs, arXiv IDs or PMIDs found in the input", file=sys.stderr)
        return EXIT_TOOL_ERROR

    cache_dir = pathlib.Path(args.cache_dir) / "crossref" if args.cache_dir else None
    client = crossref.CrossrefClient(
        cache_dir=cache_dir,
        mailto=args.mailto,
        max_age_days=args.max_age_days,
        offline=args.offline,
        use_cache=not args.no_cache,
    )
    rw_db, rw_note = rwdb.load_if_available(
        args.rw_db,
        pathlib.Path(args.cache_dir) if args.cache_dir else None,
        enabled=not args.no_rw,
    )

    pubmed_note = ""
    pubmed_errors: list[str] = []
    bare_pmids = [c.pmid for c in citations if c.pmid and not c.doi]
    if bare_pmids and not args.no_pubmed:
        resolver = pubmed.PubMedResolver(
            cache_dir=pathlib.Path(args.cache_dir) / "pubmed" if args.cache_dir else None,
            mailto=client.mailto,
            offline=args.offline,
            use_cache=not args.no_cache,
        )
        mapping = resolver.resolve(bare_pmids)
        pubmed_errors = list(resolver.errors)
        for c in citations:
            if c.pmid and not c.doi and mapping.get(c.pmid):
                c.doi = mapping[c.pmid]
                c.via = f"DOI resolved from PMID {c.pmid} via PubMed"
        citations = merge(citations)
        got = sum(1 for pmid in set(bare_pmids) if mapping.get(pmid))
        pubmed_note = f"PubMed: resolved {got}/{len(set(bare_pmids))} bare PMIDs to DOIs"
        for e in resolver.errors[:3]:
            pubmed_note += f"\n  warning: {e}"
    elif bare_pmids:
        pubmed_note = (
            f"{len(bare_pmids)} PMIDs left unresolved (--no-pubmed); "
            "Crossref cannot be queried for them"
        )

    def progress(i: int, total: int) -> None:
        if not args.quiet and not args.json and sys.stderr.isatty():
            print(f"\rchecking {i}/{total} ...", end="", file=sys.stderr, flush=True)

    results = check_all(citations, client, rw_db, progress=progress)
    if not args.quiet and not args.json and sys.stderr.isatty():
        print("\r" + " " * 30 + "\r", end="", file=sys.stderr)

    notes = [rw_note]
    if pubmed_note:
        notes.append(pubmed_note)
    notes.append(f"Crossref: {client.hits} cached, {client.misses} fetched")
    if not client.mailto:
        notes.append(
            f"no contact address set, using Crossref's anonymous pool. Set "
            f"${crossref.MAILTO_ENV} for better rate limits."
        )
    notes += [f"warning: {e}" for e in client.errors[:5]]

    if args.json:
        report.json_report(results, notes)
    else:
        report.text_report(
            results, notes, show_clean=args.show_clean, color=args.color
        )

    # Any lookup failure means the answer is unknown, and unknown is not clean. Crossref
    # errors used to be the only fatal kind, so a PubMed outage left a bare PMID marked
    # "checked" by a Retraction Watch query alone and the run reported success, missing a
    # publisher-only Crossref retraction attached to that PMID.
    lookup_failed = bool(client.errors) or bool(pubmed_errors)
    if lookup_failed and not args.allow_unchecked:
        return EXIT_TOOL_ERROR
    return exit_code(results, strict=args.strict, allow_unchecked=args.allow_unchecked)


if __name__ == "__main__":
    sys.exit(main())
