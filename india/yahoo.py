"""Free Yahoo Finance chart data for NSE symbols (<SYM>.NS), in IST."""

import collections
import itertools
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from india import ist

UA = "Mozilla/5.0"
HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")
SPARK_MAX = 20      # Yahoo's spark answers 400 above 20 symbols
PAUSE = 0.3         # seconds between requests: be polite to free endpoints
BACKOFF = 5.0       # seconds before the one retry after a 429
SLEEP = time.sleep  # tests swap this out

Bar = collections.namedtuple("Bar", "t o h l c v")
Chart = collections.namedtuple("Chart", "bars price price_time")

_host = itertools.cycle(HOSTS)


class NoData(Exception):
    """Yahoo has nothing for this symbol (renamed, delisted, or no bars)."""


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def yahoo_symbol(sym):
    return urllib.parse.quote(sym + ".NS", safe=".")


def _call(path, get, sym):
    get = get or http_get
    for attempt in (0, 1):
        SLEEP(PAUSE)
        try:
            return get(next(_host) + path)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NoData(sym)
            if e.code == 429 and attempt == 0:
                SLEEP(BACKOFF)
                continue
            raise


def parse_chart(doc):
    res = ((doc or {}).get("chart") or {}).get("result")
    if not res or not res[0].get("timestamp"):
        raise NoData("no bars")
    r = res[0]
    q = r["indicators"]["quote"][0]
    bars = []
    for i, ts in enumerate(r["timestamp"]):
        row = [q[k][i] for k in ("open", "high", "low", "close", "volume")]
        if any(x is None for x in row):
            continue
        bars.append(Bar(ist.from_ts(ts), *(float(x) for x in row)))
    if not bars:
        raise NoData("no bars")
    meta = r.get("meta") or {}
    price = meta.get("regularMarketPrice") or bars[-1].c
    when = meta.get("regularMarketTime")
    return Chart(bars, float(price), ist.from_ts(when) if when else bars[-1].t)


def fetch_chart(sym, rng, interval, get=None):
    path = "/v8/finance/chart/%s?range=%s&interval=%s" % (yahoo_symbol(sym), rng, interval)
    return parse_chart(_call(path, get, sym))


def fetch_spark(syms, rng, interval, get=None):
    """{sym: [(bar start, close), ...]} for every symbol asked; [] when Yahoo has none."""
    out = {s: [] for s in syms}
    for k in range(0, len(syms), SPARK_MAX):
        batch = syms[k:k + SPARK_MAX]
        path = "/v7/finance/spark?symbols=%s&range=%s&interval=%s" % (
            ",".join(yahoo_symbol(s) for s in batch), rng, interval)
        try:
            doc = _call(path, get, ",".join(batch))
        except NoData:
            continue
        for item in ((doc or {}).get("spark") or {}).get("result") or []:
            sym = (item.get("symbol") or "")[:-3]
            resp = item.get("response") or []
            if sym not in out or not resp or not resp[0].get("timestamp"):
                continue
            closes = resp[0]["indicators"]["quote"][0]["close"]
            out[sym] = [(ist.from_ts(t), float(c)) for t, c in zip(resp[0]["timestamp"], closes)
                        if c is not None]
    return out
