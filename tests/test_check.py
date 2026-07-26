import unittest

from retraction_check import crossref
from retraction_check.check import check_one, exit_code
from retraction_check.identifiers import Citation
from retraction_check.rwdb import RWRecord


class FakeClient:
    """Stands in for CrossrefClient so the checker can be tested with no network."""

    def __init__(self, records):
        self.records = records
        self.hits = 0
        self.misses = 0
        self.errors = []
        self.mailto = None

    def fetch(self, doi):
        return self.records.get(
            doi.lower(), {"status": crossref.NOT_FOUND, "doi": doi.lower()}
        )


class FakeRW:
    def __init__(self, by_doi=None, by_pmid=None):
        self.by_doi = by_doi or {}
        self.by_pmid = by_pmid or {}

    def lookup(self, doi=None, pmid=None):
        out = list(self.by_doi.get((doi or "").lower(), []))
        for rec in self.by_pmid.get(pmid or "", []):
            if rec not in out:
                out.append(rec)
        return out


def found(**message):
    return {"status": crossref.FOUND, "message": message}


class TestCrossrefSignals(unittest.TestCase):
    def check(self, doi, message, rw=None):
        client = FakeClient({doi: found(**message)})
        return check_one(Citation(raw=doi, doi=doi), client, rw)

    def test_clean_paper(self):
        r = self.check("10.1/clean", {"title": ["Array programming with NumPy"]})
        self.assertEqual(r.status, "clean")
        self.assertEqual(r.signals, [])

    def test_updated_by_retraction(self):
        r = self.check(
            "10.1/x",
            {
                "title": ["A paper"],
                "updated-by": [
                    {"type": "retraction", "DOI": "10.1/notice", "source": "publisher"}
                ],
            },
        )
        self.assertEqual(r.status, "retracted")

    def test_updated_by_expression_of_concern(self):
        r = self.check(
            "10.1/x",
            {"title": ["A paper"], "updated-by": [{"type": "expression_of_concern"}]},
        )
        self.assertEqual(r.status, "concern")

    def test_updated_by_erratum_is_a_correction(self):
        r = self.check("10.1/x", {"title": ["A paper"], "updated-by": [{"type": "erratum"}]})
        self.assertEqual(r.status, "correction")

    def test_new_version_does_not_count_against_the_citation(self):
        r = self.check("10.1/x", {"title": ["A review"], "updated-by": [{"type": "new_version"}]})
        self.assertEqual(r.status, "notice")
        self.assertEqual(exit_code([r]), 0)

    def test_title_prefix_alone_is_enough(self):
        r = self.check("10.1/x", {"title": ["RETRACTED: Something wrong"]})
        self.assertEqual(r.status, "retracted")
        self.assertEqual([s.source for s in r.signals], ["crossref-title"])

    def test_withdrawn_title(self):
        r = self.check("10.1/x", {"title": ["WITHDRAWN: a preprint"]})
        self.assertEqual(r.status, "retracted")

    def test_relation_retraction_key(self):
        r = self.check(
            "10.1/x",
            {"title": ["A paper"], "relation": {"retraction": [{"id": "10.1/notice"}]}},
        )
        self.assertEqual(r.status, "retracted")

    def test_worst_signal_wins_and_all_are_kept(self):
        r = self.check(
            "10.1/x",
            {
                "title": ["RETRACTED: A paper"],
                "updated-by": [
                    {"type": "expression_of_concern"},
                    {"type": "retraction"},
                ],
            },
        )
        self.assertEqual(r.status, "retracted")
        self.assertEqual(len(r.signals), 3)

    def test_citing_a_retraction_notice_is_only_a_notice(self):
        r = self.check(
            "10.1/notice",
            {"title": ["Retraction: A paper"], "update-to": [{"type": "retraction", "DOI": "10.1/x"}]},
        )
        self.assertEqual(r.status, "notice")
        self.assertEqual(exit_code([r]), 0)

    def test_html_entities_are_unescaped(self):
        r = self.check(
            "10.1/x", {"title": ["Obstetrics &amp; Gynecology"], "container-title": ["A &amp; B"]}
        )
        self.assertEqual(r.title, "Obstetrics & Gynecology")
        self.assertEqual(r.container, "A & B")

    def test_doi_missing_from_crossref(self):
        client = FakeClient({})
        r = check_one(Citation(raw="10.1/nope", doi="10.1/nope"), client, None)
        self.assertEqual(r.status, "unchecked")
        self.assertEqual(r.note, "not in Crossref")


