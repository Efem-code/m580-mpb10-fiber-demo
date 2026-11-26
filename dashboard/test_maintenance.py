"""Predictive maintenance (maintenance.py) on made-up histories whose answer we know.

Run with: python test_maintenance.py
"""
import os
import random
import tempfile
import time
import unittest

import history
import maintenance as m

CFG = {"zone_bc": 2.8, "zone_cd": 4.5, "temp_warn": 70.0}
NOW = time.mktime((2026, 11, 26, 18, 0, 0, 0, 0, -1))


def shift_rows(days, vib, speed=lambda day, hour: 1200, temp=lambda day, hour, rpm: 45.0, shock=lambda day: 0.0, seed=1):
    """Day shifts 8:00-16:00 every day for `days` days up to NOW. vib(day, rpm) -> (x, y, z)."""
    rnd = random.Random(seed)
    rows = []
    start = NOW - days * 86400
    for d in range(days):
        t0 = time.mktime(time.localtime(start + d * 86400)[:3] + (8, 0, 0, 0, 0, -1))
        for k in range(8 * 60):
            t = int(t0 + 60 * k)
            if t > NOW:
                break
            rpm = speed(d, 8 + k / 60)
            x, y, z = (v * (1 + rnd.uniform(-0.05, 0.05)) for v in vib(d, rpm))
            f = m.RUNNING | (m.SHOCK if rnd.random() < shock(d) else 0)
            rows.append([t, round(x, 3), round(y, 3), round(z, 3), round(temp(d, 8 + k / 60, rpm), 1), rpm, f])
    return rows


class Learning(unittest.TestCase):
    def test_one_day_is_still_learning(self):
        o = m.analyse(shift_rows(1, lambda d, r: (1, 1, 1)), CFG, NOW)
        self.assertEqual(o["status"], "learning")
        self.assertIn("Learning", o["findings"][0])

    def test_no_running_at_all(self):
        rows = [[int(NOW - 60 * i), 0.1, 0.1, 0.1, 24.0, 0, 0] for i in range(3000)]
        o = m.analyse(rows, CFG, NOW)
        self.assertEqual(o["status"], "learning")
        self.assertEqual(o["running_hours"], 0)


class Healthy(unittest.TestCase):
    def test_steady_machine_is_ok(self):
        o = m.analyse(shift_rows(21, lambda d, r: (0.9, 0.7, 1.1)), CFG, NOW)
        self.assertEqual(o["status"], "ok")
        for a in o["axes"].values():
            self.assertLess(abs(a["change_pct"]), 10)
            self.assertTrue(a["days_to_warning"] is None or a["days_to_warning"] > 365)
        self.assertIn("close to normal", o["findings"][0])

    def test_faster_days_are_not_wear(self):
        # vibration follows speed; later days run faster. Against normal at the same speed, nothing changed.
        o = m.analyse(shift_rows(21, lambda d, r: (r / 1000, r / 1200, r / 900),
                                 speed=lambda d, h: 600 if d < 10 or h < 10 else 1500), CFG, NOW)
        self.assertEqual(o["status"], "ok")
        for a in o["axes"].values():
            self.assertLess(abs(a["change_pct"]), 10)
        self.assertEqual(o["series"][-1]["minutes"], 8 * 60)      # the 1500 rpm minutes count too
        self.assertEqual(len(o["bands"]), 2)


class Wearing(unittest.TestCase):
    def test_linear_rise_gives_the_right_days(self):
        # Z: 1.0 mm/s, rising 0.05 mm/s a day from day 7. Today (day 29) 2.1 -> warning 2.8 in 14 days.
        rise = lambda d, r: (0.8, 0.6, 1.0 + 0.05 * max(0, d - 7))   # noqa: E731
        o = m.analyse(shift_rows(30, rise), CFG, NOW)
        z = o["axes"]["z"]
        self.assertAlmostEqual(z["days_to_warning"], 14, delta=2)
        self.assertAlmostEqual(z["days_to_alarm"], 48, delta=4)
        self.assertAlmostEqual(z["per_week"], 0.35, delta=0.03)
        self.assertEqual(z["status"], "act")              # doubled: +110 %
        self.assertEqual(o["axes"]["x"]["status"], "ok")
        self.assertTrue(o["findings"][0].startswith("Z axis"))
        self.assertEqual(o["confidence"], "good")

    def test_over_the_limit_says_now(self):
        o = m.analyse(shift_rows(20, lambda d, r: (0.8, 0.6, 1.0 if d < 10 else 3.0)), CFG, NOW)
        self.assertEqual(o["axes"]["z"]["days_to_warning"], 0)
        self.assertEqual(o["status"], "act")
        self.assertIn("at the warning limit", o["findings"][0])

    def test_hotter_for_the_same_speed(self):
        o = m.analyse(shift_rows(20, lambda d, r: (1, 1, 1), temp=lambda d, h, rpm: 45 + max(0, d - 6) * 1.0),
                      CFG, NOW)
        self.assertIn(o["temp"]["status"], ("watch", "act"))
        self.assertAlmostEqual(o["temp"]["per_week"], 7, delta=1)
        self.assertTrue(any("Temperature" in f for f in o["findings"]))

    def test_more_shocks(self):
        o = m.analyse(shift_rows(20, lambda d, r: (1, 1, 1), shock=lambda d: 0.002 if d < 13 else 0.05), CFG, NOW)
        self.assertEqual(o["shocks"]["status"], "watch")
        self.assertTrue(any("Shocks" in f for f in o["findings"]))

    def test_maintenance_done_starts_a_new_normal(self):
        rows = shift_rows(30, lambda d, r: (0.8, 0.6, 1.0 if d < 15 else 2.0))
        before = m.analyse(rows, CFG, NOW)
        self.assertEqual(before["status"], "act")
        after = m.analyse(rows, CFG, NOW, since=NOW - 10 * 86400)   # serviced, but it still runs at 2.0
        self.assertEqual(after["axes"]["z"]["status"], "ok")      # 2.0 is the new normal
        self.assertLess(abs(after["axes"]["z"]["change_pct"]), 10)


class Sources(unittest.TestCase):
    def test_day_files_round_trip(self):
        rows = shift_rows(3, lambda d, r: (1.1, 1.2, 1.3))
        with tempfile.TemporaryDirectory() as tmp:
            by_day = {}
            for r in rows:
                by_day.setdefault(m.day_of(r[0]), []).append(r)
            for d, rs in by_day.items():
                with open(os.path.join(tmp, d + ".csv"), "w", newline="", encoding="utf-8") as f:
                    f.write(history.to_csv(rs))
            fr = m.FileRows(tmp)
            back = fr.rows()
            self.assertEqual(len(back), len(rows))
            self.assertEqual(back[0], rows[0])
            self.assertIs(fr.rows()[0], back[0])              # cached until a file changes

    def test_demo_history_shows_wear(self):
        o = m.analyse(m.demo_rows(NOW), CFG, NOW)
        self.assertEqual(o["axes"]["z"]["status"], "act")
        self.assertTrue(10 < o["axes"]["z"]["days_to_warning"] < 40)
        self.assertEqual(o["axes"]["y"]["status"], "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
