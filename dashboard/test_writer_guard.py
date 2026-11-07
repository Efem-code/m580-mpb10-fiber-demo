"""The dashboard must step back when something else writes the command block.

Run with: python test_writer_guard.py
Uses a fake PLC on port 5029 and a thread standing in for the TV. Doesn't touch settings.json.
"""
import os
import tempfile
import threading
import time
import unittest

import server
from modbus import DataStore, ModbusClient, ModbusServer

PORT = 5029
BASE, CMD = 1000, 1100
TV_SPEED, DASH_SPEED = 1200, 900


class Tv:
    """Writes the command block every 200 ms, like the TV app."""

    def __init__(self, run=True):
        self.on, self.run, self.hb = True, run, 20000
        self.client = ModbusClient("127.0.0.1", PORT, 255)
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while self.on:
            self.hb += 1
            self.client.write_registers(CMD, [self.hb, int(self.run), TV_SPEED, 50, 50])
            time.sleep(0.2)
        self.client.close()

    def stop(self):
        self.on = False


class Guard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.SETTINGS = os.path.join(tempfile.mkdtemp(), "settings.json")
        cls.store = DataStore()
        cls.modbus = ModbusServer("127.0.0.1", PORT, cls.store)
        cls.modbus.start()
        cls.ticking = True
        threading.Thread(target=cls.plc_tick, daemon=True).start()

    @classmethod
    def plc_tick(cls):
        hb = 0
        while cls.ticking:
            hb = (hb + 1) & 0x7FFF
            regs = [0] * 32
            regs[0], regs[1], regs[20], regs[29] = hb, 3, 1 | 2 | 16 | 32, 1 | 16 | 1 << 8 | 1 << 9
            cls.store.write("holding", BASE, regs)
            time.sleep(0.1)

    def setUp(self):
        self.store.write("holding", CMD, [0, 0, 0, 0, 0])
        cfg = server.load_settings()
        cfg.update({"mode": "live", "plc_host": "127.0.0.1", "plc_port": PORT, "unit_id": 255,
                    "allow_control": True, "speed": DASH_SPEED})
        self.engine = server.Engine(cfg)
        self.engine.viewers = 1
        self.tv = None
        self.addCleanup(self.cleanup)

    def cleanup(self):
        if self.tv:
            self.tv.stop()
        self.engine.cfg["allow_control"] = False
        time.sleep(0.3)

    def cmd(self):
        return self.store.read("holding", CMD, 5)

    def wait(self, test, seconds=6):
        end = time.time() + seconds
        while time.time() < end:
            if test():
                return True
            time.sleep(0.05)
        return False

    def speeds_during(self, seconds):
        seen, end = set(), time.time() + seconds
        while time.time() < end:
            seen.add(self.cmd()[2])
            time.sleep(0.04)
        return seen

    def dash_writing(self):
        return self.wait(lambda: self.engine.state.get("control", {}).get("writing") and self.cmd()[0] not in (0,))

    def test_alone_it_controls_the_drive(self):
        self.assertTrue(self.dash_writing())
        self.assertIsNone(self.engine.drive("run"))
        self.assertTrue(self.wait(lambda: self.cmd()[1] & 1))
        self.assertFalse(self.engine.writer_blocked)

    def test_steps_back_when_the_tv_starts_and_drops_its_own_run(self):
        self.assertTrue(self.dash_writing())
        self.assertIsNone(self.engine.drive("run"))
        self.assertTrue(self.wait(lambda: self.cmd()[1] & 1))
        self.tv = Tv()
        self.assertTrue(self.wait(lambda: self.engine.writer_blocked))
        self.assertFalse(self.engine.cmd["run"])
        time.sleep(0.5)
        self.assertEqual(self.speeds_during(2), {TV_SPEED}, "the two writers overwrote each other")
        self.assertTrue(self.cmd()[1] & 1, "the TV's run bit must stay on")

    def test_start_is_refused_but_stop_gets_through(self):
        self.assertTrue(self.dash_writing())
        self.tv = Tv()
        self.assertTrue(self.wait(lambda: self.engine.writer_blocked))
        self.assertIn("TV", self.engine.drive("run"))
        self.assertIn("TV", self.engine.drive("jog", {"rev": False}))
        self.assertIsNone(self.engine.drive("stop"))
        self.assertTrue(self.wait(lambda: self.cmd()[1] & 2, 1.5), "STOP never reached the PLC")

    def test_starting_second_never_writes(self):
        self.tv = Tv()
        self.engine.cfg["allow_control"] = False
        self.engine.plc = None
        time.sleep(0.6)
        self.engine.cfg["allow_control"] = True
        self.assertEqual(self.speeds_during(3), {TV_SPEED})
        self.assertTrue(self.engine.writer_blocked)

    def test_no_restart_when_the_tv_goes_away(self):
        self.assertTrue(self.dash_writing())
        self.assertIsNone(self.engine.drive("run"))
        self.tv = Tv()
        self.assertTrue(self.wait(lambda: self.engine.writer_blocked))
        self.tv.stop()
        self.assertTrue(self.wait(lambda: not self.engine.writer_blocked))
        self.assertTrue(self.wait(lambda: self.cmd()[2] != TV_SPEED))
        self.assertFalse(self.cmd()[1] & 1, "the run must need a fresh Start")
        self.assertIsNone(self.engine.drive("run"))
        self.assertTrue(self.wait(lambda: self.cmd()[1] & 1))


if __name__ == "__main__":
    unittest.main()
