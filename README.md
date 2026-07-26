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
4. **Retraction Watch adds cases Crossref lacks.** The database comes from Crossref as a public
   dataset, 71,315 rows, refreshed with `--update-db`. The expression of concern above came from
   there rather than from the Crossref record.
5. **An expression of concern is not a retraction.** It gets its own exit code, because
   conflating the two either cries wolf or hides a real problem.
6. **A failed lookup is not a clean result.** `--strict` exits 3 when any citation could not be
   checked, so an outage cannot read as a pass.

## Using it

```bash
retraction-check refs.bib                          # a bibliography
retraction-check notes/*.md                        # markdown, DOIs and PMIDs extracted
retraction-check --doi 10.1016/S0140-6736(97)11096-0
retraction-check --update-db                       # refresh the Retraction Watch CSV
retraction-check notes/*.md --json                 # machine readable
retraction-check notes/*.md --strict               # unresolvable citations fail too
```

| Exit | Meaning |
|---:|---|
| 0 | Every citation resolved and none is retracted |
| 1 | At least one cited paper is retracted |
| 2 | Corrections or expressions of concern only, no retraction |
| 3 | Tool error, or `--strict` and something could not be checked |

Set `RETRACTION_CHECK_MAILTO` to get Crossref's polite pool and better rate limits. Responses
cache for 30 days by default, so a CI run costs almost nothing after the first.

## In CI

```yaml
- run: pip install -e . && retraction-check docs/**/*.md
```

Exit 1 fails the job. Use `|| [ $? -eq 2 ]` if corrections should pass.

## Status

Verified 2026-07-26.

```
$ ./verify.sh
1. parser unit checks
  ok    DOI, PMID and arXiv extraction from markdown, bibtex and plain lists
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

PASS
```

The live checks use Wakefield 1998, the retracted MMR paper, as a known positive and the NumPy
paper as a known negative. Both directions matter: a checker that flagged everything would pass
a positive-only test.

## Limitations

- **Coverage is only as good as Crossref and Retraction Watch.** A retraction announced solely
  in a journal's own PDF, with no Crossref record and no Retraction Watch entry, will not be seen.
- **No preprint retraction tracking.** arXiv withdrawals and bioRxiv removals are not checked.
  arXiv IDs are extracted and resolved where a DOI exists; a withdrawn preprint without one is
  invisible here.
- **A book, a webpage, or a citation with no persistent identifier cannot be checked.** Those
  count as unresolvable rather than clean, and `--strict` makes that fail.
- Title-only citations are not resolved. Fuzzy title matching produces false positives, and a
  false retraction claim is worse than a miss.

## License

MIT.
