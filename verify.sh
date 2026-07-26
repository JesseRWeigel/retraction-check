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
  # A skipped live section is "could not verify", not "verified". Reporting PASS here would
  # mean the suite certifies nothing about the code path that actually talks to Crossref.
  if [ "${RC_ALLOW_OFFLINE_VERIFY:-}" = "1" ]; then
    echo "  SKIPPED  api.crossref.org unreachable, and RC_ALLOW_OFFLINE_VERIFY=1 was set"
  else
    bad "live Crossref section could not run (set RC_ALLOW_OFFLINE_VERIFY=1 to accept that)"
  fi
fi

echo
echo "4. the REAL Retraction Watch implementation, against a real CSV"
# Every command above passes --no-rw, and the unit tests use a fake database, so the real
# RetractionWatchDB was never exercised: replacing its lookup with a stub that returns nothing
# left the whole suite green. fixtures/retractionwatch-sample.csv holds genuine rows with the
# genuine 21-column header, including every Retraction Watch row for Wakefield 1998.
RW=fixtures/retractionwatch-sample.csv
if python3 - "$RW" <<'PYCHECK'
import sys
from retraction_check.rwdb import RetractionWatchDB
db = RetractionWatchDB(sys.argv[1])
assert db.rows >= 10, f"fixture only parsed {db.rows} rows"
hits = db.lookup(doi="10.1016/S0140-6736(97)11096-0")
kinds = {h.kind for h in hits}
assert "retraction" in kinds, f"real parser did not find the retraction, got {kinds}"
eoc = {h.kind for h in db.lookup(doi="10.1124/jpet.114.218735")}
assert "expression_of_concern" in eoc, f"expected an expression of concern, got {eoc}"
assert db.lookup(doi="10.9999/definitely-not-real") == [], "a clean DOI must return nothing"
PYCHECK
then pass "real RetractionWatchDB parses a real CSV and finds a real retraction"
else bad "real RetractionWatchDB check"; fi

# The check above has to be able to fail. Stub the lookup and confirm it does, which is the
# control the original suite lacked: without it, an empty implementation passed everything.
if python3 - "$RW" <<'PYCONTROL'
import sys
from retraction_check import rwdb
rwdb.RetractionWatchDB.lookup = lambda self, **kw: []
db = rwdb.RetractionWatchDB(sys.argv[1])
sys.exit(0 if db.lookup(doi="10.1016/S0140-6736(97)11096-0") == [] else 1)
PYCONTROL
then pass "negative control: a stubbed lookup does return nothing, so the check above is real"
else bad "negative control did not behave as expected"; fi

echo
echo "5. an unresolvable citation fails by default"
# The documented CI workflow omitted --strict, so an offline run over a known-retracted DOI
# exited 0. A Crossref outage could produce a green build containing retracted citations.
expect_exit 3 "offline lookup of a retracted DOI is not a pass" \
  python3 -m retraction_check --offline --no-cache --no-rw --quiet --color never \
  --doi "10.1016/S0140-6736(97)11096-0"
expect_exit 0 "--allow-unchecked accepts it deliberately" \
  python3 -m retraction_check --offline --no-cache --no-rw --quiet --color never \
  --allow-unchecked --doi "10.1016/S0140-6736(97)11096-0"

echo
if [ "$fail" = 0 ]; then echo "PASS"; else echo "FAIL"; fi
exit "$fail"
