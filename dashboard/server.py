"""M580 + ATV930 + SICK MPB10 condition-monitoring demo dashboard (stdlib only).

  python server.py                 simulation (no hardware needed), http://127.0.0.1:8780
  python server.py --live          read the M580 over Modbus TCP (addresses from the IP addresses tab)
  python server.py --lan           also reachable from phones/iPads/TVs on the same network

Live mode READS one block of holding registers from the M580. It WRITES only when "Allow drive control
from the dashboard" is ticked in Setup: then a 3-register command block, refreshed every cycle, so the
PLC's watchdog sees the dashboard is alive. The PLC decides whether the drive actually runs.
Control Expert side: ../control-expert/.

STATUS MAP  (read, offset from the status base, default %MW1000)
  +0   heartbeat          PLC counts up every scan; frozen = PLC data stale
  +1   status bits        b0 SIG300 EtherNet/IP healthy   b1 IO-Link device on S1
                          b2 vibration warning   b3 vibration alarm   b4 shock   b5 temperature warning
  +2   velocity RMS       mm/s x100 (overall)
  +3   velocity X         mm/s x100   (-32768 = not available)
  +4   velocity Y         mm/s x100
  +5   velocity Z         mm/s x100
  +6   temperature        degC x10
  +7   shock counter
  +8   MPB10 alarm word   (bits 0-15 of the IO-Link process data)
  +9 .. +18               raw IO-Link process data, 20 bytes, high byte first
  +19  reserved
  +20  drive bits         b0 ATV930 EtherNet/IP healthy  b1 ready  b2 running  b3 fault
                          b4 dashboard control allowed by PLC  b5 dashboard watchdog OK  b6 reverse
  +21  ETA                drive status word (raw)
  +22  output speed       rpm (RFRD)
  +23  speed reference    rpm actually sent to the drive (LFRD)
  +24  motor current      A x10 (LCR)
  +25  motor torque       % (OTR)
  +26  motor thermal      % (THR)
  +27  last fault code    (LFT)
  +28  CMD                command word sent to the drive (raw)
  +29  panel / RIO bits  b0 BMECRA31210 drop healthy  b4 demo enable (xDemoEnable)
                          b5 green  b6 amber  b7 red: the program's stack-light state (no lamps fitted)
                          b8 RIO main ring closed  b9 ring diagnostics mapped
                          (b1-b3 and b10 were the operator panel buttons: no panel on this board, always 0)
  +30  accel ramp         s x10 sent to the drive (ACC)
  +31  decel ramp         s x10 sent to the drive (DEC)

COMMAND MAP (written, offset from the command base, default %MW1100)
  +0   dashboard heartbeat
  +1   bits: b0 run  b1 stop  b2 fault reset  b3 reverse  b4 jog (PLC caps the speed at its jog limit)
  +2   speed request, rpm
  +3   accel ramp, s x10 (0 = PLC default)   +4 decel ramp, s x10
"""
import argparse
import ipaddress
import json
import os
import random
import socket
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from modbus import ModbusClient

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
SETTINGS = os.path.join(HERE, "settings.json")
NA = -32768
BLOCK = 32
CMD_WORDS = 5
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml"}

DEFAULTS = {
    "mode": "sim",
    # IP plan (the IP addresses tab): two networks.
    # Control 192.168.10.x: the CPU, the RIO drop and the laptop on CPU ETH1.
    # Devices 192.168.20.x: the BMENOC0311 (backplane port disabled) with the SIG300 and ATV930 on its front ports.
    "laptop_ip": "192.168.10.100", "plc_host": "192.168.10.10", "noc_host": "192.168.20.10",
    "cra_host": "192.168.10.20", "cra_drop": 3,
    "sig300_host": "192.168.20.20", "atv_host": "192.168.20.30", "netmask": "255.255.255.0",
    # also on the device network: the D-Link router as a Wi-Fi access point (DHCP pool .150-.199) and the cobot
    "ap_host": "192.168.20.1", "cobot_host": "192.168.20.94",
    "gateway": "",                        # none: the rig is isolated
    # Modbus
    "plc_port": 502, "unit_id": 255, "base": 1000, "cmd_base": 1100,
    # ISO 10816-3 group 2 (15-300 kW), rigid mounting: zone limits in mm/s RMS
    "zone_ab": 1.4, "zone_bc": 2.8, "zone_cd": 4.5,
    "temp_warn": 70.0,
    "asset": "Demo motor",
    # drive
    "allow_control": False, "max_rpm": 1500, "nominal_rpm": 1500,
    "jog_rpm": 150, "accel_s": 5.0, "decel_s": 5.0,
    # demo sequence (VFD control tab): direction fwd / rev / stop, speed, hold time from the step's start
    "sequence": [
        {"dir": "fwd", "rpm": 600, "hold": 8}, {"dir": "fwd", "rpm": 1200, "hold": 8},
        {"dir": "fwd", "rpm": 300, "hold": 6}, {"dir": "stop", "rpm": 0, "hold": 6},
        {"dir": "rev", "rpm": 900, "hold": 8}, {"dir": "rev", "rpm": 1500, "hold": 6},
        {"dir": "stop", "rpm": 0, "hold": 6},
    ],
    "seq_loop": True,
}
DEVICE_KEYS = ("laptop_ip", "plc_host", "noc_host", "cra_host", "sig300_host", "atv_host", "ap_host", "cobot_host")
CONTROL_KEYS = ("laptop_ip", "plc_host", "cra_host")      # on the CPU's network
FIELD_KEYS = ("noc_host", "sig300_host", "atv_host", "ap_host", "cobot_host")   # on the NOC's network
# what the IP tab checks from this laptop, and on which TCP port
REACH = {"plc_host": 502, "noc_host": 502, "cra_host": 502, "sig300_host": 80, "atv_host": 502,
         "ap_host": 80}      # not the cobot: its controller limits connections