class TestRetractionWatchSignals(unittest.TestCase):
    def rec(self, kind, nature):
        return RWRecord(
            kind=kind, nature=nature, date="1/1/2020", journal="J", reason="Fraud",
            retraction_doi="10.1/notice", record_id="1",
        )

    def test_pmid_only_citation_matched_in_rw(self):
        rw = FakeRW(by_pmid={"999": [self.rec("retraction", "Retraction")]})
        r = check_one(Citation(raw="999", pmid="999"), FakeClient({}), rw)
        self.assertEqual(r.status, "retracted")

    def test_rw_catches_what_crossref_misses(self):
        client = FakeClient({"10.1/x": found(title=["A perfectly normal title"])})
        rw = FakeRW(by_doi={"10.1/x": [self.rec("retraction", "Retraction")]})
        r = check_one(Citation(raw="10.1/x", doi="10.1/x"), client, rw)
        self.assertEqual(r.status, "retracted")
        self.assertEqual([s.source for s in r.signals], ["retraction-watch-db"])

    def test_reinstatement_is_recorded_as_a_note(self):
        rw = FakeRW(
            by_doi={
                "10.1/x": [
                    self.rec("retraction", "Retraction"),
                    self.rec("reinstatement", "Reinstatement"),
                ]
            }
        )
        client = FakeClient({"10.1/x": found(title=["A paper"])})
        r = check_one(Citation(raw="10.1/x", doi="10.1/x"), client, rw)
        self.assertEqual(r.status, "retracted")
        self.assertIn("reinstatement", r.note.lower())


class TestUnchecked(unittest.TestCase):
    def test_bare_arxiv_id_is_unchecked_not_clean(self):
        r = check_one(Citation(raw="arXiv:1706.03762", arxiv="1706.03762"), FakeClient({}), None)
        self.assertEqual(r.status, "unchecked")

    def test_pmid_with_no_rw_database_is_unchecked(self):
        r = check_one(Citation(raw="123", pmid="123"), FakeClient({}), None)
        self.assertEqual(r.status, "unchecked")


class TestExitCodes(unittest.TestCase):
    def result(self, status):
        r = check_one(Citation(raw="x", doi="10.1/x"), FakeClient({"10.1/x": found()}), None)
        r.status = status
        return r

    def test_clean_is_zero(self):
        self.assertEqual(exit_code([self.result("clean")]), 0)

    def test_retraction_is_one(self):
        self.assertEqual(
            exit_code([self.result("clean"), self.result("concern"), self.result("retracted")]), 1
        )

    def test_concern_only_is_two(self):
        self.assertEqual(exit_code([self.result("clean"), self.result("concern")]), 2)

    def test_correction_only_is_two(self):
        self.assertEqual(exit_code([self.result("correction")]), 2)

    def test_notice_alone_is_zero(self):
        self.assertEqual(exit_code([self.result("notice")]), 0)

    def test_unchecked_is_zero_unless_strict(self):
        results = [self.result("unchecked")]
        self.assertEqual(exit_code(results), 0)
        self.assertEqual(exit_code(results, strict=True), 3)

    def test_strict_reports_tool_error_over_retraction(self):
        results = [self.result("unchecked"), self.result("retracted")]
        self.assertEqual(exit_code(results, strict=True), 3)


if __name__ == "__main__":
    unittest.main()
