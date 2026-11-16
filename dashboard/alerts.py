"""Email (and text, through a carrier's email-to-text address) when the PLC raises an alarm, plus a daily summary.

The laptop dashboard and the TV app can both send. So nobody gets everything twice, a screen claims each
message in three spare PLC words before sending, and skips it if the other screen already has:
    %MW1297  the newest alarm event already sent (aAlm event number)
    %MW1298  who claimed last: 1 the laptop, 2 the TV
    %MW1299  the day the last daily summary went out, MMDD
No PLC code uses them. The TV app (tv-app/.../Alerter.java) does the same; keep the two in step.

The PLC is also the outbox: %MW1297 says how far the alerts have gone, so a screen that connects sends
what was raised while no screen was sending (the last 24 h), in one email.

The settings everyone shares (not the mail user name or password, which any Modbus client could read) live
in the PLC too, in aMailCfg at %MW1746 (154 words), so every screen picks them up:
    [0] 16#4D41 once written   [1] bit 0 alerts on, bit 1 warnings, bit 2 clears   [2] port
    [3] security 0 STARTTLS, 1 SSL, 2 none   [4] summary hour (-1 off)   [5] changes so far
    [6..29] mail server, [30..53] from address, [54..153] recipients: ASCII, low byte first (as a
    Control Expert STRING), zero-padded

When: a critical alarm raised goes straight away. Warnings (and clears, if ticked) are gathered and sent at
most every 15 minutes. The summary goes once a day after summary_hour, with the last 24 h of history as CSV.
"""
import random
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage

import history

CLAIM = 1297
MAIL_BASE, MAIL_WORDS, MARK = 1746, 154, 0x4D41
SHARED = ("alerts_on", "alert_warnings", "alert_cleared", "smtp_port", "smtp_security", "summary_hour",
          "smtp_host", "mail_from", "mail_to")
TEXT = {"smtp_host": (6, 24), "mail_from": (30, 24), "mail_to": (54, 100)}      # word offset, words
SECURITY = ["starttls", "ssl", "none"]
BACKLOG_S = 24 * 3600
LAPTOP, TV = 1, 2
BATCH_S = 15 * 60
SETTINGS = {                       # added to the dashboard's settings
    "alerts_on": False, "smtp_host": "", "smtp_port": 587, "smtp_security": "starttls", "smtp_user": "",
    "smtp_password": "", "mail_from": "", "mail_to": "", "alert_warnings": True, "alert_cleared": False,
    "summary_hour": 7,
}


def newer(a, b):
    """Alarm event number a came after b (they count to 29999, then start again)."""
    return 0 < (a - b) % 30000 < 15000


def check_text(k, v):
    """The shared text settings must fit their PLC words, in plain ASCII."""
    if k in TEXT:
        if not v.isascii():
            raise ValueError(f"{k.replace('_', ' ')}: plain letters only (no accents)")
        if len(v) > 2 * TEXT[k][1]:
            raise ValueError(f"{k.replace('_', ' ')} is too long: {2 * TEXT[k][1]} characters at most")
    return v


def pack(text, words):
    b = text.encode("ascii")[:2 * words].ljust(2 * words, b"\0")
    return [b[2 * i] | b[2 * i + 1] << 8 for i in range(words)]


def unpack(words):
    b = bytes(x for w in words for x in (w & 0xFF, w >> 8 & 0xFF))
    return b.split(b"\0", 1)[0].decode("ascii", "replace")


def to_block(cfg, counter):
    flags = bool(cfg["alerts_on"]) | bool(cfg["alert_warnings"]) << 1 | bool(cfg["alert_cleared"]) << 2
    hdr = [MARK, flags, int(cfg["smtp_port"]) & 0xFFFF, SECURITY.index(cfg["smtp_security"]) if cfg["smtp_security"] in SECURITY else 0,
           int(cfg["summary_hour"]) & 0xFFFF, counter & 0x7FFF]
    return hdr + pack(cfg["smtp_host"], 24) + pack(cfg["mail_from"], 24) + pack(cfg["mail_to"], 100)


