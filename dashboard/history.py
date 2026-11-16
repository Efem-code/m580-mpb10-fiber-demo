"""The historian inside the PLC (S07_History, ../control-expert/07_History.st): one sample a minute,
3 days of them, and the dashboard copies them off into one CSV file a day.

  The PLC block, one read of 124 words at %MW1900:
    %MW1900 header  [0] next slot  [1] samples kept  [2] seconds per sample  [3] samples so far (0-29999)
                    [4] year of the newest sample (BCD)  [5] slots (4320)  [6] window's first slot
                    [7] request number answered  [8] samples in the window (14)  [9] layout version
    %MW1910 request [0] first slot (-1 clears)  [1] a new request number
    %MW1912 window  14 samples, 8 words each:
                    MMDD, HHMM (BCD), vibration X, Y, Z (highest in the minute, mm/s x100),
                    temperature (degC x10), speed (rpm), low byte flags / high byte seconds (BCD)

HistBuf is S07's logic for simulation mode and the stand-in PLC. Reader copies a block, from the
real PLC or from a HistBuf, into memory and CSV files.
"""
import csv
import os
import threading
import time

from alarms import bcd, unbcd

BASE, REQ, WIN = 1900, 1910, 1912
WORDS = 124                       # header + request + window
SLOTS, PER_WIN, SEQ_WRAP = 4320, 14, 30000
NA = -32768
FLAGS = ["vib_warning", "vib_alarm", "temp_warning", "shock", "data_lost", "running", "drive_fault"]
CSV_HEAD = ["time", "vib_x_mm_s", "vib_y_mm_s", "vib_z_mm_s", "temp_c", "speed_rpm"] + FLAGS
KEEP_S = 8 * 86400                # what the page can show: 3 days from the PLC plus what the files hold


def s16(v):
    return v - 0x10000 if v & 0x8000 else v


class HistBuf:
    """S07_History, for simulation mode and the stand-in PLC. Holds raw words, like the PLC."""

    def __init__(self, period=60):
        self.lock = threading.RLock()
        self.buf = [[0] * 8 for _ in range(SLOTS)]
        self.head = self.count = self.seq = self.year = 0
        self.period = period
        self.win, self.win_slot, self.req_last = [0] * (8 * PER_WIN), 0, 0
        self.next_at = None
        self._reset_minute()

    def _reset_minute(self, flags=0):
        self.flags, self.vmax, self.temp = flags, None, None

    @staticmethod
    def flags_of(warn=False, alarm=False, temp_warn=False, shock=False, lost=False, running=False, fault=False):
        return sum(int(bool(b)) << i for i, b in enumerate((warn, alarm, temp_warn, shock, lost, running, fault)))

    def _write(self, t, vxyz, temp, speed, flags):
        lt = time.localtime(t)
        self.buf[self.head] = [bcd(lt.tm_mon * 100 + lt.tm_mday), bcd(lt.tm_hour * 100 + lt.tm_min)] + (
            [round(v * 100) for v in vxyz] + [round(temp * 10)] if vxyz else [NA] * 4) + [
            int(speed) & 0xFFFF, bcd(lt.tm_sec) << 8 | flags & 0xFF]
        self.year = bcd(lt.tm_year)
        self.head = (self.head + 1) % SLOTS
        self.count = min(SLOTS, self.count + 1)
        self.seq = (self.seq + 1) % SEQ_WRAP

    def update(self, now, vxyz, temp, speed, flags_now):
        """Each cycle. vxyz is None when the MPB10 has nothing valid."""
        with self.lock:
            self.flags |= flags_now
            if vxyz is not None:
                self.vmax = list(vxyz) if self.vmax is None else [max(a, b) for a, b in zip(self.vmax, vxyz)]
                self.temp = temp
            if self.next_at is None:
                self.next_at = now + self.period
            if now >= self.next_at:
                self.next_at += self.period
                if self.next_at < now:                    # paused for a while: don't catch up
                    self.next_at = now + self.period
                self._write(now, self.vmax, self.temp if self.temp is not None else 0, speed, self.flags)
                self._reset_minute(flags_now)

    def backfill(self, now, sample):
        """Fill the buffer as if it had been running for 3 days. sample(t) -> (vxyz, temp, speed, flags)."""
        with self.lock:
            t0 = now - SLOTS * self.period
            for i in range(SLOTS):
                self._write(t0 + i * self.period, *sample(t0 + i * self.period))

    def request(self, slot, number):
        """A screen wrote %MW1910."""
        with self.lock:
            if number == self.req_last:
                return
            self.req_last = number
            if s16(slot) == -1:
                self.head = self.count = self.seq = 0
                self.win_slot = 0xFFFF
            else:
                self.win_slot = max(0, min(SLOTS - 1, slot))
                self.win = [w for i in range(PER_WIN) for w in self.buf[(self.win_slot + i) % SLOTS]]

    def block(self):
        """The 124 words at %MW1900."""
        with self.lock:
            hdr = [self.head, self.count, self.period, self.seq, self.year, SLOTS, self.win_slot,
                   self.req_last, PER_WIN, 1]
            return [w & 0xFFFF for w in hdr + [0, 0] + self.win]