def load_settings():
    cfg = dict(DEFAULTS)
    try:
        with open(SETTINGS) as f:
            saved = json.load(f)
        if "sig200_host" in saved and "sig300_host" not in saved:     # the rig moved from a SIG200 to a SIG300
            saved["sig300_host"] = saved["sig200_host"]
        cfg.update({k: v for k, v in saved.items() if k in DEFAULTS})   # old keys (the NUA's, OPC UA) drop out
    except (OSError, ValueError):
        pass
    return cfg


def save_settings(cfg):
    with open(SETTINGS, "w") as f:
        json.dump(cfg, f, indent=2)


def clean_sequence(v):
    if not isinstance(v, list) or not 1 <= len(v) <= 20:
        raise ValueError("A sequence needs 1 to 20 steps")
    out = []
    for st in v:
        d = str(st.get("dir", "stop"))
        if d not in ("fwd", "rev", "stop"):
            raise ValueError(f"Unknown direction “{d}”")
        rpm, hold = int(float(st.get("rpm") or 0)), float(st.get("hold") or 0)
        if not 1 <= hold <= 300:
            raise ValueError("Hold times must be 1 to 300 s")
        if d != "stop" and rpm <= 0:
            raise ValueError("Forward and reverse steps need a speed above 0")
        out.append({"dir": d, "rpm": 0 if d == "stop" else rpm, "hold": round(hold, 1)})
    return out


def coerce(k, v):
    if k == "sequence":
        return clean_sequence(v)
    t = type(DEFAULTS[k])
    if t is bool:
        return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "on", "yes")
    return t(v)


def check_ip_plan(cfg):
    """-> (errors, warnings). Errors block saving; warnings are shown."""
    errors, warns = [], []
    try:
        mask = ipaddress.IPv4Address(cfg["netmask"])
        net_bits = bin(int(mask)).count("1")
        if bin(int(mask))[2:].rstrip("0").count("0"):
            errors.append("Subnet mask is not a valid mask")
    except ValueError:
        return ["Subnet mask is not an IPv4 address"], []
    addrs = {}
    for k in DEVICE_KEYS:
        try:
            addrs[k] = ipaddress.IPv4Address(cfg[k])
        except ValueError:
            errors.append(f"{LABELS[k]}: “{cfg[k]}” is not an IPv4 address")
    if cfg["gateway"]:                    # no gateway is fine
        try:
            ipaddress.IPv4Address(cfg["gateway"])
        except ValueError:
            errors.append(f"{LABELS['gateway']}: “{cfg['gateway']}” is not an IPv4 address")
    if not 1 <= int(cfg["cra_drop"]) <= 159:
        errors.append("RIO drop number must be 1–159 (the BMECRA31210 rotary switches)")
    if errors:
        return errors, warns
    net = ipaddress.IPv4Network(f"{cfg['plc_host']}/{net_bits}", strict=False)
    dev = ipaddress.IPv4Network(f"{cfg['noc_host']}/{net_bits}", strict=False)
    for keys, n, what in ((CONTROL_KEYS, net, "the M580’s control network"), (FIELD_KEYS, dev, "the NOC’s device network")):
        for k in keys:
            a = addrs[k]
            if a not in n:
                warns.append(f"{LABELS[k]} {a} is not in {what} {n}")
            elif a in (n.network_address, n.broadcast_address):
                errors.append(f"{LABELS[k]} {a} is the network or broadcast address")
    if cfg["gateway"] and ipaddress.IPv4Address(cfg["gateway"]) not in net:
        warns.append(f"Gateway {cfg['gateway']} is not in the control network {net}")
    seen = {}
    for k in DEVICE_KEYS:
        a = str(addrs[k])
        if a in seen:
            errors.append(f"{LABELS[k]} and {LABELS[seen[a]]} both use {a}")
        seen[a] = k
    return errors, warns


LABELS = {"laptop_ip": "Laptop", "plc_host": "M580 CPU", "noc_host": "BMENOC0311",
          "cra_host": "RIO drop (BMECRA31210)", "sig300_host": "SIG300", "atv_host": "ATV930",
          "gateway": "Gateway", "ap_host": "D-Link Wi-Fi access point",
          "cobot_host": "Lexium cobot"}


def local_ips():
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(i for i in ips if not i.startswith("127."))


def s16(v):
    return v - 0x10000 if v >= 0x8000 else v


def scaled(v, k):
    v = s16(v)
    return None if v == NA else round(v / k, 2)


