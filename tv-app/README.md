# TV app

A fullscreen, read-only machine screen for the Android TV in the demo room (Hikvision DS-D5B75RB/D, Android 11).
It reads the M580 directly over Modbus TCP, with no laptop: the status block at %MW1000 and the alarm block at
%MW1300. It never writes to the PLC, so acknowledging alarms stays on the dashboard or an HMI.

## Install

Copy `M580-Machine-Health.apk` to a USB stick, plug it into the TV, and open it from the TV's file manager.
The TV may ask to allow installs from that app. It starts by itself when the TV powers up.

## Network

The TV has to be on the control network, so it needs a cable to a small unmanaged switch at the CPU's service
port (the laptop moves to that switch too). Set the TV's wired network to a fixed address, for example
`192.168.10.50`, mask `255.255.255.0`, no gateway. The app defaults to the CPU at `192.168.10.10`, port 502,
unit 255. To change that, press MENU on the remote or tap the top right of the screen.

## Build

`python build.py <folder holding sdk\ and jdk-17*\>`, using Android platform 30, build-tools 34.0.0 and JDK 17.
It signs with `keystore.jks` here, made on the first build and not in the repo. Keep it: a later APK only installs
over this one if it has the same key. Its password is in `build.py`, so treat it as a demo key, not a release key.
