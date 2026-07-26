import unittest

from retraction_check.identifiers import (
    Citation,
    extract,
    extract_from_bibtex,
    extract_from_list,
    extract_from_text,
    normalize_doi,
)


class TestNormalizeDOI(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(normalize_doi("10.1038/s41586-020-2649-2"), "10.1038/s41586-020-2649-2")

    def test_lowercased(self):
        self.assertEqual(
            normalize_doi("10.1016/S0140-6736(97)11096-0"), "10.1016/s0140-6736(97)11096-0"
        )

    def test_strips_url_prefix(self):
        for raw in (
            "https://doi.org/10.1234/abc",
            "http://dx.doi.org/10.1234/abc",
            "doi.org/10.1234/abc",
            "doi:10.1234/abc",
            "DOI: 10.1234/abc",
        ):
            self.assertEqual(normalize_doi(raw), "10.1234/abc", raw)

    def test_strips_trailing_sentence_punctuation(self):
        self.assertEqual(normalize_doi("10.1234/abc."), "10.1234/abc")
        self.assertEqual(normalize_doi("10.1234/abc,"), "10.1234/abc")
        self.assertEqual(normalize_doi("10.1234/abc;"), "10.1234/abc")

    def test_keeps_balanced_parens_but_drops_markdown_paren(self):
        self.assertEqual(
            normalize_doi("10.1016/S0140-6736(97)11096-0)"), "10.1016/s0140-6736(97)11096-0"
        )
        self.assertEqual(
            normalize_doi("10.1016/S0140-6736(97)11096-0"), "10.1016/s0140-6736(97)11096-0"
        )

    def test_strips_publisher_view_suffix(self):
        self.assertEqual(
            normalize_doi(
                "https://www.cochranelibrary.com/cdsr/doi/10.1002/14651858.CD008900.pub3/full"
            ),
            "10.1002/14651858.cd008900.pub3",
        )

    def test_strips_query_and_fragment(self):
        self.assertEqual(normalize_doi("10.1234/abc?utm_source=x"), "10.1234/abc")
        self.assertEqual(normalize_doi("10.1234/abc#sec2"), "10.1234/abc")

    def test_rejects_non_doi(self):
        self.assertIsNone(normalize_doi("not a doi"))
        self.assertIsNone(normalize_doi(""))
        self.assertIsNone(normalize_doi("10.123/tooshortregistrant"))


class TestExtractMarkdown(unittest.TestCase):
    TEXT = (
        "See ([doi](https://doi.org/10.1016/S0140-6736(97)11096-0)) and\n"
        "doi:10.1038/s41586-020-2649-2 plus arXiv:1706.03762 and\n"
        "https://pubmed.ncbi.nlm.nih.gov/12937682/ and arxiv.org/abs/math/0211159\n"
    )

    def test_finds_everything(self):
        cites = extract_from_text(self.TEXT, "notes.md")
        keys = {c.key for c in cites}
        self.assertIn("10.1016/s0140-6736(97)11096-0", keys)
        self.assertIn("10.1038/s41586-020-2649-2", keys)
        self.assertIn("arXiv:1706.03762", keys)
        self.assertIn("arXiv:math/0211159", keys)
        self.assertIn("PMID:12937682", keys)

    def test_records_line_numbers(self):
        cites = {c.key: c for c in extract_from_text(self.TEXT, "notes.md")}
        self.assertEqual(cites["10.1016/s0140-6736(97)11096-0"].source, "notes.md:1")
        self.assertEqual(cites["PMID:12937682"].source, "notes.md:3")

    def test_dedupes_repeats_without_duplicating_the_same_site(self):
        text = "10.1234/abc and PMID: 999999 at https://pubmed.ncbi.nlm.nih.gov/999999/\n10.1234/abc\n"
        cites = {c.key: c for c in extract_from_text(text, "n.md")}
        self.assertEqual(len(cites), 2)
        self.assertEqual(cites["10.1234/abc"].labels, ["n.md:2"])
        # Both PMID patterns hit line 1, which must not produce a doubled site.
        self.assertEqual(cites["PMID:999999"].labels, [])

    def test_arxiv_doi_is_not_double_counted(self):
        cites = extract_from_text("10.48550/arXiv.1706.03762", "n.md")
        self.assertEqual([c.key for c in cites], ["10.48550/arxiv.1706.03762"])


class TestExtractBibtex(unittest.TestCase):
    BIB = """
@article{smith2020,
  title = {A paper},
  doi = {10.1234/abcd},
  year = {2020}
}

@misc{vaswani2017,
  title = {Attention Is All You Need},
  archivePrefix = {arXiv},
  eprint = {1706.03762}
}

@article{nodoi,
  title = {Only a URL},
  url = {https://example.org/x/10.5555/zzzz}
}
"""

    def test_reads_doi_field(self):
        cites = {c.key: c for c in extract_from_bibtex(self.BIB, "refs.bib")}
        self.assertIn("10.1234/abcd", cites)
        self.assertEqual(cites["10.1234/abcd"].source, "refs.bib:smith2020")

    def test_reads_arxiv_eprint(self):
        keys = {c.key for c in extract_from_bibtex(self.BIB, "refs.bib")}
        self.assertIn("arXiv:1706.03762", keys)

    def test_falls_back_to_url_field(self):
        keys = {c.key for c in extract_from_bibtex(self.BIB, "refs.bib")}
        self.assertIn("10.5555/zzzz", keys)


class TestExtractList(unittest.TestCase):
    def test_one_per_line_with_comments(self):
        text = "# a comment\n10.1234/abcd\n\nhttps://doi.org/10.5555/ee\n12345678\n"
        keys = [c.key for c in extract_from_list(text, "l.txt")]
        self.assertEqual(keys, ["10.1234/abcd", "10.5555/ee", "PMID:12345678"])

    def test_trailing_comment_on_doi_line_is_not_swallowed(self):
        keys = [c.key for c in extract_from_list("10.1234/abcd\n", "l.txt")]
        self.assertEqual(keys, ["10.1234/abcd"])


class TestFormatDetection(unittest.TestCase):
    def test_bib_extension_uses_the_entry_parser(self):
        cites = extract("@article{k2020,\n  doi = {10.1234/abcd}\n}\n", "a.bib")
        self.assertEqual([c.source for c in cites], ["a.bib:k2020"])

    def test_md_uses_regex_sweep(self):
        keys = [c.key for c in extract("prose 10.1234/abcd prose", "a.md")]
        self.assertEqual(keys, ["10.1234/abcd"])

    def test_bare_list_detected(self):
        keys = [c.key for c in extract("10.1234/abcd\n10.5555/ee\n", "a.txt")]
        self.assertEqual(keys, ["10.1234/abcd", "10.5555/ee"])


class TestCitationKey(unittest.TestCase):
    def test_doi_wins_over_pmid(self):
        c = Citation(raw="x", doi="10.1/x", pmid="123456")
        self.assertEqual(c.key, "10.1/x")


if __name__ == "__main__":
    unittest.main()
