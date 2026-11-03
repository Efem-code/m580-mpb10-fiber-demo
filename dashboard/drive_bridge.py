"""Drive bridge: lets Control Expert's PLC simulator run the real ATV930 (testing only).

The PLC simulator runs our program but can't scan EtherNet/IP devices, so this script does the
drive's half over Modbus TCP:

  simulator %MW1000 block (aDash)   [23] LFRD  [28] CMD  [30] ACC  [31] DEC   -> the ATV930
  the ATV930   ETA RFRD LCR OTR THR LFT                                        -> simulator %MW1200 (aBridge)

  python drive_bridge.py                          simulator on this PC, drive at 192.168.20.30
  python drive_bridge.py --drive 192.168.20.30 --max-rpm 300

Safety, on top of the E-stop on the drive's STO (which works whatever this script does):
  - it never writes to the drive until the program says it is in bridge mode (xSimBridge TRUE,
    aBridge[8] = 1) and the simulator's heartbeat is counting;
  - once it has been driving, it sends a ramp stop (CMD 16#0007, LFRD 0) whenever the simulator
    stops answering, its heartbeat freezes for 1 s, it leaves bridge mode, or this script is closed;
  - the speed reference is capped at --max-rpm (300 by default: raise it once the first run is good).
Set the drive's own communication-loss response too, so a crashed PC also stops the motor.
Don't run this while a real M580 is scanning the drive: one master at a time.
"""
import argparse
import signal
import time

from modbus import ModbusClient, ModbusError

# ATV930 Modbus addresses (Altivar logical addresses)
ETA, LCR, OTR, THR, RFRD, LFT = 3201, 3204, 3205, 3209, 8604, 7121
CMD, LFRD, ACC, DEC = 8501, 8602, 9001, 9002
STOP_CMD = 0x0007          # switched on, motor stops on its ramp

# the PLC program's blocks (offsets from their bases)
DASH_HB, DASH_LFRD, DASH_CMD, DASH_ACC, DASH_DEC = 0, 23, 28, 30, 31
BRIDGE_WORDS = 8           # aBridge[0..7] are ours; aBridge[8] is the PLC's "bridge mode" flag


def s16(v):
    return v - 0x10000 if v >= 0x8000 else v


def u16(v):
    return int(v) & 0xFFFF


