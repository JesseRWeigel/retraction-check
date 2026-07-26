"""End to end CLI tests.

These run against fixtures/cache, a small committed set of real Crossref and
PubMed responses, with --offline. So they exercise the whole pipeline including
argument parsing, extraction, signal fusion, reporting and exit codes, without
touching the network or depending on Crossref being up.
"""

import contextlib
import io
import json
import pathlib
import unittest

from retraction_check.cli import main

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
CACHE = FIXTURES / "cache"

BASE = ["--offline", "--no-rw", "--quiet", "--color", "never", "--cache-dir", str(CACHE)]


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(args) + BASE)
    return code, out.getvalue(), err.getvalue()


class TestExitCodes(unittest.TestCase):
    def test_retracted_list_exits_1(self):
        code, out, _ = run(str(FIXTURES / "retracted.txt"))
        self.assertEqual(code, 1)
        self.assertIn("[RETRACTED]", out)
        self.assertIn("10.1016/s0140-6736(97)11096-0", out)

    def test_clean_list_exits_0(self):
        code, out, _ = run(str(FIXTURES / "clean.txt"))
        self.assertEqual(code, 0)
        self.assertNotIn("[RETRACTED]", out)
        self.assertIn("nothing flagged", out)

    def test_expression_of_concern_exits_2(self):
        code, out, _ = run(str(FIXTURES / "concern.txt"))
        self.assertEqual(code, 2)
        self.assertIn("[CONCERN]", out)

    def test_markdown_input_exits_1(self):
        code, out, _ = run(str(FIXTURES / "sample.md"))
        self.assertEqual(code, 1)
        self.assertIn("sample.md:6", out)

    def test_bibtex_input_exits_1(self):
        code, out, _ = run(str(FIXTURES / "sample.bib"))
        self.assertEqual(code, 1)
        self.assertIn("sample.bib:wakefield1998", out)

    def test_clean_doi_inside_the_retracted_list_is_not_flagged(self):
        _, out, _ = run(str(FIXTURES / "retracted.txt"), "--show-clean")
        self.assertIn("[ok] 10.1038/s41586-020-2649-2", out)


class TestToolErrors(unittest.TestCase):
    def test_missing_file_exits_3(self):
        code, _, err = run(str(FIXTURES / "does-not-exist.txt"))
        self.assertEqual(code, 3)
        self.assertIn("no such file", err)

    def test_directory_argument_exits_3(self):
        code, _, err = run(str(FIXTURES))
        self.assertEqual(code, 3)
        self.assertIn("is a directory", err)

    def test_no_arguments_exits_3(self):
        code, _, err = run()
        self.assertEqual(code, 3)
        self.assertIn("nothing to check", err)

    def test_input_with_no_identifiers_exits_3(self):
        path = FIXTURES / "empty-ish.md"
        path.write_text("Just prose, no citations at all.\n", encoding="utf-8")
        try:
            code, _, err = run(str(path))
            self.assertEqual(code, 3)
            self.assertIn("no DOIs", err)
        finally:
            path.unlink()

    def test_a_retraction_in_the_file_outranks_its_unresolvable_arxiv_id(self):
        # sample.md contains both a retracted DOI and a bare arXiv id that cannot be
        # resolved. Exit 1 is correct: the retraction is a confirmed fact, and reporting 3
        # would bury it behind "some things could not be checked".
        code, _, _ = run(str(FIXTURES / "sample.md"), "--strict")
        self.assertEqual(code, 1)

    def test_an_unresolvable_citation_alone_is_a_tool_error(self):
        # With nothing confirmed either way, an unresolvable citation is the whole result,
        # and the tool must not report success.
        path = FIXTURES / "only-arxiv.md"
        path.write_text("See arXiv:1706.03762 for the architecture.\n", encoding="utf-8")
        try:
            code, _, _ = run(str(path), "--offline", "--no-cache", "--no-rw")
            self.assertEqual(code, 3)
            code, _, _ = run(str(path), "--offline", "--no-cache", "--no-rw", "--allow-unchecked")
            self.assertEqual(code, 0)
        finally:
            path.unlink(missing_ok=True)


class TestJSONOutput(unittest.TestCase):
    def test_shape_and_content(self):
        code, out, _ = run(str(FIXTURES / "retracted.txt"), "--json")
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertEqual(payload["summary"]["total"], 3)
        self.assertEqual(payload["summary"]["retracted"], 2)
        by_doi = {r["doi"]: r for r in payload["results"]}
        wakefield = by_doi["10.1016/s0140-6736(97)11096-0"]
        self.assertEqual(wakefield["status"], "retracted")
        self.assertTrue(any(s["source"] == "crossref-title" for s in wakefield["signals"]))
        self.assertTrue(wakefield["sources"])


class TestPMIDResolution(unittest.TestCase):
    def test_bare_pmid_gets_a_doi_from_the_cache(self):
        _, out, _ = run(str(FIXTURES / "sample.md"), "--show-clean")
        self.assertIn("DOI resolved from PMID 12937682 via PubMed", out)

    def test_no_pubmed_leaves_the_pmid_unresolved(self):
        _, out, _ = run(str(FIXTURES / "sample.md"), "--show-clean", "--no-pubmed")
        self.assertIn("PMID:12937682", out)
        self.assertNotIn("DOI resolved from PMID", out)


class TestStdin(unittest.TestCase):
    def test_reads_dois_from_stdin(self):
        import sys

        saved = sys.stdin
        sys.stdin = io.StringIO("10.1016/S0140-6736(97)11096-0\n")
        try:
            code, out, _ = run("-", "--format", "list")
        finally:
            sys.stdin = saved
        self.assertEqual(code, 1)
        self.assertIn("[RETRACTED]", out)


if __name__ == "__main__":
    unittest.main()
