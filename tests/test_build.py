import importlib.util, json, tempfile, unittest
from pathlib import Path

MODULE = Path(__file__).parents[1] / "src" / "build.py"
spec = importlib.util.spec_from_file_location("build", MODULE); build = importlib.util.module_from_spec(spec); spec.loader.exec_module(build)

class BuildTests(unittest.TestCase):
    def test_deduplicate_merges_sources(self):
        papers = [{"title":"A Paper!","doi":None,"arxiv_id":"1","sources":["arXiv"],"cited_by_count":0},{"title":"A Paper!","doi":None,"arxiv_id":"1","sources":["OpenAlex"],"cited_by_count":3}]
        merged = build.deduplicate(papers)
        self.assertEqual(len(merged), 1); self.assertEqual(merged[0]["sources"], ["OpenAlex", "arXiv"])
    def test_topic_match_respects_exclusion(self):
        topic={"keywords":["causal inference"],"exclude":["causal language model"]}
        self.assertTrue(build.matches_topic({"title":"Causal inference","abstract":""},topic))
        self.assertFalse(build.matches_topic({"title":"Causal language model","abstract":"causal inference"},topic))
    def test_fallback_is_complete(self):
        output=build.fallback_summary({"title":"Test","abstract":"A short abstract"})
        self.assertEqual(set(output), {"question","method","contribution","caveat","why_read"})

    def test_render_includes_refresh_and_saved_papers(self):
        topic={"id":"ope","name":"Off-Policy Evaluation"}
        paper={"title":"Test","authors":["A"],"published":"2026-07-29","url":"https://example.org","doi":"10.test/x","arxiv_id":None,"sources":["OpenAlex"],"summary":build.fallback_summary({"title":"Test","abstract":""})}
        page=build.render("2026-W31", [(topic,[paper])], "Weekly Paper Radar", "https://github.com/example/actions/workflows/weekly.yml")
        self.assertIn("Refresh now", page)
        self.assertIn("Save paper", page)
        self.assertIn("Saved papers", page)
        self.assertNotIn("研究问题", page)
if __name__ == "__main__": unittest.main()
