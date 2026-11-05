"""The alarm list, the block the PLC's S06_Alarms section fills at %MW1300, and a copy of that
section's logic for simulation mode and the stand-in PLC. See ../control-expert/06_Alarms.st.
"""
import time

BASE, CMD_BASE = 1300, 1290
SIZE = 444                           # words in the block
SLOTS = 64                           # history lines
LOG_START, STAMPS_START = 60, 10
NAMES = {1: "raised", 2: "cleared", 3: "acknowledged"}
ON_DELAY = 3.0                       # seconds, the PLC's iAlmDelay at 100 ms a count
ALARMS = [                           # number, name, priority (1 critical, 2 warning)
    (1, "Drive fault", 1), (2, "Drive connection lost", 1), (3, "SIG300 connection lost", 2),
    (4, "MPB10 data lost", 2), (5, "RIO drop lost", 1), (6, "RIO ring open", 2),
    (7, "Vibration warning", 2), (8, "Vibration alarm", 1), (9, "Temperature warning", 2),
    (10, "Dashboard link lost", 2), (11, "Spare 1", 2), (12, "Spare 2", 2)]


def bcd(n):
    return int(str(int(n)), 16)


def unbcd(v):
    return int(f"{v & 0xFFFF:04X}") if all(c in "0123456789" for c in f"{v & 0xFFFF:04X}") else 0


def clock_words(t=None):
    """The PC's clock as the four BCD words the PLC reads from %SW53..%SW50."""
    t = time.localtime(t)
    return [bcd(t.tm_year), bcd(t.tm_mon * 100 + t.tm_mday), bcd(t.tm_hour * 100 + t.tm_min), bcd(t.tm_sec)]


def stamp(words):
    """Four clock words as text; None if blank."""
    if not any(words):
        return None
    year, mmdd, hhmm, sec = (unbcd(w) for w in words)
    sec = sec // 100 if sec > 59 else sec           # seconds may sit in the high byte of %SW50
    return f"{year:04d}-{mmdd // 100:02d}-{mmdd % 100:02d} {hhmm // 100:02d}:{hhmm % 100:02d}:{sec:02d}"


def decode(b):
    """The block as the Alarms tab wants it, newest history line first."""
    items = []
    for n, name, prio in ALARMS:
        bit = 1 << (n - 1)
        active, unack = bool(b[1] & bit), bool(b[2] & bit)
        state = 1 if active and unack else 2 if active else 3 if unack else 0
        ofs = STAMPS_START + 4 * (n - 1)
        items.append({"id": n, "text": name, "prio": prio, "state": state, "raised": stamp(b[ofs:ofs + 4])})
    log = []
    for k in range(1, SLOTS + 1):
        ofs = LOG_START + 6 * ((b[5] - k) % SLOTS)
        code = b[ofs + 1]
        if code:
            n = code % 100
            log.append({"seq": b[ofs], "type": NAMES.get(code // 100, "?"), "id": n,
                        "text": ALARMS[n - 1][1] if 1 <= n <= len(ALARMS) else f"Alarm {n}",
                        "prio": ALARMS[n - 1][2] if 1 <= n <= len(ALARMS) else 2,
                        "at": stamp(b[ofs + 2:ofs + 6])})
    words = b[6:10]
    return {"items": items, "log": log, "active": b[3], "unack": b[4], "seq": b[0], "clock": stamp(words),
            "clock_set": unbcd(words[0]) >= 2020}


class AlarmLog:
    """S06_Alarms, for simulation mode and the stand-in PLC."""

    def __init__(self):
        self.b = [0] * SIZE
        self.since = {}
        self.active, self.unack = set(), set()
        self.seq, self.head = 0, 0

    def update(self, conds, now, ack_mask=0, clear=False):
        """conds: the numbers of the alarms whose condition is true now."""
        if clear:
            self.b[LOG_START:] = [0] * (SIZE - LOG_START)
            self.seq, self.head = 0, 0
        clock = clock_words(now)
        for n, _, _ in ALARMS:
            if n in conds:
                self.since.setdefault(n, now)
            else:
                self.since.pop(n, None)
            on = n in self.since and now - self.since[n] >= ON_DELAY
            kind = 0
            if on and n not in self.active:
                self.active.add(n)
                self.unack.add(n)
                kind = 1
                self.b[STAMPS_START + 4 * (n - 1):STAMPS_START + 4 * n] = clock
            elif n in self.active and not on:
                self.active.discard(n)
                kind = 2
            elif n in self.unack and ack_mask >> (n - 1) & 1:
                self.unack.discard(n)
                kind = 3
            if kind:
                self.seq = self.seq + 1 if self.seq < 29999 else 1
                ofs = LOG_START + 6 * self.head
                self.b[ofs:ofs + 6] = [self.seq, 100 * kind + n] + clock
                self.head = (self.head + 1) % SLOTS
        self.b[:10] = [self.seq, sum(1 << (n - 1) for n in self.active), sum(1 << (n - 1) for n in self.unack),
                       len(self.active), len(self.unack), self.head] + clock
        return self.b
