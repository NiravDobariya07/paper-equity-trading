import json
import os
import tempfile
import unittest

from india import config


class Config(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "config.json")

    def test_load_creates_config_with_defaults(self):
        cfg = config.load(self.path)
        self.assertTrue(os.path.exists(self.path))
        self.assertEqual(cfg["capital"], 20000)
        self.assertEqual(cfg["min_turnover"], 100000000)
        self.assertEqual(cfg["charges"]["brokerage_cap"], 20)
        self.assertEqual(cfg["holidays"], [])

    def test_load_fills_missing_keys(self):
        with open(self.path, "w") as fh:
            json.dump({"capital": 50000, "charges": {"gst_pct": 12}}, fh)
        cfg = config.load(self.path)
        self.assertEqual(cfg["capital"], 50000)
        self.assertEqual(cfg["max_trades_per_day"], 3)
        self.assertEqual(cfg["charges"]["gst_pct"], 12)
        self.assertEqual(cfg["charges"]["stt_sell_pct"], 0.025)

    def test_paths_are_absolute_from_workspace(self):
        self.assertTrue(os.path.isabs(config.ROOT))
        self.assertEqual(config.DATA, os.path.join(config.ROOT, "trader", "india"))
        self.assertTrue(os.path.isabs(config.load(self.path)["equity_list"]))
