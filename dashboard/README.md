# Machine Health demo dashboard

Customer-facing screen for the M580 safety board (BMEP584040S + BMEP58CPROS3, BMENOC0311, and a BMECRA31210 RIO drop on a fiber ring through two BMXNRP0200s) + ATV930 + SICK SIG300 + MPB10 demo. Python 3 standard library only, nothing to install.

```
Start Dashboard.bat              simulation, opens http://127.0.0.1:8780
Start Dashboard.bat --live       talk to the real M580 (addresses from the IP addresses tab)
Start Dashboard.bat --lan        also show it on a TV / iPad on the same network (view-only there)
python fake_m580.py              stand-in PLC on 127.0.0.1:5020 that obeys the command block
python fake_m580.py --no-enable  ...with the PLC-side permission off
python fake_atv930.py            stand-in ATV930 (Modbus on 127.0.0.1:5030), for the bridge with no hardware
python drive_bridge.py           Control Expert's PLC simulator runs the real ATV930 (testing only, see the
                                 Control Expert guide, section 8; or Start Drive Bridge.bat)
```

Tabs:
- **Machine health**: health state, vibration gauge with ISO 10816-3 zones, motor speed, temperature, per-axis bars, shocks, trend and the PLC event log. In simulation, demo controls (unbalance, heat, knock, pull the SIG300 cable, swap the sensor, pull one or both RIO ring fibers) drive it.
- **Drive (ATV930)**: the stack light state the program works out (the real SLT060 on SIG300 port S2 shows vibration as a bar), xDemoEnable, who started the motor, CiA402 state, speed and reference, current/torque/thermal, speed-vs-vibration trend, and the ETA and CMD words bit by bit.
- **VFD control**: run the ATV930 through the M580: ready-to-run permissive list, Start forward / Start reverse / STOP, hold-to-run jog, speed presets, accel/decel ramps, a reference-vs-actual speed chart, and a **demo sequence** (editable forward / reverse / stop steps with speeds and hold times, optional loop) that runs the real drive through the M580. Every speed change is marked on the speed charts. Space bar = STOP.
- **SIG300 & network**: the board drawing (main rack and RIO drop, live), the sensor path (BMENOC0311 ETH3 to the SIG300 on copper), the drive path, the RIO ring over the two fibers with the break shown when one or both drop, the SIG300 ports, and the raw 20 IO-Link bytes beside the decoded values.
- **IP addresses**: every address in one place (CPU, NOC, RIO drop and its number, SIG300, ATV930, D-Link, cobot), validated (duplicates, subnet), with a live reachability check from this laptop, where to set each one, copy/print.
- **Setup**: data source, Modbus blocks, alarm limits, drive control on/off and speed limit, and the register maps.

Live mode reads %MW1000–1031 (word 29 = RIO and status bits: drop health, xDemoEnable, the stack-light state, ring status; 30–31 = ramps). It writes the 5-word command block (heartbeat, bits incl. jog, speed, accel, decel) at %MW1100 only when **Allow drive control** is ticked; the PLC still needs `xDashboardAllowed` and `xDemoEnable`, and runs a 1 s watchdog on the dashboard heartbeat. Only the laptop running the server can send commands or change settings.

PLC side: `..\control-expert\` (ST sections, variable list, build guide).
