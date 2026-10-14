"""Offline tests: run with `python -m unittest discover tests`."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import add_refs as ar  # noqa: E402

NATURE_CSL = {
    "type": "article-journal",
    "title": "Pervasive gene content variation in mammalian genomes",
    "author": [{"family": "García-López", "given": "Ana"}, {"family": "Smith", "given": "J"}],
    "container-title": "Nature",
    "issued": {"date-parts": [[2018, 7]]},
}
NATCOMMUN_CSL = {
    "type": "article-journal",
    "title": "The role of TP53 duplication in elephant cancer resistance",
    "author": [{"family": "Berg", "non-dropping-particle": "van der", "given": "K"}],
    "container-title": "Nature Communications",
    "container-title-short": "Nat. Commun.",
    "published-online": {"date-parts": [[2023, 2, 1]]},
}
PNAS_CSL = {
    "type": "article-journal",
    "title": "A new analysis of chromatin aging in bats",
    "author": [{"literal": "Bat1K Consortium"}],
    "container-title": "Proceedings of the National Academy of Sciences",
    "issued": {"date-parts": [[2021]]},
}
BIORXIV_CSL = {
    "type": "posted-content",
    "title": "Myotis genomes reveal DNA repair adaptations",
    "author": [{"family": "Vazquez", "given": "J M"}],
    "institution": [{"name": "bioRxiv"}],
    "posted": {"date-parts": [[2024, 5, 3]]},
}
RAW_BIB = (
    " @article{Garc_a_L_pez_2018, title={Pervasive gene content variation}, "
    "volume={559}, DOI={10.1038/s41586-018-0279-9}, journal={Nature}, year={2018} }"
)


class TestKeys(unittest.TestCase):
    def test_patterns(self):
        self.assertEqual(ar.make_key(NATURE_CSL, "10.1038/x"), "GarciaLopez-2018-Nature-Pervasive")
        self.assertEqual(ar.make_key(NATCOMMUN_CSL, "10.1038/y"), "VanDerBerg-2023-NatCommun-TP53")
        self.assertEqual(ar.make_key(PNAS_CSL, "10.1073/z"), "Bat1K-2021-PNAS-Chromatin")
        self.assertEqual(ar.make_key(BIORXIV_CSL, "10.1101/2024.05.01.1"), "Vazquez-2024-bioRxiv-Myotis")

    def test_collision_suffix(self):
        self.assertEqual(ar.unique_key("A-2020-Cell-X", {"A-2020-Cell-X"}), "A-2020-Cell-Xb")

    def test_set_citekey_and_pretty(self):
        out = ar.set_citekey(RAW_BIB, "GarciaLopez-2018-Nature-Pervasive", "10.1038/s41586-018-0279-9")
        self.assertTrue(out.startswith("@article{GarciaLopez-2018-Nature-Pervasive,\n"))
        self.assertIn("\tDOI = {10.1038/s41586-018-0279-9},", out)
        self.assertTrue(out.endswith("\tyear = {2018}\n}\n"))


class TestExtraction(unittest.TestCase):
    def test_teams_html(self):
        msg = (
            '<p>Check this <a href="https://www.nature.com/articles/s41586-018-0279-9">'
            "https://www.nature.com/articles/s41586-018-0279-9</a> and doi 10.7554/eLife.83409.</p>"
            '<p>preprint https://www.biorxiv.org/content/10.1101/2024.05.01.591234v2.full</p>'
            '<img src="https://statics.teams.cdn.office.net/evergreen-assets/x.png">'
            "<p>https://pubmed.ncbi.nlm.nih.gov/12345678/ and https://arxiv.org/abs/2401.01234</p>"
        )
        dois, urls = ar.extract_candidates(msg)
        self.assertEqual(
            dois,
            [
                "10.1038/s41586-018-0279-9",
                "10.1101/2024.05.01.591234",
                "10.48550/arxiv.2401.01234",
                "10.7554/elife.83409",
            ],
        )
        self.assertEqual(urls, ["https://pubmed.ncbi.nlm.nih.gov/12345678/"])


class TestEndToEnd(unittest.TestCase):
    def test_process_writes_and_dedupes(self):
        with tempfile.TemporaryDirectory() as d:
            refs = Path(d) / "refs"
            with mock.patch.object(ar, "REFS_DIR", refs), mock.patch.object(
                ar, "LIBRARY", Path(d) / "library.bib"
            ), mock.patch.object(ar, "fetch_csl", return_value=NATURE_CSL), mock.patch.object(
                ar, "fetch_bibtex", return_value=RAW_BIB
            ), mock.patch.object(ar.time, "sleep"):
                r1 = ar.process("https://www.nature.com/articles/s41586-018-0279-9")
                r2 = ar.process("doi: 10.1038/S41586-018-0279-9")
            self.assertEqual(r1.added, [("GarciaLopez-2018-Nature-Pervasive", "10.1038/s41586-018-0279-9")])
            self.assertTrue((refs / "GarciaLopez-2018-Nature-Pervasive.bib").exists())
            self.assertEqual(r2.added, [])
            self.assertEqual(len(r2.duplicates), 1)
            lib = (Path(d) / "library.bib").read_text()
            self.assertEqual(lib.count("@article{"), 1)


if __name__ == "__main__":
    unittest.main()
