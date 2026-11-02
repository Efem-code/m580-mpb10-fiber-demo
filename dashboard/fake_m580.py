"""Stand-in for the M580 on the bench: serves the dashboard's Modbus map and obeys its command block
the way the Control Expert program does (watchdog, enable, CiA402-style drive state).

  python fake_m580.py [port] [--no-enable]
  then in the dashboard: IP addresses tab -> M580 = 127.0.0.1, Setup -> Live, port 5020

--no-enable  starts with xDashboardAllowed = FALSE, to check the dashboard refuses to run.
"""
import math
import sys
import time

from modbus import DataStore, ModbusServer

BASE, CMD = 1000, 1100
args = [a for a in sys.argv[1:] if not a.startswith("--")]
port = int(args[0]) if args else 5020
allowed = "--no-enable" not in sys.argv
store = DataStore()
ModbusServer("127.0.0.1", port, store).start()
print(f"Fake M580 on 127.0.0.1:{port}: status %MW{BASE}, commands %MW{CMD}, dashboard allowed = {allowed}")

hb = 0
speed = 0.0
last_dash_hb, dash_changed = None, 0.0
last = time.time()
while True:
    time.sleep(0.1)
    now = time.time()
    dt, last = now - last, now
    hb = (hb + 1) & 0x7FFF
    dash_hb, bits, req, acc, dec = store.read("holding", CMD, 5)
    if dash_hb != last_dash_hb:
        last_dash_hb, dash_changed = dash_hb, now
    dash_ok = allowed and now - dash_changed < 1.0          # the PLC watchdog
    run = dash_ok and bool(bits & 1) and not bits & 2
    rev = bool(bits & 8) and dash_ok
    if bits & 16:
        req = min(req, 300)                                    # jog limit
    ref = (-1 if rev else 1) * min(req, 1500) if run else 0
    acc_s = acc / 10 if dash_ok and acc else 5.0               # PLC defaults when the dashboard isn't in control
    dec_s = dec / 10 if dash_ok and dec else 5.0
    step = 1500 / (acc_s if abs(ref) > abs(speed) else dec_s) * dt
    speed += max(-step, min(step, ref - speed))
    running = abs(speed) > 1 or run
    s = abs(speed) / 1500
    v = 0.12 + 0.75 * s + 1.2 * s * s * (1 + math.sin(now / 5))
    eta = 0x0027 if run else 0x0023
    cmdw = (0x000F | (0x800 if rev else 0)) if run else 0x0007
    regs = [hb, 0b11 | (v >= 2.8) << 2 | (v >= 4.5) << 3, round(v * 100), round(v * 85), round(v * 100),
            round(v * 40), round((30 + 12 * s) * 10), 3, int(v >= 2.8)] + [0x1234] * 10 + [0]
    regs += [1 | 2 | 4 * running | 16 * allowed | 32 * dash_ok | 64 * rev, eta, round(speed), ref,
             round((1.1 + 2.2 * s) * 10) if running else 0, round(12 + 25 * s) if running else 0, 40, 0, cmdw]
    # RIO drop healthy, key switch on, stack light, ring closed and mapped
    regs += [1 | 16 | running << 5 | (v >= 2.8) << 6 | (v >= 4.5) << 7 | 1 << 8 | 1 << 9]
    regs += [round(acc_s * 10), round(dec_s * 10)]
    store.write("holding", BASE, [x & 0xFFFF for x in regs])
