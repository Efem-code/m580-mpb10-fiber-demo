"""Email alerts: who gets what, when, and that two screens sharing a PLC send each alert once.

Run with: python test_alerts.py      A stand-in mail server on 127.0.0.1:2526, a stand-in PLC on port 5035.
"""
import email
import os
import socketserver
import subprocess
import sys
import threading
import time
import unittest

import alerts
from modbus import ModbusClient

HERE = os.path.dirname(os.path.abspath(__file__))
INBOX = []


class Smtp(socketserver.StreamRequestHandler):
    """Just enough SMTP to take a message."""

    def handle(self):
        w = lambda s: self.wfile.write((s + "\r\n").encode())   # noqa: E731
        w("220 test")
        data = None
        while True:
            line = self.rfile.readline()
            if not line:
                return
            if data is not None:
                if line in (b".\r\n", b".\n"):
                    INBOX.append(email.message_from_bytes(b"".join(data)))
                    data = None
                    w("250 ok")
                else:
                    data.append(line[1:] if line.startswith(b"..") else line)
                continue
            cmd = line[:4].upper()
            if cmd == b"DATA":
                data = []
                w("354 go")
            elif cmd == b"QUIT":
                w("221 bye")
                return
            elif cmd == b"EHLO":
                w("250 test")
            else:
                w("250 ok")


CFG = dict(alerts.SETTINGS, alerts_on=True, smtp_host="127.0.0.1", smtp_port=2526, smtp_security="none",
           mail_to="a@example.com, 5195551234@txt.bell.ca", asset="Test motor", summary_hour=-1)


def state(seq, log, active=()):
    items = [{"id": n, "text": t, "prio": p, "state": 1 if n in active else 0} for n, t, p in
             ((7, "Vibration warning", 2), (8, "Vibration alarm", 1))]
    return {"mode": "live", "mpb": {"v": 4.8, "vx": 3.9, "vy": 4.8, "vz": 2.0, "temp": 41.0},
            "drive": {"state": "Operation enabled", "speed": 1200}, "alarms": {"seq": seq, "log": log, "items": items}}


def ev(seq, n, kind="raised"):
    name, prio = {7: ("Vibration warning", 2), 8: ("Vibration alarm", 1)}[n]
    return {"seq": seq, "type": kind, "id": n, "text": name, "prio": prio, "at": "2026-11-16 13:00:00"}


