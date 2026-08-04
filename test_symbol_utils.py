import os
import tempfile
import unittest

from trading_common import normalize_symbol, get_db_path, resolve_symbol


class SymbolUtilsTest(unittest.TestCase):
    def test_normalize_symbol(self):
        self.assertEqual(normalize_symbol(" BTCUSDT "), "btcusdt")
        self.assertEqual(normalize_symbol("ETH/USDT"), "ethusdt")
        self.assertEqual(normalize_symbol("  SolUsDt  "), "solusdt")

    def test_get_db_path(self):
        base_dir = tempfile.gettempdir()
        path = get_db_path("BTCUSDT", base_dir)
        self.assertTrue(path.endswith("trading_data_btcusdt.duckdb"))

    def test_resolve_symbol_prefers_argument(self):
        self.assertEqual(resolve_symbol("ETHUSDT", default="btcusdt"), "ethusdt")
        self.assertEqual(resolve_symbol(None, default="btcusdt"), "btcusdt")


if __name__ == "__main__":
    unittest.main()
