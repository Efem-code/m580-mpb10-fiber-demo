# TV app

The Machine Health dashboard, fullscreen on the Android TV in the demo room (Hikvision DS-D5B75RB/D, Android 11),
with no laptop. The page is the dashboard's own (`dashboard/static`, copied in at each build). `Engine.java` and
`WebServer.java` do what `dashboard/server.py` does, in the app, and talk straight to the M580 over Modbus TCP.
Change the status or command maps in both places.

It has the same tabs as the dashboard, except the Cobot tab, which needs the laptop. It's live mode only.

## Install

Copy `M580-Machine-Health.apk` to a USB stick, plug it into the TV, and open it from the TV's file manager.
It installs over an earlier version, signed with the same key. It starts by itself when the TV powers up.

## Network

The TV needs to be on the control network: a fixed address such as `192.168.10.201`, mask `255.255.255.0`, cabled
to the CPU's service port or a switch on it. The PLC address is on the IP addresses tab.

## Running the motor from the TV

Off by default, and the app then only reads. On the VFD control tab, turn on **Dashboard control**, then **PLC
permission** and **Demo enable**. The app then writes the command block at %MW1100 every 200 ms. The PLC needs
S02 with `aEnableCmd`.

**Only one writer at a time.** The laptop's dashboard watches the command block and steps back when something else
writes it, so with control on here, the TV wins and the laptop can only watch or press STOP. The app has no such
check, so turn control off here before using the laptop. The E-stop is hard-wired and works whatever the app does. If the app closes or leaves the screen,
the run request drops, and the PLC's 1 s watchdog stops the motor if the writes stop.

Limit sliders and alarm acknowledge write to the PLC at any time.

## Build

`python build.py <folder holding sdk\ and jdk-17*\>`, using Android platform 30, build-tools 34.0.0 and JDK 17.
It signs with `keystore.jks` here, made on the first build and not in the repo. Keep it: a later APK only installs
over this one if it has the same key. Its password is in `build.py`, so treat it as a demo key, not a release key.

To test the server on a PC: compile `src` without `MainActivity` and `BootReceiver`, then run
`ca.guillevin.m580tv.WebServer <dashboard/static> <settings file> <port>`.
