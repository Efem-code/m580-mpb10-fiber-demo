# M580 + ATV930 + SICK MPB10 fiber demo

A hands-on condition-monitoring demo rig: a Schneider Electric **M580** PLC reads a **SICK MPB10** vibration/temperature sensor through a **SICK SIG200** IO-Link master over a fiber link, and runs an **ATV930** drive on the motor. A customer-facing dashboard shows machine health, controls the drive through the PLC and plays forward/reverse/speed demo sequences.

The dashboard also runs **without any hardware**, in simulation mode, so anyone can try it on any laptop.

**Read the guides online:** <https://efem-code.github.io/m580-mpb10-fiber-demo/>

## What's in here

| Folder / file | What it is |
|---|---|
| `dashboard/` | The customer dashboard (Python 3, standard library only) and a stand-in PLC for testing |
| `control-expert/` | The M580 program: Structured Text sections, variable list and a step-by-step Control Expert build guide |
| `M580-MPB10-fiber-build-guide.html` | Bench build guide: parts, wiring diagram, IP plan, build steps, checklist, troubleshooting |

## Install and run the dashboard

1. Install **Python 3.8 or newer** from [python.org](https://www.python.org/downloads/). On Windows, tick **Add python.exe to PATH** in the installer.
2. Download this repo: the green **Code** button → **Download ZIP**, then unzip it. Or, with git:
   ```bash
   git clone <this repo's URL>
   ```
3. Start it:
   - **Windows:** double-click `dashboard\Start Dashboard.bat`
   - **Mac / Linux:** `cd dashboard` then `python3 server.py`
4. Your browser opens at <http://127.0.0.1:8780> in **simulation mode**. Use the demo controls at the top to add unbalance, heat, knocks, pull the fiber, and so on.

Nothing else needs installing. Your settings are saved to `dashboard/settings.json` on first change (it isn't part of the repo, so every new install starts from the defaults).

### Options

```
Start Dashboard.bat --live       talk to the real M580 (addresses from the IP addresses tab)
Start Dashboard.bat --lan        also show it on a TV / iPad on the same network (view-only there)
Start Dashboard.bat --port 8781  use another port
python fake_m580.py              stand-in PLC on 127.0.0.1:5020, to try Live mode without hardware
```

### The IT side (OPC UA), optional

The **IT side · OPC UA** tab reads the same PLC values over OPC UA from the BMENUA0100's control port, a separate network reached through a USB Ethernet adapter, and shows them next to the Modbus values. It needs one extra package:

```bash
python -m pip install asyncua
```

Without it, everything else works as before. To rehearse without the module, run `python fake_nua.py` in the `dashboard` folder and set the BMENUA0100 control port to `127.0.0.1` on the IP addresses tab.

To try Live mode without a PLC: run `python fake_m580.py`, then in the dashboard set the M580 address to `127.0.0.1` on the **IP addresses** tab, and port `5020` and Live mode on the **Setup** tab.

## Using it with the real hardware

Our demo board has a BMEP584040 CPU, BMENOC0301, BMENUA0100 and a BMECRA31210 RIO drop, plus an **ATV930U07M3** drive (0.75 kW / 1 HP, **200–240 V three-phase input**, 4.6 A output).

1. Build the rig with `M580-MPB10-fiber-build-guide.html` (open it in a browser).
2. Build and download the PLC program with `control-expert/Control-Expert-build-guide.html`.
3. Plug the laptop into the M580 CPU service port (ETH1), set it to `192.168.10.100 / 255.255.255.0` (no gateway), and start the dashboard with `--live`.
4. For the IT side: a USB Ethernet adapter at `192.168.30.100 / 255.255.255.0` (no gateway) into the BMENUA0100 control port at `192.168.30.10`. Setup is in section 7 of the Control Expert guide.

Drive control from the dashboard is **off by default**. It needs **Allow drive control** ticked in Setup *and* `xDashboardAllowed` set in the PLC. The E-stop must be hard-wired to the ATV930 STO inputs; it is not in software. Commission with the motor uncoupled and a low `iMaxSpeed` first.

## Status

Tested in simulation and in Live mode against the stand-in PLC. The device-DDT names in `00_IO_Map.st` / `05_IO_Out.st` and the MPB10 process-data offsets are placeholders to confirm on the real hardware (listed in the Control Expert guide).
