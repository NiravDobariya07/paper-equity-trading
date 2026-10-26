import datetime
import unittest

from india import config, ist, money

D = datetime.date(2026, 10, 27)


class Money(unittest.TestCase):
    def setUp(self):
        self.cfg = config.defaults()
        self.rates = self.cfg["charges"]

    def test_charges_round_trip(self):
        # brokerage 20+20, STT 5.05, txn 1.19, SEBI 0.04, stamp 0.60, GST 18% of 41.23 = 7.42
        self.assertAlmostEqual(money.charges(20000, 20200, self.rates), 54.31, delta=0.02)

    def test_small_orders_pay_percentage_brokerage(self):
        # 0.1% of 5,000 = 5 per side, under the ₹20 cap
        got = money.charges(5000, 5000, self.rates)
        self.assertAlmostEqual(got, 10 + 1.25 + 0.30 + 0.01 + 0.15 + 0.18 * (10 + 0.30 + 0.01), delta=0.02)

    def test_size(self):
        self.assertEqual(money.size(500, 495, 20000, 200), 40)
        self.assertEqual(money.size(500, 499, 20000, 200), 40)   # cash caps it
        self.assertEqual(money.size(500, 490, 20000, 200), 20)   # max loss caps it
        self.assertEqual(money.size(500, 500, 20000, 200), 0)
        self.assertEqual(money.size(30000, 29900, 20000, 200), 0)

    def check(self, day, risk=200, charges=50, at="10:30"):
        return money.risk_check(risk, charges, day, self.cfg, ist.at(D, at))

    def test_risk_check(self):
        fresh = {"realized_net": 0.0, "open_risk": 0.0, "trades": 0}
        self.assertEqual(self.check(fresh), (True, "ok"))
        self.assertEqual(self.check(dict(fresh, trades=3)), (False, "trade limit"))
        self.assertEqual(self.check({"realized_net": -230.0, "open_risk": 200.0, "trades": 2}),
                         (False, "daily loss"))
        self.assertEqual(self.check({"realized_net": 100.0, "open_risk": 0.0, "trades": 1}), (True, "ok"))
        self.assertEqual(self.check(fresh, at="14:31"), (False, "after 14:30"))
        self.assertEqual(self.check(fresh, risk=0), (False, "zero shares"))
