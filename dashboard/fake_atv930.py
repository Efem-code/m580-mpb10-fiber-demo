"""Stand-in for the ATV930 on the bench: answers Modbus TCP like the drive, with the CiA402 state
machine, ramps, current and torque, so drive_bridge.py and the program can run with no hardware.

  python fake_atv930.py [port]                  default 127.0.0.1:5030
  python drive_bridge.py --drive 127.0.0.1 --drive-port 5030

While it runs:  f + Enter = trip a fault   q + Enter = quit
"""
import sys
import threading
import time

from modbus import DataStore, ModbusServer

ETA, LCR, OTR, THR, RFRD, LFT = 3201, 3204, 3205, 3209, 8604, 7121
CMD, LFRD, ACC, DEC = 8501, 8602, 9001, 9002
NOMINAL = 1750                       # rpm
REMOTE = 0x0200                      # ETA bit 9: taking commands from the network
SOD, READY, ON, ENABLED, FAULT = 0x0050, 0x0031, 0x0033, 0x0037, 0x0008
NAMES = {SOD: "switch on disabled", READY: "ready to switch on", ON: "switched on",
         ENABLED: "operation enabled", FAULT: "FAULT"}
FAULT_CODE = 16                      # what LFT shows after a tripped fault

port = int(sys.argv[1]) if len(sys.argv) > 1 else 5030
store = DataStore()
store.write("holding", ACC, [30, 30])
ModbusServer("127.0.0.1", port, store).start()
print(f"Fake ATV930 on 127.0.0.1:{port}, in Remote. f + Enter trips a fault, q + Enter quits.", flush=True)

trip = threading.Event()
stop = threading.Event()


def keys():
    for line in sys.stdin:
        if line.strip() == "f":
            trip.set()
        elif line.strip() == "q":
            stop.set()


threading.Thread(target=keys, daemon=True).start()


def s16(v):
    return v - 0x10000 if v >= 0x8000 else v


state, speed, last_fault, reset_was = SOD, 0.0, 0, False
last, last_print = time.monotonic(), 0.0
while not stop.is_set():
    time.sleep(0.02)
    now = time.monotonic()
    dt, last = now - last, now
    cmd, lfrd = store.read("holding", CMD, 1)[0], s16(store.read("holding", LFRD, 1)[0])
    acc, dec = store.read("holding", ACC, 2)
    low = cmd & 0x000F
    reset = bool(cmd & 0x0080)
    was = state

    if trip.is_set():
        trip.clear()
        state, last_fault = FAULT, FAULT_CODE
    elif state == FAULT:
        speed = max(0.0, speed - NOMINAL * dt) if speed >= 0 else min(0.0, speed + NOMINAL * dt)   # coasts
        if reset and not reset_was:
            state = SOD
    elif low & 0x0002 == 0:                      # disable voltage
        state = SOD
    elif state == SOD and low & 0x0007 == 0x0006:
        state = READY
    elif state == READY and low & 0x0007 == 0x0007:
        state = ON
    elif state == ON and low == 0x000F:
        state = ENABLED
    elif state == ON and low == 0x0006:
        state = READY
    elif state == ENABLED and low != 0x000F and abs(speed) < 1:
        state = ON                               # disable operation: stops on the ramp first
    reset_was = reset

    if state != FAULT:
        target = 0.0
        if state == ENABLED and low == 0x000F:
            target = float(lfrd) * (-1 if cmd & 0x0800 else 1)
        speeding_up = abs(target) > abs(speed)
        step = NOMINAL / (max(acc if speeding_up else dec, 1) / 10) * dt
        speed += max(-step, min(step, target - speed))
    if state not in (ENABLED, FAULT):
        speed = 0.0

    load = abs(speed) / NOMINAL
    moving = abs(speed) > 1
    store.write("holding", ETA, [state | REMOTE])
    store.write("holding", LCR, [round(9 + 14 * load) if moving else 0])
    store.write("holding", OTR, [round(10 + 25 * load) if moving else 0])
    store.write("holding", THR, [35])
    store.write("holding", RFRD, [round(speed) & 0xFFFF])
    store.write("holding", LFT, [last_fault])

    if state != was or now - last_print >= 2:
        last_print = now
        print(f"{time.strftime('%H:%M:%S')}  {NAMES[state]:<20} CMD {cmd:04X}h  ref {lfrd} rpm  speed {speed:.0f} rpm", flush=True)