class Bridge:
    def __init__(self, a):
        self.a = a
        self.sim = ModbusClient(a.sim, a.sim_port, a.sim_unit, timeout=0.5)
        self.drv = ModbusClient(a.drive, a.drive_port, a.drive_unit, timeout=0.5)
        self.hb = 0
        self.armed = False          # we have driven the motor this session
        self.latched = False        # we stopped it; stays until the program itself stops the run
        self.sim_hb, self.sim_hb_t = None, 0.0
        self.ramps = None           # last ACC/DEC written (written only on change)
        self.last_print = 0.0
        self.note = ""

    # ---- one cycle
    def step(self):
        now = time.monotonic()
        dash, mode = self.read_sim()
        if dash is not None:
            if dash[DASH_HB] != self.sim_hb:
                self.sim_hb, self.sim_hb_t = dash[DASH_HB], now
        sim_alive = dash is not None and now - self.sim_hb_t < 1.0
        in_control = sim_alive and mode == 1

        drive = self.read_drive()
        asks_run = dash is not None and (u16(dash[DASH_CMD]) & 0x000F) == 0x000F
        if in_control and drive is not None and self.latched and asks_run:
            # no restart by surprise: after a bridge stop the program must stop the run first
            self.stop_drive()
            self.note = "STOPPED: stop the run in the program (Stop, or xLocalRun FALSE), then start again"
        elif in_control and drive is not None:
            self.write_drive(dash)
            self.armed, self.latched = True, False
            self.note = "driving"
        elif self.armed:
            self.latched = True
            why = ("simulator not answering" if dash is None else "simulator heartbeat stopped" if not sim_alive
                   else "program left bridge mode (xSimBridge FALSE)")
            if drive is not None and self.stop_drive():
                self.note = f"STOPPED: {why}"
            else:
                self.note = f"{why}; drive not answering either"
        else:
            self.note = ("waiting: simulator not answering" if dash is None else
                         "waiting: simulator heartbeat not counting (is it in RUN?)" if not sim_alive else
                         "waiting: set xSimBridge TRUE in the program" if mode != 1 else
                         "waiting: drive not answering")

        self.write_sim(drive)
        self.report(dash, drive, now)

    def read_sim(self):
        try:
            dash = self.sim.read_holding(self.a.base, 32)
            mode = self.sim.read_holding(self.a.bridge_base + 8, 1)[0]
            return dash, mode
        except (OSError, ModbusError):
            self.sim.close()
            return None, None

    def read_drive(self):
        try:
            r = self.drv.read_holding(ETA, THR - ETA + 1)          # 3201..3209
            rfrd = self.drv.read_holding(RFRD, 1)[0]
            lft = self.drv.read_holding(LFT, 1)[0]
            return {"eta": r[0], "lcr": r[LCR - ETA], "otr": r[OTR - ETA], "thr": r[THR - ETA], "rfrd": rfrd, "lft": lft}
        except (OSError, ModbusError):
            self.drv.close()
            return None

    def write_drive(self, dash):
        cmd = u16(dash[DASH_CMD])
        ref = max(-self.a.max_rpm, min(self.a.max_rpm, s16(dash[DASH_LFRD])))
        ramps = (dash[DASH_ACC], dash[DASH_DEC])
        try:
            if not self.a.no_ramps and ramps != self.ramps and all(0 < s16(v) <= 6000 for v in ramps):
                self.drv.write_registers(ACC, list(ramps))
                self.ramps = ramps
            self.drv.write_register(LFRD, u16(ref))
            self.drv.write_register(CMD, cmd)
        except (OSError, ModbusError) as e:
            self.drv.close()
            self.note = f"drive write failed: {e}"

    def stop_drive(self):
        try:
            self.drv.write_register(LFRD, 0)
            self.drv.write_register(CMD, STOP_CMD)
            return True
        except (OSError, ModbusError):
            self.drv.close()
            return False

    def write_sim(self, drive):
        self.hb = (self.hb + 1) & 0x7FFF
        d = drive or {}
        words = [self.hb, 1 if drive else 0, d.get("eta", 0), d.get("rfrd", 0), d.get("lcr", 0),
                 d.get("otr", 0), d.get("thr", 0), d.get("lft", 0)]
        try:
            self.sim.write_registers(self.a.bridge_base, [u16(w) for w in words])
        except (OSError, ModbusError):
            self.sim.close()

    def report(self, dash, drive, now):
        if now - self.last_print < 1.0:
            return
        self.last_print = now
        cmd = f"CMD {u16(dash[DASH_CMD]):04X}h LFRD {s16(dash[DASH_LFRD])}" if dash else "CMD –"
        drv = f"ETA {drive['eta']:04X}h {s16(drive['rfrd'])} rpm {s16(drive['lcr']) / 10:.1f} A" if drive else "drive –"
        print(f"{time.strftime('%H:%M:%S')}  {cmd:<22} {drv:<30} {self.note}", flush=True)

    def close(self):
        if self.armed and self.read_drive() is not None and self.stop_drive():
            print("Bridge closed: ramp stop sent to the drive.")
        self.sim.close()
        self.drv.close()


def main():
    ap = argparse.ArgumentParser(description="Run the real ATV930 from Control Expert's PLC simulator (testing only)")
    ap.add_argument("--sim", default="127.0.0.1", help="PLC simulator address (default this PC)")
    ap.add_argument("--sim-port", type=int, default=502)
    ap.add_argument("--sim-unit", type=int, default=255)
    ap.add_argument("--base", type=int, default=1000, help="status block, %%MW (aDash)")
    ap.add_argument("--bridge-base", type=int, default=1200, help="bridge block, %%MW (aBridge)")
    ap.add_argument("--drive", default="192.168.20.30", help="ATV930 address")
    ap.add_argument("--drive-port", type=int, default=502)
    ap.add_argument("--drive-unit", type=int, default=0, help="Modbus unit ID of the drive (try 248 or 255 if 0 gets no reply)")
    ap.add_argument("--max-rpm", type=int, default=300, help="cap on the speed reference sent to the drive")
    ap.add_argument("--no-ramps", action="store_true", help="never write ACC/DEC: keep the ramps set on the keypad")
    ap.add_argument("--period", type=float, default=0.05, help="cycle time, s")
    a = ap.parse_args()

    print(f"Drive bridge: simulator {a.sim}:{a.sim_port} (%MW{a.base}, %MW{a.bridge_base}) <-> ATV930 {a.drive}:{a.drive_port}"
          f", speed capped at {a.max_rpm} rpm. Ctrl+C to stop.")
    b = Bridge(a)
    if hasattr(signal, "SIGBREAK"):      # Windows: closing the console window also sends the stop
        signal.signal(signal.SIGBREAK, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        while True:
            t0 = time.monotonic()
            b.step()
            time.sleep(max(0.0, a.period - (time.monotonic() - t0)))
    except KeyboardInterrupt:
        pass
    finally:
        b.close()


if __name__ == "__main__":
    main()
