#!/usr/bin/env bash
# Verify command for retraction-check. Exit 0 means the tool genuinely works.
#
#   1. Unit tests. Hermetic, no network.
#   2. End to end exit codes against fixture reading lists, replayed from the
#      committed Crossref cache in fixtures/cache so the result is deterministic.
#   3. A live Crossref lookup of a real retracted DOI. Skipped, loudly, if the
#      API is unreachable, because a broken network is not a broken tool.

set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

fail=0
pass() { printf '  ok    %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1"; fail=1; }

expect_exit() {
  local want="$1" label="$2"; shift 2
  "$@" >/dev/null 2>&1
  local got=$?
  if [ "$got" = "$want" ]; then pass "$label (exit $got)"; else bad "$label (want $want, got $got)"; fi
}

OFFLINE=(python3 -m retraction_check --offline --no-rw --quiet --color never --cache-dir fixtures/cache)

echo "1. unit tests"
python3 -m unittest discover -s tests -t . -q 2>&1 | tail -3
# tail would otherwise swallow the failure, so read the real exit code.
if [ "${PIPESTATUS[0]}" = 0 ]; then pass "unit tests"; else bad "unit tests"; fi

echo
echo "2. exit codes, replayed from fixtures/cache"
expect_exit 1 "retracted DOI list flags a retraction"   "${OFFLINE[@]}" fixtures/retracted.txt
expect_exit 0 "clean DOI list passes"                   "${OFFLINE[@]}" fixtures/clean.txt
expect_exit 2 "expression of concern only"              "${OFFLINE[@]}" fixtures/concern.txt
expect_exit 1 "markdown reading list flags a retraction" "${OFFLINE[@]}" fixtures/sample.md
expect_exit 1 "bibtex bibliography flags a retraction"  "${OFFLINE[@]}" fixtures/sample.bib
expect_exit 3 "missing file is a tool error"            "${OFFLINE[@]}" fixtures/nope.txt

echo
echo "3. live Crossref lookup"
if curl -sf -m 20 -o /dev/null \
     -H "User-Agent: retraction-check/verify" \
     "https://api.crossref.org/works/10.1038/s41586-020-2649-2"; then
  LIVE=(python3 -m retraction_check --no-rw --no-cache --quiet --color never)
  expect_exit 1 "live: Wakefield 1998 is flagged as retracted" \
    "${LIVE[@]}" --doi "10.1016/S0140-6736(97)11096-0"
  expect_exit 0 "live: Harris 2020 NumPy paper is clean" \
    "${LIVE[@]}" --doi "10.1038/s41586-020-2649-2"
else
  echo "  SKIPPED  api.crossref.org unreachable from this shell"
fi

echo
if [ "$fail" = 0 ]; then echo "PASS"; else echo "FAIL"; fi
exit "$fail"