# ------------------------------------------------------------------ sources
class SimSource:
    """A motor on an ATV930 with an MPB10 bolted on, plus the network chain, all simulated.
    Mirrors what the PLC program in ../control-expert does, so the screen behaves the same."""

    def __init__(self):
        self.unbalance = 0.0      # 0..1, the slider ("clip-on weight")
        self.heat = 0.0           # 0..1, extra heating
        self.sig_ok = True        # SIG300 Ethernet cable on NOC ETH3
        self.ring_ok = True       # RIO main ring closed: both fibers between the BMXNRP0200s
        self.drop_ok = True       # at least one fiber path to the drop
        self.swap_until = 0.0
        self.shock_until = 0.0
        self.shocks = 0
        self.v = 0.2
        self.temp = 28.0
        self.hb = 0
        # drive
        self.speed = 0.0
        self.thermal = 20.0
        self.fault = 0             # 0 = none
        self.reset_until = 0.0

    def action(self, name, value=None):
        if name == "unbalance":
            self.unbalance = max(0.0, min(1.0, float(value)))
        elif name == "heat":
            self.heat = max(0.0, min(1.0, float(value)))
        elif name == "knock":
            self.shocks += 1
            self.shock_until = time.time() + 1.5
        elif name == "sig":
            self.sig_ok = bool(value)
        elif name == "ring":
            self.ring_ok = bool(value)
        elif name == "drop":
            self.drop_ok = bool(value)
        elif name == "swap":
            self.swap_until = time.time() + 4.0
        elif name == "trip":
            self.fault = 16       # shown as "simulated fault"
        elif name == "reset":
            self.__init__()

    def read(self, cfg, cmd, dt):
        now = time.time()
        self.hb = (self.hb + 1) & 0x7FFF
        # ---- drive (same rules as the PLC: run needs the dashboard alive, no fault, no stop)
        if cmd["reset"] and self.fault:
            self.fault = 0
        run = cmd["run"] and cmd["alive"] and not cmd["stop"] and not self.fault
        ref = min(max(cmd["speed"], 0), cfg["max_rpm"]) if run else 0
        target = -ref if cmd["reverse"] else ref
        # ATV ramps: ACC/DEC = seconds from 0 to nominal speed
        speeding_up = abs(target) > abs(self.speed) and (target >= 0) == (self.speed >= 0)
        secs = cmd["accel"] if speeding_up else cmd["decel"]
        ramp = cfg["nominal_rpm"] / max(0.1, secs) * dt
        self.speed += max(-ramp, min(ramp, target - self.speed))
        if self.fault:
            self.speed -= max(-900 * dt, min(900 * dt, self.speed))   # freewheel-ish stop
        running = abs(self.speed) > 1 or run
        s = min(1.2, abs(self.speed) / max(1, cfg["nominal_rpm"]))
        current = (1.1 + 2.2 * s + 1.6 * self.unbalance * s) if running else 0.0
        torque = (12 + 25 * s + 12 * self.unbalance * s) if running else 0.0
        self.thermal += ((25 + 55 * s * (1 + 0.3 * self.unbalance)) - self.thermal) * min(1.0, dt / 20.0)
        if run and self.fault == 0:
            eta = 0x0027
        elif self.fault:
            eta = 0x0008
        else:
            eta = 0x0023
        cmdw = (0x000F | (0x0800 if cmd["reverse"] else 0)) if run else (0x0080 if cmd["reset"] else 0x0007)
        dbits = 1 | 2 * (not self.fault) | 4 * running | 8 * bool(self.fault) | 16 | 32 * cmd["alive"] | 64 * cmd["reverse"]
        drive = [dbits, eta, round(self.speed), round(target), round(current * 10), round(torque),
                 round(self.thermal), self.fault, cmdw]

        # ---- vibration and temperature follow speed: unbalance force grows with speed squared
        knock = 4.0 * (self.shock_until - now) / 1.5 if now < self.shock_until else 0.0
        target = 0.12 + 0.75 * s + 6.0 * self.unbalance ** 1.3 * s * s
        self.v += (target - self.v) * min(1.0, dt / 1.5)
        v = max(0.05, self.v * (1 + random.gauss(0, 0.04)) + knock)
        ttarget = 26 + 10 * s + 22 * self.unbalance * s + 45 * self.heat
        self.temp += (ttarget - self.temp) * min(1.0, dt / 12.0)

        present = now >= self.swap_until
        eip = self.sig_ok
        regs = [0] * BLOCK
        regs[0] = self.hb
        if eip and present:
            vx, vy, vz = v * 0.85, v, v * 0.35 + 0.05
            warn, alarm = v >= cfg["zone_bc"], v >= cfg["zone_cd"]
            shock = now < self.shock_until
            twarn = self.temp >= cfg["temp_warn"]
            alarm_word = warn | alarm << 1 | shock << 2 | twarn << 3
            regs[1] = 1 | 2 | warn << 2 | alarm << 3 | shock << 4 | twarn << 5
            regs[2:9] = [round(v * 100), round(vx * 100), round(vy * 100), round(vz * 100),
                         round(self.temp * 10), self.shocks, alarm_word]
            # raw process data as the sensor would send it (simulated layout)
            regs[9:19] = [alarm_word, 0, round(v * 100), round(vx * 100), round(vy * 100), round(vz * 100),
                          round(self.temp * 10), self.shocks, 0, 0]
        else:
            regs[1] = (1 if eip else 0) | (2 if eip and present else 0)
            regs[2:8] = [NA] * 5 + [self.shocks]
        regs[20:29] = drive
        # the program's stack-light state (04_Dashboard_Out): no lamps fitted on this board
        sensor_lost = not (eip and present)
        red = bool(self.fault) or (not sensor_lost and v >= cfg["zone_cd"]) or (sensor_lost and int(now * 2) % 2)
        amber = not sensor_lost and (v >= cfg["zone_bc"] or self.temp >= cfg["temp_warn"])
        green = running and not self.fault
        regs[29] = (self.drop_ok | 16 | green << 5 | amber << 6 | red << 7
                    | (self.ring_ok and self.drop_ok) << 8 | 1 << 9)
        regs[30:32] = [round(cmd["accel"] * 10), round(cmd["decel"] * 10)]
        return [x & 0xFFFF for x in regs]