class BufClient:
    """A HistBuf behind the same two calls as a Modbus client, so the Reader works the same in simulation."""

    def __init__(self, hb):
        self.hb = hb

    def read_holding(self, addr, count):
        b = self.hb.block()
        return b[addr - BASE:addr - BASE + count]

    def write_registers(self, addr, values):
        if addr == REQ:
            self.hb.request(values[0], values[1])

    def close(self):
        pass


def sample_time(words, year_newest, newest_mmdd, now):
    """When a sample was taken, from its BCD date and time; None if the clock wasn't set."""
    year = unbcd(year_newest)
    mmdd, hhmm, sec = unbcd(words[0]), unbcd(words[1]), unbcd(words[7] >> 8)
    if year < 2020 or not 101 <= mmdd <= 1231:
        return None
    if mmdd > newest_mmdd:                       # newest is in January, this one in December
        year -= 1
    try:
        return time.mktime((year, mmdd // 100, mmdd % 100, hhmm // 100, hhmm % 100, min(sec, 59), 0, 0, -1))
    except (OverflowError, ValueError):
        return None


def row_of(t, w):
    """[t, vx, vy, vz, temp, speed, flags] with None where the MPB10 had nothing."""
    ok = s16(w[2]) != NA
    return [int(t)] + ([s16(w[i]) / 100 for i in (2, 3, 4)] + [s16(w[5]) / 10] if ok else [None] * 4) + [
        s16(w[6]), w[7] & 0xFF]


class Reader:
    """Copies the PLC's history into memory and, when `folder` is set, into one CSV file a day."""

    def __init__(self, folder=None):
        self.folder = folder
        self.lock = threading.RLock()
        self.rows = {}                 # t -> row
        self.last_seq = None
        self.req = int(time.time()) % 20000 + 1
        self.status = {"ok": False, "error": "", "samples": 0, "period": 60, "slots": SLOTS,
                       "last_copy": None, "clock_set": True, "folder": folder, "files": 0, "copying": False}
        if folder:
            self._load_files()

    # ---- files
    def _path(self, t):
        return os.path.join(self.folder, time.strftime("%Y-%m-%d", time.localtime(t)) + ".csv")

    def _load_files(self):
        if not os.path.isdir(self.folder):
            return
        since = time.time() - KEEP_S
        for name in sorted(os.listdir(self.folder)):
            if not name.endswith(".csv"):
                continue
            try:
                with open(os.path.join(self.folder, name), newline="", encoding="utf-8") as f:
                    for r in csv.DictReader(f):
                        t = time.mktime(time.strptime(r["time"], "%Y-%m-%d %H:%M:%S"))
                        if t >= since:
                            num = lambda k: float(r[k]) if r[k] != "" else None   # noqa: E731
                            self.rows[int(t)] = [int(t), num("vib_x_mm_s"), num("vib_y_mm_s"), num("vib_z_mm_s"),
                                                 num("temp_c"), int(float(r["speed_rpm"] or 0)),
                                                 sum(int(r[k] == "1") << i for i, k in enumerate(FLAGS))]
            except (OSError, ValueError, KeyError):
                continue
        self._count_files()

    def _count_files(self):
        if self.folder and os.path.isdir(self.folder):
            self.status["files"] = sum(n.endswith(".csv") for n in os.listdir(self.folder))

    def _append_files(self, new):
        os.makedirs(self.folder, exist_ok=True)
        by_file = {}
        for r in sorted(new):
            by_file.setdefault(self._path(r[0]), []).append(r)
        for path, rows in by_file.items():
            fresh = not os.path.exists(path)
            with open(path, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if fresh:
                    w.writerow(CSV_HEAD)
                w.writerows(csv_row(r) for r in rows)
        self._count_files()

    # ---- the PLC
    def _window(self, client, slot):
        for _ in range(3):                        # the TV may ask at the same moment: ask again
            self.req = self.req % 30000 + 1
            client.write_registers(REQ, [slot, self.req])
            for _ in range(15):                   # the PLC answers within a scan or two
                b = client.read_holding(BASE, WORDS)
                if b[7] == self.req and b[6] == slot:
                    return b
                time.sleep(0.025)
        raise TimeoutError("the PLC didn't answer the history request: is S07_History loaded?")

    def poll(self, client, now=None, full=False):
        """Copy any samples we don't have yet. full: all the PLC holds."""
        now = now or time.time()
        hdr = client.read_holding(BASE, 10)
        if hdr[5] != SLOTS or hdr[9] != 1:
            raise RuntimeError("no historian at %MW1900: load the S07_History section")
        head, count, period, seq = hdr[0], hdr[1], hdr[2], hdr[3]
        if full or self.last_seq is None:
            want = count
        else:
            want = min(count, (seq - self.last_seq) % SEQ_WRAP)
        new_rows, clock_set = [], True
        if want:
            self.status["copying"] = True
            start = (head - want) % SLOTS
            words = []
            for k in range(0, want, PER_WIN):
                slot = (start + k) % SLOTS
                b = self._window(client, slot)
                words += [b[12 + 8 * i:20 + 8 * i] for i in range(PER_WIN)]
            words = words[:want]
            hdr = client.read_holding(BASE, 10)
            newest_mmdd = unbcd(words[-1][0])
            for i, w in enumerate(words):
                t = sample_time(w, hdr[4], newest_mmdd, now)
                if t is None:                    # clock not set: date it back from now
                    clock_set = False
                    t = now - (want - 1 - i) * period
                new_rows.append(row_of(t, w))
        with self.lock:
            added = [r for r in new_rows if r[0] not in self.rows]
            for r in added:
                self.rows[r[0]] = r
            cut = now - KEEP_S
            for t in [t for t in self.rows if t < cut]:
                del self.rows[t]
            self.last_seq = seq
            if want or self.status["last_copy"] is None:
                self.status["last_copy"] = now
            self.status.update(ok=True, error="", samples=count, period=period, copying=False)
            if want:
                self.status["clock_set"] = clock_set
        if self.folder and added and clock_set:
            self._append_files(added)
        return len(added)

    def fail(self, err):
        with self.lock:
            self.status.update(ok=False, error=err, copying=False)

    def clear_plc(self, client):
        self.req = self.req % 30000 + 1
        client.write_registers(REQ, [0xFFFF, self.req])
        self.last_seq = None

    def query(self, hours):
        cut = time.time() - hours * 3600
        with self.lock:
            return dict(self.status), [self.rows[t] for t in sorted(self.rows) if t >= cut]


def csv_row(r):
    t, vx, vy, vz, temp, speed, flags = r
    f = lambda v: "" if v is None else v   # noqa: E731
    return [time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)), f(vx), f(vy), f(vz), f(temp), speed] + [
        flags >> i & 1 for i in range(len(FLAGS))]


def to_csv(rows):
    import io
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(CSV_HEAD)
    w.writerows(csv_row(r) for r in rows)
    return out.getvalue()