def from_block(b):
    if b[0] != MARK:
        return None
    hour = b[4] - 0x10000 if b[4] & 0x8000 else b[4]
    out = {"alerts_on": bool(b[1] & 1), "alert_warnings": bool(b[1] & 2), "alert_cleared": bool(b[1] & 4),
           "smtp_port": b[2], "smtp_security": SECURITY[b[3]] if b[3] < 3 else "starttls", "summary_hour": max(-1, min(23, hour))}
    for k, (ofs, n) in TEXT.items():
        out[k] = unpack(b[ofs:ofs + n])
    return out


class MailSync:
    """Keeps the shared alert settings the same here and in the PLC (aMailCfg). The newest change wins:
    ours when we've just saved, the PLC's when its change count moves."""

    def __init__(self):
        self.counter = None
        self.next_read = 0.0

    @staticmethod
    def _read(client):
        return client.read_holding(MAIL_BASE, 100) + client.read_holding(MAIL_BASE + 100, MAIL_WORDS - 100)

    def write(self, client, cfg):
        cur = client.read_holding(MAIL_BASE, 6)
        counter = ((cur[5] if cur[0] == MARK else 0) + 1) & 0x7FFF
        b = to_block(cfg, counter)
        client.write_registers(MAIL_BASE + 6, b[6:100])
        client.write_registers(MAIL_BASE + 100, b[100:])
        client.write_registers(MAIL_BASE, b[:6])            # the header last: the block is whole when it changes
        self.counter = counter

    def tick(self, cfg, client, dirty, now=None):
        """-> the PLC's settings if they changed there, else None. dirty: ours changed, send them."""
        now = now or time.time()
        if dirty:
            self.write(client, cfg)
            return None
        if now < self.next_read:
            return None
        self.next_read = now + 10
        hdr = client.read_holding(MAIL_BASE, 6)
        if hdr[0] != MARK:
            if cfg.get("smtp_host") or cfg.get("mail_to"):
                self.write(client, cfg)                     # an empty PLC: give it ours
            return None
        if hdr[5] == self.counter:
            return None
        self.counter = hdr[5]
        theirs = from_block(self._read(client))
        return theirs if theirs and any(theirs[k] != cfg.get(k) for k in theirs) else None


def recipients(cfg):
    return [a.strip() for a in str(cfg.get("mail_to", "")).replace(";", ",").split(",") if a.strip()]


def send_mail(cfg, subject, body, attachment=None):
    """Raises on any failure, with a message fit for the page."""
    to = recipients(cfg)
    if not cfg.get("smtp_host") or not to:
        raise ValueError("fill in the mail server and at least one recipient")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.get("mail_from") or cfg.get("smtp_user")
    msg["To"] = ", ".join(to)
    msg.set_content(body)
    if attachment:
        name, data = attachment
        msg.add_attachment(data.encode("utf-8"), maintype="text", subtype="csv", filename=name)
    host, port, sec = cfg["smtp_host"], int(cfg["smtp_port"]), cfg.get("smtp_security", "starttls")
    ctx = ssl.create_default_context()
    if sec == "ssl":
        s = smtplib.SMTP_SSL(host, port, timeout=20, context=ctx)
    else:
        s = smtplib.SMTP(host, port, timeout=20)
    try:
        if sec == "starttls":
            s.starttls(context=ctx)
        if cfg.get("smtp_user"):
            s.login(cfg["smtp_user"], cfg.get("smtp_password", ""))
        s.send_message(msg)
    finally:
        try:
            s.quit()
        except (smtplib.SMTPException, OSError):
            pass


def readings(state):
    m, d = state.get("mpb") or {}, state.get("drive") or {}
    f = lambda v, u, dp=2: "–" if v is None else f"{v:.{dp}f} {u}"   # noqa: E731
    return (f"Vibration {f(m.get('v'), 'mm/s')} (X {f(m.get('vx'), '')}, Y {f(m.get('vy'), '')}, Z {f(m.get('vz'), '')})\n"
            f"Temperature {f(m.get('temp'), '°C', 1)}\n"
            f"Drive: {d.get('state', '–')}, {d.get('speed') if d.get('speed') is not None else '–'} rpm")