class PlcSource:
    def __init__(self, cfg):
        self.client = ModbusClient(cfg["plc_host"], int(cfg["plc_port"]), int(cfg["unit_id"]), timeout=1.0)
        self.base = int(cfg["base"])
        self.cmd_base = int(cfg["cmd_base"])
        self.hb = 0

    def read(self, cfg, cmd, dt):
        regs = self.client.read_holding(self.base, BLOCK)
        if cfg["allow_control"]:
            # refreshed every cycle: the PLC watchdog stops the drive if this stops changing
            self.hb = (self.hb + 1) & 0x7FFF
            run = cmd["run"] and cmd["alive"] and not cmd["stop"]
            bits = run | cmd["stop"] << 1 | cmd["reset"] << 2 | cmd["reverse"] << 3 | cmd["jog"] << 4
            speed = min(max(int(cmd["speed"]), 0), int(cfg["max_rpm"]))
            self.client.write_registers(self.cmd_base, [self.hb, bits, speed,
                                                        round(cmd["accel"] * 10), round(cmd["decel"] * 10)])
        return regs

    def close(self):
        self.client.close()


# ------------------------------------------------------------------ engine
class Engine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.sim = SimSource()
        self.plc = None
        self.state = {}
        self.history = deque(maxlen=600)   # 5 min at 2 Hz
        self.events = deque(maxlen=40)
        self.prev = {}
        self.last_hb = None
        self.hb_changed = time.time()
        self.reads = deque(maxlen=50)
        self.errors = 0
        self.last_error = ""
        self.seq = 0
        self.viewers = 0                   # open dashboard pages (SSE streams)
        self.last_viewer = 0.0
        self.cmd = {"run": False, "stop_until": 0.0, "reverse": False, "speed": 900, "reset_until": 0.0,
                    "jog_until": 0.0, "jog_rev": False}
        self.reach = {}
        self.prog = {"on": False}           # demo sequence runner
        self.marks = deque(maxlen=60)      # speed-change markers for the charts: [t, label]
        threading.Thread(target=self._loop, daemon=True, name="poll").start()
        threading.Thread(target=self._reach_loop, daemon=True, name="reach").start()

    # ---- settings
    def configure(self, new):
        with self.lock:
            trial = dict(self.cfg)
            trial.update({k: coerce(k, v) for k, v in new.items() if k in DEFAULTS})
            errors, warns = check_ip_plan(trial)
            if errors:
                return {"errors": errors, "warnings": warns}
            changed_source = any(trial[k] != self.cfg[k] for k in ("mode", "plc_host", "plc_port", "unit_id", "base", "cmd_base"))
            if trial["allow_control"] and not self.cfg["allow_control"]:
                self.event("warn", "Drive control from the dashboard switched ON")
            if not trial["allow_control"] and self.cfg["allow_control"]:
                self.cmd["run"] = False
                self.event("info", "Drive control from the dashboard switched off")
            self.cfg.update(trial)
            save_settings(self.cfg)
            if changed_source:
                if self.plc:
                    self.plc.close()
                    self.plc = None
                self.cmd["run"] = False
                self.last_hb = None
                self.prev = {}
                self.event("info", f"Switched to {'live PLC' if self.cfg['mode'] == 'live' else 'simulation'} mode")
            return {"errors": [], "warnings": warns, "config": public_cfg(self.cfg)}

    def event(self, level, text):
        self.events.appendleft({"t": time.time(), "level": level, "text": text})

    # ---- drive commands from the page
    def control_allowed(self):
        return self.cfg["mode"] == "sim" or self.cfg["allow_control"]

    def drive(self, action, value=None):
        with self.lock:
            if not self.control_allowed():
                return "Drive control from the dashboard is off (Setup tab)"
            c = self.cmd
            if self.prog["on"] and action in ("run", "run_fwd", "run_rev", "jog", "speed", "reverse"):
                return "The demo sequence is running: press Stop sequence (or STOP) first"
            if action == "seq_start":
                return self._seq_start()
            if action == "seq_stop":
                self._seq_halt("stopped by the presenter")
                c["run"] = False
                return None
            if action == "seq_save":
                if self.prog["on"]:
                    return "Stop the sequence before editing it"
                try:
                    self.cfg["sequence"] = clean_sequence(value["steps"])
                except ValueError as e:
                    return str(e)
                self.cfg["seq_loop"] = bool(value.get("loop"))
                save_settings(self.cfg)
                return None
            if action in ("run", "run_fwd", "run_rev"):
                if action != "run":
                    rev = action == "run_rev"
                    if c["run"] and rev != c["reverse"]:
                        return "Stop the motor before changing direction"
                    c["reverse"] = rev
                c["run"], c["stop_until"], c["jog_until"] = True, 0.0, 0.0
                self.event("info", f"Start {'reverse' if c['reverse'] else 'forward'} requested at {c['speed']} rpm")
            elif action == "jog":
                # hold-to-run: the page repeats this every 200 ms while the button is held
                if c["run"]:
                    return "Jog only works with the motor stopped"
                if time.time() >= c["jog_until"]:
                    self.event("info", f"Jog {'reverse' if value == 'rev' else 'forward'} at {self.cfg['jog_rpm']} rpm")
                c["jog_until"], c["jog_rev"], c["stop_until"] = time.time() + 0.5, value == "rev", 0.0
            elif action == "jog_stop":
                c["jog_until"] = 0.0
            elif action == "ramps":
                acc, dec = float(value["accel"]), float(value["decel"])
                if not (0.1 <= acc <= 60 and 0.1 <= dec <= 60):
                    return "Ramp times must be 0.1 to 60 s"
                self.cfg["accel_s"], self.cfg["decel_s"] = round(acc, 1), round(dec, 1)
                save_settings(self.cfg)
                self.event("info", f"Ramps set: accel {acc:.1f} s, decel {dec:.1f} s")
            elif action == "stop":
                # a 1 s pulse, not a latch, so a later Start still works after a dashboard Stop
                c["run"], c["stop_until"], c["jog_until"] = False, time.time() + 1.0, 0.0
                self.event("info", "Stop requested")
                self._seq_halt("STOP pressed")
            elif action == "speed":
                c["speed"] = max(0, min(int(float(value)), int(self.cfg["max_rpm"])))
            elif action == "reverse":
                if c["run"]:
                    return "Stop the motor before changing direction"
                c["reverse"] = bool(value)
            elif action == "reset":
                c["reset_until"] = time.time() + 1.0
                self.event("info", "Fault reset requested")
            elif action == "trip" and self.cfg["mode"] == "sim":
                self.sim.action("trip")
            else:
                return "unknown action"
            return None

    # ---- demo sequence: steps through the same command block as the buttons, so the PLC rules all apply
    def _seq_start(self):
        if self.state.get("drive", {}).get("fault"):
            return "Clear the drive fault first"
        now = time.time()
        self.cmd["jog_until"] = 0.0
        self.prog = {"on": True, "i": -1, "cycle": 1, "t0": now, "wait": False, "wait_since": 0.0, "rev": False}
        self.event("info", f"Demo sequence started ({len(self.cfg['sequence'])} steps"
                           f"{', looping' if self.cfg['seq_loop'] else ''})")
        self._seq_next(now)
        return None

    def _seq_halt(self, why, level="info"):
        if self.prog["on"]:
            self.prog = {"on": False}
            self.event(level, f"Demo sequence ended: {why}")

    def _seq_next(self, now):
        P, steps = self.prog, self.cfg["sequence"]
        P["i"] += 1
        if P["i"] >= len(steps):
            if not self.cfg["seq_loop"]:
                self.cmd["run"] = False
                return self._seq_halt("finished")
            P["i"], P["cycle"] = 0, P["cycle"] + 1
        st, c = steps[P["i"]], self.cmd
        P["t0"], P["wait"] = now, False
        if st["dir"] == "stop":
            c["run"] = False
            return
        rev = st["dir"] == "rev"
        c["speed"] = min(st["rpm"], int(self.cfg["max_rpm"]))
        speed = self.state.get("drive", {}).get("speed") or 0
        if (c["run"] and c["reverse"] != rev) or (abs(speed) > 30 and (speed < 0) != rev):
            # change of direction: stop on the ramp first, then start the other way
            c["run"], P["wait"], P["wait_since"], P["rev"] = False, True, now, rev
        else:
            c["reverse"], c["run"] = rev, True

    def _seq_tick(self, now, cmd):
        P = self.prog
        if not P["on"]:
            return
        s, c = self.state, self.cmd
        d, st = s["drive"], self.cfg["sequence"][P["i"]]
        if not self.control_allowed():
            return self._seq_halt("drive control switched off", "warn")
        if d["fault"]:
            c["run"] = False
            return self._seq_halt("drive fault", "bad")
        if not s["links"]["fresh"]:
            c["run"] = False
            return self._seq_halt("lost the M580", "bad")
        if not cmd["alive"]:
            return self._seq_halt("no dashboard page open", "warn")
        if P["wait"]:
            if abs(d["speed"] or 0) < 30 or now - P["wait_since"] > 20:
                c["reverse"], c["run"], P["wait"], P["t0"] = P["rev"], True, False, now
            return
        if st["dir"] != "stop" and not c["run"]:
            # something else dropped the run: the PLC (demo enable, animation table)
            return self._seq_halt("the run was stopped by the PLC", "warn")
        if st["dir"] != "stop" and now - P["t0"] > 4 and not d["running"]:
            c["run"] = False
            return self._seq_halt("the drive didn’t start: check the Ready to run list", "bad")
        if now - P["t0"] >= st["hold"]:
            self._seq_next(now)

    def _seq_state(self, now):
        P = self.prog
        if not P["on"]:
            return {"on": False}
        st = self.cfg["sequence"][P["i"]]
        return {"on": True, "i": P["i"], "n": len(self.cfg["sequence"]), "cycle": P["cycle"], "wait": P["wait"],
                "left": None if P["wait"] else max(0.0, round(st["hold"] - (now - P["t0"]), 1)), "step": st}

    def _cmd_now(self, now):
        c = self.cmd
        alive = self.viewers > 0 or now - self.last_viewer < 2.0
        if not alive and c["run"]:
            c["run"] = False
            self.event("warn", "No dashboard page open: run request dropped")
        jog = alive and now < c["jog_until"] and not c["run"]
        cfg = self.cfg
        return {"run": c["run"] or jog, "stop": now < c["stop_until"], "jog": jog,
                "reverse": c["jog_rev"] if jog else c["reverse"],
                "speed": min(int(cfg["jog_rpm"]), int(cfg["max_rpm"])) if jog else c["speed"],
                "accel": float(cfg["accel_s"]), "decel": float(cfg["decel_s"]),
                "reset": now < c["reset_until"], "alive": alive}

    # ---- loops
    def _reach_loop(self):
        while True:
            cfg = dict(self.cfg)
            out = {}
            for key, port in REACH.items():
                t0 = time.time()
                try:
                    socket.create_connection((cfg[key], port), timeout=0.6).close()
                    out[key] = {"ok": True, "ms": round((time.time() - t0) * 1000), "port": port}
                except OSError:
                    out[key] = {"ok": False, "ms": None, "port": port}
            out["local"] = local_ips()
            self.reach = out
            time.sleep(3)

    def _loop(self):
        last, last_hist = time.time(), 0.0
        while True:
            time.sleep(0.2)
            now = time.time()
            dt, last = now - last, now
            with self.lock:
                cfg = self.cfg
                cmd = self._cmd_now(now)
                regs, err = None, ""
                try:
                    if cfg["mode"] == "sim":
                        regs = self.sim.read(cfg, cmd, dt)
                    else:
                        if not self.plc:
                            self.plc = PlcSource(cfg)
                        regs = self.plc.read(cfg, cmd, dt)
                    self.reads.append(now)
                except Exception as e:  # noqa: BLE001 - any comms failure shows on the page
                    err = str(e) or e.__class__.__name__
                    self.errors += 1
                    self.last_error = err
                self.state = self._decode(regs, err, now, cmd)
                self._diff_events()
                self._seq_tick(now, cmd)
                self.state["prog"] = self._seq_state(now)
                m, d = self.state["mpb"], self.state["drive"]
                if now - last_hist >= 0.5 and (m["v"] is not None or d["speed"] is not None):
                    last_hist = now
                    self.history.append([round(now, 1), m["v"], m["vx"], m["vy"], m["vz"], m["temp"], d["speed"], d["ref"]])
                self.seq += 1

    def _decode(self, regs, err, now, cmd):
        cfg = self.cfg
        plc_ok = regs is not None
        if plc_ok and regs[0] != self.last_hb:
            self.last_hb, self.hb_changed = regs[0], now
        fresh = plc_ok and now - self.hb_changed < 2.0
        st = regs[1] if plc_ok else 0
        eip = fresh and bool(st & 1)
        present = eip and bool(st & 2)
        data_ok = present
        v = scaled(regs[2], 100) if data_ok else None
        mpb = {
            "v": v,
            "vx": scaled(regs[3], 100) if data_ok else None,
            "vy": scaled(regs[4], 100) if data_ok else None,
            "vz": scaled(regs[5], 100) if data_ok else None,
            "temp": scaled(regs[6], 10) if data_ok else None,
            "shocks": regs[7] if plc_ok else None,
            "alarm_word": regs[8] if data_ok else None,
            "warn": bool(st & 4), "alarm": bool(st & 8), "shock": bool(st & 16), "temp_warn": bool(st & 32),
            "raw": [b for w in regs[9:19] for b in (w >> 8, w & 0xFF)] if data_ok else None,
        }
        db = regs[20] if fresh else 0
        d_ok = bool(db & 1)
        drive = {
            "healthy": d_ok,
            "ready": bool(db & 2), "running": bool(db & 4), "fault": bool(db & 8),
            "plc_allows": bool(db & 16), "watchdog_ok": bool(db & 32), "reverse": bool(db & 64),
            "eta": regs[21] if d_ok else None,
            "speed": s16(regs[22]) if d_ok else None,
            "ref": s16(regs[23]) if d_ok else None,
            "current": round(s16(regs[24]) / 10, 1) if d_ok else None,
            "torque": s16(regs[25]) if d_ok else None,
            "thermal": s16(regs[26]) if d_ok else None,
            "fault_code": regs[27] if d_ok else None,
            "cmd": regs[28] if fresh else None,
            "accel": round(regs[30] / 10, 1) if d_ok else None,
            "decel": round(regs[31] / 10, 1) if d_ok else None,
        }
        pb = regs[29] if fresh else 0
        panel = {"drop": bool(pb & 1), "enable": bool(pb & 16), "green": bool(pb & 32), "amber": bool(pb & 64),
                 "red": bool(pb & 128), "ring": bool(pb & 256), "ring_known": bool(pb & 512)}
        if fresh and cmd["run"] and not panel["enable"]:
            # the PLC dropped the run: clear the page's request so it needs a fresh Start
            self.cmd["run"], self.cmd["jog_until"] = False, 0.0
            self.event("info", "Run dropped: demo enable (xDemoEnable) is off in the PLC")
        drive["state"] = cia402_state(drive["eta"]) if d_ok else "No connection"
        zone = None
        if v is not None:
            zone = "A" if v < cfg["zone_ab"] else "B" if v < cfg["zone_bc"] else "C" if v < cfg["zone_cd"] else "D"
        health = ("offline" if not data_ok else
                  "alarm" if zone == "D" or mpb["alarm"] else
                  "warning" if zone == "C" or mpb["warn"] or mpb["temp_warn"] else "good")
        rate = 0.0
        if len(self.reads) > 1:
            span = self.reads[-1] - self.reads[0]
            rate = (len(self.reads) - 1) / span if span > 0 else 0.0
        return {
            "t": now, "mode": cfg["mode"], "seq": self.seq,
            "links": {"plc": plc_ok, "fresh": fresh, "eip": eip, "iolink": present, "atv": fresh and d_ok,
                      "drop": fresh and panel["drop"]},
            "plc": {"heartbeat": regs[0] if plc_ok else None, "rate": round(rate, 1), "errors": self.errors,
                    "error": err, "last_error": self.last_error, "host": cfg["plc_host"]},
            "mpb": mpb, "zone": zone, "health": health, "drive": drive, "panel": panel,
            "control": {"allowed": self.control_allowed(), "writing": cfg["mode"] == "live" and cfg["allow_control"],
                        "run": self.cmd["run"], "speed": self.cmd["speed"], "reverse": self.cmd["reverse"],
                        "jog": cmd["jog"], "jog_rev": self.cmd["jog_rev"], "jog_rpm": cfg["jog_rpm"],
                        "accel_s": cfg["accel_s"], "decel_s": cfg["decel_s"],
                        "alive": cmd["alive"], "max_rpm": cfg["max_rpm"]},
            "sim": {"unbalance": self.sim.unbalance, "heat": self.sim.heat, "sig": self.sim.sig_ok,
                    "ring": self.sim.ring_ok, "drop": self.sim.drop_ok,
                    "swapping": time.time() < self.sim.swap_until} if cfg["mode"] == "sim" else None,
        }

    def _diff_events(self):
        s, p = self.state, self.prev
        if not p and not s["links"]["plc"]:
            self.event("bad", f"Can’t reach the M580 at {s['plc']['host']} ({s['plc']['error']})")
        if p:
            L, PL = s["links"], p["links"]
            if PL["plc"] and not L["plc"]:
                self.event("bad", f"Lost the M580 at {s['plc']['host']} ({s['plc']['error']})")
            if not PL["plc"] and L["plc"]:
                self.event("ok", "M580 connected")
            if PL["fresh"] and L["plc"] and not L["fresh"]:
                self.event("bad", "PLC heartbeat stopped: data is stale")
            if PL["eip"] and not L["eip"] and L["fresh"]:
                self.event("bad", "SIG300 lost: EtherNet/IP connection down (cable on NOC ETH3?)")
            if not PL["eip"] and L["eip"]:
                self.event("ok", "SIG300 back online")
            if PL["iolink"] and not L["iolink"] and L["eip"]:
                self.event("bad", "MPB10 disconnected from port S1")
            if not PL["iolink"] and L["iolink"] and PL["eip"]:
                self.event("ok", "MPB10 detected on S1: parameters restored from Data Storage")
            if PL["atv"] and not L["atv"] and L["fresh"]:
                self.event("bad", "ATV930 lost: EtherNet/IP connection down")
            if not PL["atv"] and L["atv"] and PL["fresh"]:
                self.event("ok", "ATV930 connected")
            P, PP = s["panel"], p["panel"]
            if PL["drop"] and not L["drop"] and L["fresh"]:
                self.event("bad", "RIO drop (BMECRA31210) lost: both fiber paths down")
            if not PL["drop"] and L["drop"] and PL["fresh"]:
                self.event("ok", "RIO drop (BMECRA31210) back")
            if P["ring_known"] and L["drop"] and PL["drop"]:
                if PP["ring"] and not P["ring"]:
                    self.event("warn", "RIO ring opened (a fiber pulled?): drop still running on the other path")
                if not PP["ring"] and P["ring"]:
                    self.event("ok", "RIO ring closed again: redundancy restored")
            if P["enable"] != PP["enable"] and L["fresh"] and PL["fresh"]:
                self.event("info" if P["enable"] else "warn", f"Demo enable (xDemoEnable) {'ON' if P['enable'] else 'OFF'}")
            D, PD = s["drive"], p["drive"]
            if D["ref"] is not None and PD["ref"] is not None and D["ref"] != PD["ref"]:
                r = D["ref"]
                label = "STOP" if r == 0 else f"{'REV' if r < 0 else 'FWD'} {abs(r)}"
                if self.marks and s["t"] - self.marks[-1][0] < 1.5:
                    self.marks[-1] = [round(s["t"], 1), label]   # slider drags: keep the last value only
                else:
                    self.marks.append([round(s["t"], 1), label])
            if D["running"] and not PD["running"]:
                self.event("ok", "Motor running")
            if PD["running"] and not D["running"] and L["atv"]:
                self.event("info", "Motor stopped")
            if D["fault"] and not PD["fault"]:
                self.event("bad", f"Drive fault (code {D['fault_code']})")
            if PD["fault"] and not D["fault"] and L["atv"]:
                self.event("ok", "Drive fault cleared")
            if s["health"] != p["health"] and s["health"] in ("warning", "alarm") and L["iolink"]:
                self.event("warn" if s["health"] == "warning" else "bad",
                           f"Vibration {s['health'].upper()}: {s['mpb']['v']:.2f} mm/s (zone {s['zone']})")
            if p["health"] in ("warning", "alarm") and s["health"] == "good":
                self.event("ok", "Vibration back to normal")
            if s["mpb"]["shock"] and not p["mpb"]["shock"]:
                self.event("warn", f"Shock detected (count {s['mpb']['shocks']})")
            if s["mpb"]["temp_warn"] and not p["mpb"]["temp_warn"]:
                self.event("warn", f"Temperature high: {s['mpb']['temp']:.1f} °C")
        self.prev = s

    def snapshot(self):
        with self.lock:
            return dict(self.state, events=list(self.events)[:14], reach=self.reach, marks=list(self.marks))


