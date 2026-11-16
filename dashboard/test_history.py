"""The historian: S07's logic (HistBuf), copying it off (Reader), over a real Modbus link to the stand-in.

Run with: python test_history.py      Uses a temporary folder for the CSV files and port 5031.
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest

import history
from modbus import ModbusClient

HERE = os.path.dirname(os.path.abspath(__file__))


def sample(t):
    return [1.0, 2.0, 3.0], 40.0, 1200, history.HistBuf.flags_of(warn=True, running=True)


class Buffer(unittest.TestCase):
    def test_minute_keeps_the_highest_and_the_flags(self):
        hb = history.HistBuf(period=60)
        t = time.mktime((2026, 11, 16, 10, 0, 0, 0, 0, -1))
        hb.update(t, [1, 1, 1], 30, 100, 0)                         # starts the minute
        hb.update(t + 20, [4, 2, 1], 31, 200, history.HistBuf.flags_of(shock=True))
        hb.update(t + 40, None, None, 300, history.HistBuf.flags_of(lost=True))
        hb.update(t + 60, [2, 1, 1], 32, 400, 0)
        self.assertEqual(hb.count, 1)
        w = hb.buf[0]
        self.assertEqual(w[:2], [0x1116, 0x1001])
        self.assertEqual(w[2:6], [400, 200, 100, 320])
        self.assertEqual(w[6], 400)
        self.assertEqual(w[7] & 0xFF, 8 | 16)

    def test_no_data_in_a_minute(self):
        hb = history.HistBuf(period=60)
        hb.update(0, None, None, 0, 16)
        hb.update(61, None, None, 0, 16)
        self.assertEqual(hb.buf[0][2:6], [history.NA] * 4)

    def test_ring_wraps_and_reader_gets_everything_in_order(self):
        hb = history.HistBuf(period=60)
        now = time.time()
        hb.backfill(now - 7200, sample)                           # fills all 4320 slots
        for i in range(100):                                     # and 100 more: the ring has wrapped
            hb._write(now - 7200 + 60 * (i + 1), [0.5, 0.5, i / 10], 41, -900, 0)
        self.assertEqual((hb.count, hb.head), (4320, 100))
        r = history.Reader()
        n = r.poll(history.BufClient(hb), now)
        self.assertEqual(n, 4320)
        _, rows = r.query(80)
        times = [x[0] for x in rows]
        self.assertEqual(times, sorted(times))
        self.assertEqual(rows[-1][3], 9.9)                        # the newest is last
        self.assertEqual(rows[-1][5], -900)                       # speed is signed
        # one more sample: only that one is copied
        hb._write(now + 60, [7, 7, 7], 42, 0, 2)
        self.assertEqual(r.poll(history.BufClient(hb), now + 60), 1)
        self.assertEqual(r.query(80)[1][-1][1:4], [7.0, 7.0, 7.0])

    def test_clear(self):
        hb = history.HistBuf()
        hb.backfill(time.time(), sample)
        r = history.Reader()
        r.clear_plc(history.BufClient(hb))
        self.assertEqual(hb.count, 0)
        self.assertEqual(r.poll(history.BufClient(hb)), 0)

    def test_year_end(self):
        newest = [0x0101, 0x0010, 0, 0, 0, 0, 0, 0]
        old = [0x1231, 0x2350, 0, 0, 0, 0, 0, 0]
        y = history.bcd(2027)
        self.assertEqual(time.localtime(history.sample_time(newest, y, 101, 0)).tm_year, 2027)
        self.assertEqual(time.localtime(history.sample_time(old, y, 101, 0)).tm_year, 2026)
        self.assertIsNone(history.sample_time(old, 0, 101, 0))   # clock never set


class OverModbus(unittest.TestCase):
    """The stand-in PLC, the Reader and the CSV files, like the dashboard in live mode."""

    @classmethod
    def setUpClass(cls):
        cls.proc = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_m580.py"), "5031", "--hist-period=1"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=HERE)
        time.sleep(2.5)
        cls.client = ModbusClient("127.0.0.1", 5031, 255, timeout=1.0)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.proc.kill()

    def test_copy_to_files_then_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            r = history.Reader(folder)
            t0 = time.time()
            n = r.poll(self.client)
            took = time.time() - t0
            self.assertGreaterEqual(n, 4300)
            self.assertLess(took, 60)
            print(f"\n  full copy of {n} samples over Modbus: {took:.1f} s")
            files = [f for f in os.listdir(folder) if f.endswith(".csv")]
            self.assertTrue(files)
            time.sleep(2.2)                                       # the stand-in samples every second here
            self.assertGreaterEqual(r.poll(self.client), 1)
            with open(os.path.join(folder, sorted(files)[-1]), encoding="utf-8") as f:
                head = f.readline().strip().split(",")
            self.assertEqual(head, history.CSV_HEAD)
            # a dashboard restart reads the files back, and doesn't write anything twice
            total = 0
            for name in os.listdir(folder):
                with open(os.path.join(folder, name), encoding="utf-8") as f:
                    total += sum(1 for _ in f) - 1
            r2 = history.Reader(folder)
            self.assertGreaterEqual(len(r2.rows), total - 5)
            before = len(r2.rows)
            added = r2.poll(self.client, full=True)
            self.assertLessEqual(added, 3)
            self.assertLessEqual(len(r2.rows) - before, 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