def summary_text(rows, period):
    per = period / 60
    vmax, at, warn, alarm, shock, run = None, None, 0, 0, 0, 0
    for r in rows:
        v = max((x for x in r[1:4] if x is not None), default=None)
        if v is not None and (vmax is None or v > vmax):
            vmax, at = v, r[0]
        alarm += bool(r[6] & 2)
        warn += bool(r[6] & 1) and not r[6] & 2
        shock += bool(r[6] & 8)
        run += bool(r[6] & 32)
    hm = lambda n: f"{int(n * per) // 60} h {int(n * per) % 60:02d} min"   # noqa: E731
    return (f"Highest vibration: {'–' if vmax is None else f'{vmax:.2f} mm/s at ' + time.strftime('%a %H:%M', time.localtime(at))}\n"
            f"Time in vibration warning: {hm(warn)}\nTime in vibration alarm: {hm(alarm)}\n"
            f"Minutes with a shock: {shock}\nMotor running: {hm(run)}\nSamples: {len(rows)}")


class Alerter:
    """Watches the alarm block the dashboard already reads. tick() every couple of seconds from its own thread."""

    def __init__(self, me, send=send_mail):
        self.me, self.send = me, send
        self.last_seq = None           # newest alarm event seen
        self.queue = []                # warnings and clears waiting for the next batch
        self.last_batch = 0.0
        self.summary_day = None
        self.lock = threading.Lock()
        self.status = {"sent": 0, "last_sent": None, "last_subject": "", "error": "", "error_at": None}

    # ---- the PLC words shared with the other screen
    def _claim(self, client, word, value):
        """True if we may send. client None: no PLC (simulation), always ours."""
        if client is None:
            return True
        time.sleep(random.uniform(0, 1.5))         # so the two screens rarely try at the same moment
        cur = client.read_holding(CLAIM, 3)
        if word == 0 and not newer(value, cur[0]):
            return False                           # the other screen already sent up to here
        if word == 2 and cur[2] == value:
            return False
        client.write_registers(CLAIM + word, [value])
        client.write_registers(CLAIM + 1, [self.me])
        time.sleep(0.4)
        return client.read_holding(CLAIM + 1, 1)[0] == self.me

    def _sent(self, subject):
        self.status.update(sent=self.status["sent"] + 1, last_sent=time.time(), last_subject=subject, error="")

    def _failed(self, e):
        self.status.update(error=str(e) or e.__class__.__name__, error_at=time.time())

    def tick(self, cfg, state, client, rows_24h, now=None):
        """cfg: settings; state: the dashboard state (with "alarms"); client: Modbus client or None;
        rows_24h: a function returning (rows, period) for the summary."""
        now = now or time.time()
        a = state.get("alarms")
        if not cfg.get("alerts_on") or not a:
            self.last_seq = a["seq"] if a else self.last_seq
            return
        backlog = []
        if self.last_seq is None:
            self.last_seq = a["seq"]               # start: what was there is history, except what nobody sent
            if client is not None:
                done = client.read_holding(CLAIM, 1)[0]
                if done:                           # 0: alerts never went out from here, nothing is owed
                    backlog = [e for e in a["log"] if newer(e["seq"], done) and not newer(e["seq"], a["seq"])
                               and self._recent(e, now) and self._wanted(e, cfg)]
        if a["seq"] != self.last_seq and not newer(a["seq"], self.last_seq):
            self.last_seq = a["seq"]               # the history was cleared
        new = sorted((e for e in a["log"] if newer(e["seq"], self.last_seq) and not newer(e["seq"], a["seq"])),
                     key=lambda e: (e["seq"] - self.last_seq) % 30000)
        self.last_seq = a["seq"]
        urgent = [e for e in new if e["type"] == "raised" and e["prio"] == 1]
        for e in new:
            if e in urgent:
                continue
            if (e["type"] == "raised" and cfg.get("alert_warnings")) or (e["type"] == "cleared" and cfg.get("alert_cleared")):
                self.queue.append(e)
        tag = "[SIMULATION] " if state.get("mode") == "sim" else ""
        asset = cfg.get("asset", "Demo motor")
        try:
            if backlog:
                backlog.sort(key=lambda e: (e["seq"] - a["seq"] - 1) % 30000)      # oldest first, the newest last
                if self._claim(client, 0, backlog[-1]["seq"]):
                    subject = f"{tag}While no screen was sending, on {asset}: {len(backlog)} alarm event" + ("s" if len(backlog) > 1 else "")
                    self.send(cfg, subject, "Nothing could send these when they happened (no screen with internet was connected).\n\n"
                              + self._body(a, state, backlog))
                    self._sent(subject)
            if urgent:
                top = max(e["seq"] for e in new)
                if self._claim(client, 0, top):
                    names = ", ".join(dict.fromkeys(e["text"] for e in urgent))
                    subject = f"{tag}ALARM on {asset}: {names}"
                    self.send(cfg, subject, self._body(a, state, urgent + self.queue))
                    self._sent(subject)
                    self.queue, self.last_batch = [], now
                else:
                    self.queue = []
            if self.queue and now - self.last_batch >= BATCH_S:
                top = max(e["seq"] for e in self.queue)
                if self._claim(client, 0, top):
                    raised = [e for e in self.queue if e["type"] == "raised"]
                    names = ", ".join(dict.fromkeys(e["text"] for e in raised)) or "alarms cleared"
                    subject = f"{tag}Warning on {asset}: {names}"
                    self.send(cfg, subject, self._body(a, state, self.queue))
                    self._sent(subject)
                self.queue, self.last_batch = [], now
            hour = int(cfg.get("summary_hour", -1))
            lt = time.localtime(now)
            day = history.bcd(lt.tm_mon * 100 + lt.tm_mday)
            if client is None and self.summary_day is None:
                self.summary_day = day             # no PLC to remember it: don't send one at every restart
            if 0 <= hour <= 23 and lt.tm_hour >= hour and self.summary_day != day:
                self.summary_day = day
                if self._claim(client, 2, day):
                    rows, period = rows_24h()
                    subject = f"{tag}Daily summary for {asset}, {time.strftime('%a %d %b', lt)}"
                    body = (f"The last 24 hours on {asset}, from the PLC's history:\n\n{summary_text(rows, period)}\n\n"
                            f"Now:\n{readings(state)}\n\nActive alarms: {self._active(a)}\n\nThe samples are attached as CSV.")
                    self.send(cfg, subject, body, (f"machine-history-{time.strftime('%Y-%m-%d', lt)}.csv", history.to_csv(rows)))
                    self._sent(subject)
        except Exception as e:  # noqa: BLE001 - shown in Setup; the next event tries again
            self._failed(e)

    @staticmethod
    def _recent(e, now):
        try:
            return now - time.mktime(time.strptime(e["at"] or "", "%Y-%m-%d %H:%M:%S")) <= BACKLOG_S
        except ValueError:
            return False

    @staticmethod
    def _wanted(e, cfg):
        if e["type"] == "raised":
            return e["prio"] == 1 or bool(cfg.get("alert_warnings"))
        return e["type"] == "cleared" and bool(cfg.get("alert_cleared"))

    @staticmethod
    def _active(a):
        act = [i["text"] for i in a["items"] if i["state"] in (1, 2)]
        return ", ".join(act) if act else "none"

    def _body(self, a, state, events):
        lines = [f"{e['at'] or '–'}  {e['text']} {e['type']}" + ("  (critical)" if e["prio"] == 1 else "") for e in events]
        return ("What happened (PLC clock):\n" + "\n".join(lines) + f"\n\nNow:\n{readings(state)}\n\n"
                f"Active alarms: {self._active(a)}\n\nAcknowledge on the dashboard's Alarms tab.")

    def test(self, cfg, state):
        subject = f"Test from the {cfg.get('asset', 'Demo motor')} dashboard"
        try:
            self.send(cfg, subject, "The email alerts are set up. Alarms and the daily summary will come to this address.\n\n"
                      f"Now:\n{readings(state)}")
        except Exception as e:  # noqa: BLE001
            self._failed(e)
            return str(e) or e.__class__.__name__
        self._sent(subject)
        return None