def public_cfg(cfg):
    """The settings as the page sees them."""
    return dict(cfg)


def cia402_state(eta):
    if eta is None:
        return "–"
    if eta & 0x0008:
        return "Fault"
    if eta & 0x004F == 0x0040:
        return "Switch on disabled"
    m = eta & 0x006F
    return {0x0021: "Ready to switch on", 0x0023: "Switched on", 0x0027: "Operation enabled",
            0x0007: "Quick stop active"}.get(m, f"0x{eta:04X}")


# ------------------------------------------------------------------ web
class Handler(BaseHTTPRequestHandler):
    engine: Engine = None

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def do_GET(self):
        path = self.path.split("?")[0]
        eng = self.engine
        if path == "/api/state":
            return self._json(eng.snapshot())
        if path == "/api/history":
            with eng.lock:
                return self._json(list(eng.history))
        if path == "/api/config":
            errors, warns = check_ip_plan(eng.cfg)
            return self._json(dict(public_cfg(eng.cfg), _warnings=warns, _local=self._is_local()))
        if path == "/api/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            seq = -1
            with eng.lock:
                eng.viewers += 1
            try:
                while True:
                    s = eng.snapshot()
                    if s.get("seq") != seq:
                        seq = s.get("seq")
                        self.wfile.write(b"data: " + json.dumps(s).encode() + b"\n\n")
                        self.wfile.flush()
                    time.sleep(0.2)
            except (ConnectionError, OSError):
                return
            finally:
                with eng.lock:
                    eng.viewers -= 1
                    eng.last_viewer = time.time()
        name = "index.html" if path == "/" else path.lstrip("/")
        full = os.path.normpath(os.path.join(STATIC, name))
        if not full.startswith(STATIC) or not os.path.isfile(full):
            return self._json({"error": "not found"}, 404)
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", TYPES.get(os.path.splitext(full)[1], "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _is_local(self):
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def do_POST(self):
        path = self.path.split("?")[0]
        b = self._body()
        eng = self.engine
        if not self._is_local():
            # TVs and iPads (--lan) can watch; only the presenter's own laptop can change anything
            return self._json({"error": "view-only: use the presenter’s laptop to control the demo"}, 403)
        if path == "/api/sim":
            if eng.cfg["mode"] != "sim":
                return self._json({"error": "simulation controls only work in simulation mode"}, 409)
            with eng.lock:
                eng.sim.action(b.get("action", ""), b.get("value"))
            return self._json({"ok": True})
        if path == "/api/drive":
            try:
                err = eng.drive(b.get("action", ""), b.get("value"))
            except (TypeError, ValueError, KeyError) as e:
                err = f"bad request: {e}"
            return self._json({"error": err} if err else {"ok": True}, 409 if err else 200)
        if path == "/api/config":
            try:
                r = eng.configure(b)
            except (TypeError, ValueError) as e:
                return self._json({"errors": [str(e)], "warnings": []}, 400)
            return self._json(r, 400 if r["errors"] else 200)
        if path == "/api/clear":
            with eng.lock:
                eng.history.clear()
                eng.events.clear()
            return self._json({"ok": True})
        return self._json({"error": "not found"}, 404)


def main():
    ap = argparse.ArgumentParser(description="M580 + ATV930 + SICK MPB10 demo dashboard")
    ap.add_argument("--live", action="store_true", help="read the M580 instead of simulating")
    ap.add_argument("--sim", action="store_true", help="force simulation mode")
    ap.add_argument("--lan", action="store_true", help="listen on all interfaces (TVs, iPads on the network)")
    ap.add_argument("--port", type=int, default=8780)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    cfg = load_settings()
    if a.live:
        cfg["mode"] = "live"
    if a.sim:
        cfg["mode"] = "sim"
    Handler.engine = Engine(cfg)
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    srv = ThreadingHTTPServer((host, a.port), Handler)
    srv.daemon_threads = True
    url = f"http://127.0.0.1:{a.port}"
    print(f"Dashboard: {url}   ({cfg['mode']} mode)")
    if a.lan:
        for ip in local_ips():
            print(f"On the network: http://{ip}:{a.port}")
    if not a.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