class Alerts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        socketserver.ThreadingTCPServer.allow_reuse_address = True
        cls.smtp = socketserver.ThreadingTCPServer(("127.0.0.1", 2526), Smtp)
        threading.Thread(target=cls.smtp.serve_forever, daemon=True).start()
        cls.plc = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_m580.py"), "5035", "--hist-empty"],
                                   cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)

    @classmethod
    def tearDownClass(cls):
        cls.smtp.shutdown()
        cls.plc.kill()

    def setUp(self):
        INBOX.clear()

    def test_test_email(self):
        a = alerts.Alerter(alerts.LAPTOP)
        self.assertIsNone(a.test(CFG, state(0, [])))
        self.assertEqual(len(INBOX), 1)
        self.assertIn("5195551234@txt.bell.ca", INBOX[0]["To"])

    def test_old_history_not_sent_then_critical_at_once_and_warnings_batched(self):
        a = alerts.Alerter(alerts.LAPTOP)
        rows = lambda: ([], 60)   # noqa: E731
        a.tick(CFG, state(5, [ev(5, 8), ev(4, 7)]), None, rows, now=1000)
        self.assertEqual(INBOX, [])                         # what was there at start
        a.tick(CFG, state(6, [ev(6, 7), ev(5, 8)]), None, rows, now=1010)
        self.assertEqual(len(INBOX), 1)                      # the first warning goes (nothing sent for 15 min)
        a.tick(CFG, state(7, [ev(7, 7, "cleared"), ev(6, 7)]), None, rows, now=1020)
        a.tick(CFG, state(8, [ev(8, 7), ev(7, 7, "cleared")]), None, rows, now=1030)
        self.assertEqual(len(INBOX), 1)                      # the next warning waits
        a.tick(CFG, state(9, [ev(9, 8), ev(8, 7)], active=(8,)), None, rows, now=1040)
        self.assertEqual(len(INBOX), 2)                      # a critical one goes straight away, with the waiting warning
        self.assertIn("ALARM on Test motor: Vibration alarm", INBOX[1]["Subject"])
        self.assertIn("Vibration warning raised", INBOX[1].get_payload())

    def test_two_screens_send_once(self):
        lap, tv = alerts.Alerter(alerts.LAPTOP), alerts.Alerter(alerts.TV)
        c1, c2 = ModbusClient("127.0.0.1", 5035, 255), ModbusClient("127.0.0.1", 5035, 255)
        c1.write_registers(alerts.CLAIM, [0, 0, 0])
        rows = lambda: ([], 60)   # noqa: E731
        for a, c in ((lap, c1), (tv, c2)):
            a.tick(CFG, state(20, []), c, rows, now=2000)
        for n in range(3):                                   # three alarms, each seen by both at about the same time
            st = state(21 + n, [ev(21 + n, 8)], active=(8,))
            th = [threading.Thread(target=a.tick, args=(CFG, st, c, rows, 2010 + n)) for a, c in ((lap, c1), (tv, c2))]
            [t.start() for t in th]
            [t.join() for t in th]
        self.assertEqual(len(INBOX), 3, [m["Subject"] for m in INBOX])
        self.assertEqual(c1.read_holding(alerts.CLAIM, 1)[0], 23)

    def test_shared_settings_go_through_the_plc(self):
        c1, c2 = ModbusClient("127.0.0.1", 5035, 255), ModbusClient("127.0.0.1", 5035, 255)
        c1.write_registers(alerts.MAIL_BASE, [0] * 6)
        laptop, tv = alerts.MailSync(), alerts.MailSync()
        mine = dict(CFG, smtp_host="smtp.gmail.com", mail_to="ops@example.com, 5195551234@txt.bell.ca", smtp_user="me@gmail.com")
        self.assertIsNone(laptop.tick(mine, c1, dirty=False))           # an empty PLC gets ours
        fresh_tv = dict(alerts.SETTINGS)
        got = tv.tick(fresh_tv, c2, dirty=False)
        self.assertEqual(got["mail_to"], mine["mail_to"])
        self.assertEqual(got["smtp_host"], "smtp.gmail.com")
        self.assertNotIn("smtp_user", got)                               # never through the PLC
        self.assertNotIn("smtp_password", got)
        fresh_tv.update(got, summary_hour=18)
        tv.tick(fresh_tv, c2, dirty=True)                                # the TV changes the summary hour
        laptop.next_read = 0
        self.assertEqual(laptop.tick(mine, c1, dirty=False)["summary_hour"], 18)
        with self.assertRaises(ValueError):
            alerts.check_text("mail_to", "x" * 201)

    def test_outbox_sends_what_nobody_sent(self):
        c = ModbusClient("127.0.0.1", 5035, 255)
        c.write_registers(alerts.CLAIM, [40, 1, 0])                      # sent up to event 40, then every screen went away
        now = time.time()
        at = lambda ago: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - ago))   # noqa: E731
        log = [dict(ev(44, 7), at=at(60)), dict(ev(43, 8), at=at(600)), dict(ev(42, 7, "cleared"), at=at(900)),
               dict(ev(41, 8), at=at(2 * 86400)), dict(ev(40, 8), at=at(3 * 86400))]
        a = alerts.Alerter(alerts.TV)
        a.tick(CFG, state(44, log), c, lambda: ([], 60), now=now)
        self.assertEqual(len(INBOX), 1)
        m = INBOX[0]
        self.assertIn("While no screen was sending", m["Subject"])
        self.assertIn("2 alarm events", m["Subject"])                    # 43 and 44: 41 is too old, 42 is a clear
        self.assertEqual(c.read_holding(alerts.CLAIM, 1)[0], 44)
        a2 = alerts.Alerter(alerts.LAPTOP)                               # the next screen to start owes nothing
        a2.tick(CFG, state(44, log), c, lambda: ([], 60), now=now)
        self.assertEqual(len(INBOX), 1)

    def test_daily_summary_once_between_two_screens(self):
        lap, tv = alerts.Alerter(alerts.LAPTOP), alerts.Alerter(alerts.TV)
        c1, c2 = ModbusClient("127.0.0.1", 5035, 255), ModbusClient("127.0.0.1", 5035, 255)
        c1.write_registers(alerts.CLAIM, [0, 0, 0])
        cfg = dict(CFG, summary_hour=0)
        rows = lambda: ([[1794850000, 1.0, 2.0, 3.0, 40.0, 1200, 32 | 1], [1794850060, 1.1, 2.1, 3.1, 40.1, 1200, 32]], 60)  # noqa: E731
        th = [threading.Thread(target=a.tick, args=(cfg, state(30, []), c, rows)) for a, c in ((lap, c1), (tv, c2))]
        [t.start() for t in th]
        [t.join() for t in th]
        self.assertEqual(len(INBOX), 1)
        m = INBOX[0]
        self.assertIn("Daily summary", m["Subject"])
        parts = [p for p in m.walk() if p.get_filename()]
        self.assertTrue(parts and parts[0].get_payload(decode=True).startswith(b"time,vib_x_mm_s"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
