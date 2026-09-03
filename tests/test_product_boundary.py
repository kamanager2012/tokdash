import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ProductBoundaryTests(unittest.TestCase):
    def test_pruned_scanner_implementations_are_physically_absent(self):
        source = (ROOT / "usage.30s.py").read_text(encoding="utf-8")
        forbidden = (
            "scan_mimocode",
            "scan_prime_agent",
            "scan_openclaw",
            "scan_qwencode",
            "scan_qwenwork_quota",
            "scan_zed_quota",
            "scan_sub2api_quota",
        )
        for name in forbidden:
            self.assertIsNone(
                re.search(rf"^def\\s+{re.escape(name)}\\(", source, flags=re.MULTILINE),
                name,
            )

    def test_pruned_frontend_keys_do_not_reenter_fallback_lists(self):
        trend = (ROOT / "src/components/TrendChart.tsx").read_text(encoding="utf-8")
        overview = (ROOT / "src/components/OverviewCards.tsx").read_text(encoding="utf-8")

        for key in ("openclaw", "mimocode", "qwencode", "prime_agent"):
            self.assertNotIn(f"'{key}'", trend)
        self.assertNotIn("'qwencode'", overview)

    def test_canonical_provider_and_grok_bot_boundaries_remain(self):
        source = (ROOT / "usage.30s.py").read_text(encoding="utf-8")
        cards = (ROOT / "src/components/ToolCardList.tsx").read_text(encoding="utf-8")

        self.assertRegex(source, r"^def\\s+scan_zai_quota\\(", "Z.AI must remain a provider quota integration")
        self.assertIn("grok_bot:", cards, "Grok Bot must remain an independent first-class product")


if __name__ == "__main__":
    unittest.main()
