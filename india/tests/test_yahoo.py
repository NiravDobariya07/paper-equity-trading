import datetime
import io
import json
import os
import unittest
import urllib.error

from india import yahoo

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return json.load(fh)


def http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(b""))


class Yahoo(unittest.TestCase):
    def setUp(self):
        self.sleep, yahoo.SLEEP = yahoo.SLEEP, lambda s: None

    def tearDown(self):
        yahoo.SLEEP = self.sleep

    def test_parse_chart_is_one_ist_session(self):
        ch = yahoo.parse_chart(fixture("RELIANCE-1d-5m.json"))
        self.assertGreater(len(ch.bars), 60)
        self.assertEqual(len({b.t.date() for b in ch.bars}), 1)
        self.assertEqual((ch.bars[0].t.hour, ch.bars[0].t.minute), (9, 15))
        self.assertEqual(ch.bars[0].t.utcoffset(), datetime.timedelta(hours=5, minutes=30))
        for b in ch.bars:
            self.assertTrue(all(isinstance(x, float) for x in (b.o, b.h, b.l, b.c, b.v)))
        self.assertGreater(ch.price, 0)
        self.assertEqual(ch.price_time.utcoffset(), datetime.timedelta(hours=5, minutes=30))

    def test_rows_with_none_are_dropped(self):
        doc = fixture("RELIANCE-1d-5m.json")
        q = doc["chart"]["result"][0]["indicators"]["quote"][0]
        n = len(yahoo.parse_chart(doc).bars)
        self.assertIsNotNone(q["close"][1])
        q["close"][1] = None
        self.assertEqual(len(yahoo.parse_chart(doc).bars), n - 1)

    def test_404_is_nodata(self):
        def get(url):
            raise http_error(404)
        with self.assertRaises(yahoo.NoData):
            yahoo.fetch_chart("ZOMATO", "1d", "5m", get=get)

    def test_empty_result_is_nodata(self):
        with self.assertRaises(yahoo.NoData):
            yahoo.fetch_chart("X", "1d", "5m", get=lambda url: {"chart": {"result": None, "error": {"code": "Not Found"}}})

    def test_429_retries_once(self):
        calls = []

        def get(url):
            calls.append(url)
            if len(calls) == 1:
                raise http_error(429)
            return fixture("RELIANCE-1d-5m.json")
        ch = yahoo.fetch_chart("RELIANCE", "1d", "5m", get=get)
        self.assertEqual(len(calls), 2)
        self.assertTrue(ch.bars)

    def test_symbol_is_quoted(self):
        urls = []

        def get(url):
            urls.append(url)
            return fixture("RELIANCE-1d-5m.json")
        yahoo.fetch_chart("M&M", "1d", "5m", get=get)
        self.assertIn("/M%26M.NS?", urls[0])

    def test_spark_batches_of_20(self):
        calls = []

        def get(url):
            calls.append(url)
            return fixture("spark-1d-5m.json")
        out = yahoo.fetch_spark(["S%d" % i for i in range(45)], "1d", "5m", get=get)
        self.assertEqual(len(calls), 3)
        # the fixture answers RELIANCE and TCS only; names map back without the suffix
        self.assertEqual(sorted(k for k in out if out[k]), [])
        out = yahoo.fetch_spark(["RELIANCE", "TCS"], "1d", "5m", get=lambda u: fixture("spark-1d-5m.json"))
        self.assertTrue(out["RELIANCE"])
        t, c = out["TCS"][0]
        self.assertEqual((t.hour, t.minute), (9, 15))


class SparkMeta(unittest.TestCase):
    def setUp(self):
        self._sleep, yahoo.SLEEP = yahoo.SLEEP, lambda s: None

    def tearDown(self):
        yahoo.SLEEP = self._sleep

    def test_prev_close_and_day_high(self):
        out = yahoo.fetch_spark_meta(["RELIANCE", "TCS", "GONE"], get=lambda u: fixture("spark-1d-5m.json"))
        self.assertEqual(out["RELIANCE"], {"prev_close": 1190.0, "day_high": 1204.5})
        self.assertEqual(out["TCS"], {"prev_close": 2530.0, "day_high": 2549.7})
        self.assertNotIn("GONE", out)
