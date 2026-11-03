# Machine Health demo dashboard

Customer-facing screen for the M580 (BMEP584040 + BMENOC0301 + BMENUA0100 + BMECRA31210 RIO drop) + ATV930 + SICK SIG300 + MPB10 fiber demo. Python 3 standard library only, nothing to install.

```
Start Dashboard.bat              simulation, opens http://127.0.0.1:8780
Start Dashboard.bat --live       talk to the real M580 (addresses from the IP addresses tab)
Start Dashboard.bat --lan        also show it on a TV / iPad on the same network (view-only there)
python fake_m580.py              stand-in PLC on 127.0.0.1:5020 that obeys the command block
python fake_m580.py --no-enable  ...with the PLC-side permission off
python fake_nua.py               stand-in BMENUA0100 (OPC UA on 127.0.0.1:4840), needs: python -m pip install asyncua
```

Tabs:
- **Machine health**: health state, vibration gauge with ISO 10816-3 zones, motor speed, temperature, per-axis bars, shocks, trend and the PLC event log. In simulation, demo controls (unbalance, heat, knock, pull the fiber, swap the sensor) drive it.
- **Drive (ATV930)**: operator panel on the RIO drop (stack light, key, buttons, who started the motor), CiA402 state, speed and reference, current/torque/thermal, speed-vs-vibration trend, and the ETA and CMD words bit by bit.
- **VFD control**: run the ATV930 through the M580: ready-to-run permissive list, Start forward / Start reverse / STOP, hold-to-run jog, speed presets, accel/decel ramps, a reference-vs-actual speed chart, and a **demo sequence** (editable forward / reverse / stop steps with speeds and hold times, optional loop) that runs the real drive through the M580. Every speed change is marked on the speed charts. Space bar = STOP.
- **SIG300 & network**: the sensor path (via the BMENOC0301, the BMXNRP0200 and a media converter), the drive path, the RIO ring to the drop with the break shown when a link drops, the SIG300 ports, and the raw 20 IO-Link bytes beside the decoded values.
- **IT side · OPC UA** (optional, needs `asyncua`): connects to the BMENUA0100 control port over OPC UA (Security None or Basic256Sha256 Sign & encrypt, anonymous or a user), shows the same PLC variables from both paths side by side with their OPC UA timestamps, and a write test that should be refused. The client certificate is made on first use in `opcua-pki/` (kept out of git).
- **IP addresses**: every address in one place (CPU, NOC, NUA both ports, RIO drop and its number, SIG300, ATV930), validated (duplicates, subnet), with a live reachability check from this laptop, where to set each one, copy/print.
- **Setup**: data source, Modbus blocks, alarm limits, drive control on/off and speed limit, and the register maps.

Live mode reads %MW1000–1031 (word 29 = panel / RIO bits, 30–31 = ramps). It writes the 5-word command block (heartbeat, bits incl. jog, speed, accel, decel) at %MW1100 only when **Allow drive control** is ticked; the PLC still needs `xDashboardAllowed` and runs a 1 s watchdog on the dashboard heartbeat. Only the laptop running the server can send commands or change settings.

PLC side: `..\control-expert\` (ST sections, variable list, build guide).
