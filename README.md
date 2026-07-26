# retraction-check

Fail your build if you cite a retracted paper.

Point it at a reading list, a bibliography, or a directory of markdown notes. It resolves every
citation, cross-references Crossref and the Retraction Watch database, and exits nonzero when
something you cite has been retracted.

**[Read this on the web](https://jesserweigel.github.io/retraction-check/)**

## It found a retraction on its first real run

Run against a working peptides knowledgebase, 28 markdown files, 31 resolvable citations:

```
[RETRACTED] 10.1124/jpet.114.218735
    RETRACTED: The Procognitive and Synaptogenic Effects of Angiotensin IV-Derived Peptides
    in: The Journal of Pharmacology and Experimental Therapeutics
    DOI resolved from PMID 25187433 via PubMed
    cited at: peptides/dihexa.md:42, peptides/dihexa.md:145, peptides/dihexa.md:161
    - crossref-updated-by/publisher: Retraction 2014-11-1
    - crossref-updated-by/retraction-watch: Expression of concern 2021-9-22
    - retraction-watch-db: Expression of concern 9/22/2021 (Concerns/Issues about Image)

31 citations checked: 1 retracted, 1 correction, 1 notice, 28 ok
exit 1
```

Three citations in one file rested on a retracted study, and the retraction carries an
expression of concern about image integrity. Nothing about reading that file would have told
you. The citation was also a bare PubMed ID, so a DOI-only checker would have skipped it.

## Why this is harder than it looks

Six things have to go right before a check is trustworthy, and each is a place a naive
implementation quietly returns "clean":

1. **Bare PubMed IDs.** Notes cite PMIDs constantly. This resolves them to DOIs through NCBI
   E-utilities first, or 20 of the 23 PMIDs in the run above would not have been checked at all.
2. **A retraction notice is a separate DOI from the retracted paper.** Both need flagging, with
   different labels, because citing the notice is fine and citing the paper is not.
3. **Crossref coverage is uneven.** A retraction can appear in `update-to`, in `updated-by`, in
   `relation`, or only as a `RETRACTED:` title prefix. All four are checked, because relying on
   any one of them misses real cases.
4. **Retraction Watch adds cases Crossref lacks.** The database comes from Crossref's GitLab
   repository, 71,315 rows, refreshed with `--update-db`. The older Crossref Labs endpoint has
   stopped updating; it is kept only as a fallback and using it prints a warning, because a
   clean report against a frozen database means less than it looks like. The expression of concern above came from
   there rather than from the Crossref record.
5. **An expression of concern is not a retraction.** It gets its own exit code, because
   conflating the two either cries wolf or hides a real problem.
6. **A failed lookup is not a clean result, and that is the default.** An unresolved citation,
   or a failed Crossref or PubMed lookup, exits 3. `--allow-unchecked` opts out. This used to
   require `--strict` and the shipped CI workflow omitted it, so an outage produced a green
   build that could contain retracted citations. Found by independent review.

## Using it

```bash
retraction-check refs.bib                          # a bibliography
retraction-check notes/*.md                        # markdown, DOIs and PMIDs extracted
retraction-check --doi 10.1016/S0140-6736(97)11096-0
retraction-check --update-db                       # refresh the Retraction Watch CSV
retraction-check notes/*.md --json                 # machine readable
retraction-check notes/*.md --allow-unchecked       # accept items that have no DOI
```

| Exit | Meaning |
|---:|---|
| 0 | Every citation resolved and none is retracted |
| 1 | At least one cited paper is retracted |
| 2 | Corrections or expressions of concern only, no retraction |
| 3 | A citation could not be resolved, or a lookup failed. **This is the default**, not something `--strict` turns on |

Set `RETRACTION_CHECK_MAILTO` to get Crossref's polite pool and better rate limits. Responses
cache for 30 days by default, so a CI run costs almost nothing after the first.

## In CI

```yaml
- run: pip install -e . && retraction-check docs/**/*.md
```

Exit 1 fails the job on a retraction, exit 3 on anything it could not check. Use
`|| [ $? -eq 2 ]` if corrections should pass. Do not add `--allow-unchecked` to silence a
flaky network: it also silences an unresolvable citation.

## Status

Verified 2026-07-26.

```
$ ./verify.sh
1. unit tests
Ran 63 tests in 0.008s

OK
  ok    unit tests

2. exit codes, replayed from fixtures/cache
  ok    retracted DOI list flags a retraction (exit 1)
  ok    clean DOI list passes (exit 0)
  ok    expression of concern only (exit 2)
  ok    markdown reading list flags a retraction (exit 1)
  ok    bibtex bibliography flags a retraction (exit 1)
  ok    missing file is a tool error (exit 3)

3. live Crossref lookup
  ok    live: Wakefield 1998 is flagged as retracted (exit 1)
  ok    live: Harris 2020 NumPy paper is clean (exit 0)

4. the REAL Retraction Watch implementation, against a real CSV
  ok    real RetractionWatchDB parses a real CSV and finds a real retraction
  ok    negative control: a stubbed lookup does return nothing, so the check above is real

5. an unresolvable citation fails by default
  ok    offline lookup of a retracted DOI is not a pass (exit 3)
  ok    --allow-unchecked accepts it deliberately (exit 0)

PASS
```

The live checks use Wakefield 1998, the retracted MMR paper, as a known positive and the NumPy
paper as a known negative. Both directions matter: a checker that flagged everything would pass
a positive-only test.

## Limitations

- **Coverage is only as good as Crossref and Retraction Watch.** A retraction announced solely
  in a journal's own PDF, with no Crossref record and no Retraction Watch entry, will not be seen.
- **arXiv and other preprint IDs are extracted but never resolved.** There is no DataCite
  lookup and no DOI construction, so a bare `arXiv:1706.03762` comes back `[UNCHECKED]`, which
  now fails the run rather than passing it. An earlier version of this README claimed these were
  resolved where a DOI exists. They are not, and the claim was written from reading the code
  rather than running it.
- **A book, a webpage, or a citation with no persistent identifier cannot be checked.** Those
  count as unresolvable rather than clean, which fails the run by default. Pass
  `--allow-unchecked` when that is expected, understanding it also forgives outages.
- Title-only citations are not resolved. Fuzzy title matching produces false positives, and a
  false retraction claim is worse than a miss.

## License

MIT.
