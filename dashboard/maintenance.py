"""Predictive maintenance from the PLC's history (history.py, one sample a minute).

What it works out, from the minutes the motor was running:
  * what "normal" is for this machine: the vibration on each axis, and the temperature, at each speed
    (speed bands of 300 rpm), from the first days of data or from the last "maintenance done"
  * each day since, how far each axis is from its normal at the same speed (1.00 = normal), so a day
    spent at a faster speed doesn't look like wear
  * the trend of that over the last two weeks, turned into mm/s at the speed where the machine
    vibrates most, and the days until it reaches the warning and alarm limits at that rate
  * the same for temperature, and how often shocks come compared with normal

What it can't do: say *why*. One-minute peak values show that the machine is getting worse and how
fast; telling a bearing from misalignment or looseness needs the sensor's spectrum, which we don't log.

rows: [t, vx, vy, vz, temp, speed, flags] (history.row_of); flags bit 3 shock, bit 4 data lost, bit 5 running.
"""
import csv
import math
import os
import random
import threading
import time

AXES = ("x", "y", "z")
BAND_RPM = 300                  # speed bands
MIN_RPM = 100                   # slower than this counts as stopped
MIN_DAY = 30                    # running minutes for a day to count
MIN_BAND = 20                   # running minutes at a speed before we know its normal
BASE_DAYS_MAX = 7               # normal is learnt from up to this many days
TREND_DAYS = 14                 # the trend looks back this far
MIN_TREND = 3                   # days needed for a trend
WATCH_PCT, ACT_PCT = 25, 60     # % above normal: worth watching, act
WATCH_DAYS, ACT_DAYS = 30, 7    # days to the warning limit: worth watching, act
SHOCK, LOST, RUNNING = 8, 16, 32


def day_of(t):
    return time.strftime("%Y-%m-%d", time.localtime(t))


def median(v):
    v = sorted(v)
    n = len(v)
    return None if not n else v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def fit(points):
    """Least squares through (x, y): slope, intercept, r^2."""
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    sxx = sum((p[0] - mx) ** 2 for p in points)
    if not sxx:
        return 0.0, my, 0.0
    sxy = sum((p[0] - mx) * (p[1] - my) for p in points)
    syy = sum((p[1] - my) ** 2 for p in points)
    slope = sxy / sxx
    r2 = sxy * sxy / (sxx * syy) if syy else 1.0
    return slope, my - slope * mx, r2


def band_of(speed):
    return int(round(abs(speed) / BAND_RPM))


def band_name(b):
    lo, hi = max(MIN_RPM, b * BAND_RPM - BAND_RPM // 2), b * BAND_RPM + BAND_RPM // 2
    return f"{lo}-{hi} rpm"


def days_to(level, slope, limit):
    """Days until level reaches limit at slope per day; 0 if it already has, None if it isn't heading there."""
    if level is None or limit is None:
        return None
    if level >= limit:
        return 0.0
    if slope <= 1e-6:
        return None
    return (limit - level) / slope


def analyse(rows, cfg, now=None, since=None):
    """The maintenance outlook, as a dict for the page and the daily email.

    rows: history rows, any order. cfg: zone_bc (warning), zone_cd (alarm), temp_warn.
    since: epoch seconds, the last "maintenance done": only data after it counts."""
    now = now or time.time()
    warn, alarm, twarn = cfg.get("zone_bc", 2.8), cfg.get("zone_cd", 4.5), cfg.get("temp_warn", 70.0)
    run = [r for r in rows if r[5] is not None and abs(r[5]) >= MIN_RPM and r[6] & RUNNING and not r[6] & LOST
           and r[1] is not None and (since is None or r[0] >= since)]
    run.sort(key=lambda r: r[0])
    by_day = {}
    for r in run:
        by_day.setdefault(day_of(r[0]), []).append(r)
    days = [d for d in sorted(by_day) if len(by_day[d]) >= MIN_DAY]
    out = {"generated": now, "since": since, "limits": {"warning": warn, "alarm": alarm, "temp": twarn},
           "running_hours": round(len(run) / 60, 1), "days": len(days), "status": "learning",
           "axes": {}, "temp": None, "shocks": None, "series": [], "findings": [], "confidence": "none"}
    if len(days) < 2:
        need = 2 - len(days)
        out["findings"].append(
            f"Learning what normal looks like: {len(days)} day{'' if len(days) == 1 else 's'} with at least "
            f"{MIN_DAY} minutes of running so far. A first trend needs {MIN_TREND} days, a reliable one about two weeks."
            if days else f"Not enough running data yet. We need days with at least {MIN_DAY} minutes of running"
            f" ({need} more to start).")
        return out

    # ---- normal: the first days
    nbase = min(BASE_DAYS_MAX, max(1, len(days) // 3))
    base_days = days[:nbase]
    out["baseline"] = {"from": base_days[0], "to": base_days[-1], "days": nbase}
    # each speed learns from its own first days, so a speed first used later still gets a normal
    band_days = {}
    for d in days:
        for b in {band_of(r[5]) for r in by_day[d]}:
            band_days.setdefault(b, []).append(d)
    by_band = {}
    for b, bd in band_days.items():
        first = set(bd[:min(BASE_DAYS_MAX, max(1, len(bd) // 3))])
        by_band[b] = [r for d in sorted(first) for r in by_day[d] if band_of(r[5]) == b]
    base = {}
    for b, rs in by_band.items():
        if len(rs) < MIN_BAND:
            continue
        temps = [r[4] for r in rs if r[4] is not None]
        base[b] = {"n": len(rs), "v": [median([r[1 + i] for r in rs]) for i in range(3)],
                   "t": median(temps) if temps else None}
    if not base:
        out["findings"].append("Not enough running at any one speed yet to learn what normal looks like.")
        return out
    out["bands"] = [{"band": band_name(b), "minutes": base[b]["n"], "x": round(base[b]["v"][0], 2),
                     "y": round(base[b]["v"][1], 2), "z": round(base[b]["v"][2], 2),
                     "temp": None if base[b]["t"] is None else round(base[b]["t"], 1)} for b in sorted(base)]

    # ---- each day against normal, at the same speed
    series = []
    for d in days:
        idx = [[], [], []]
        tdelta, shocks, n = [], 0, 0
        for r in by_day[d]:
            b = base.get(band_of(r[5]))
            if not b:
                continue
            n += 1
            for i in range(3):
                if b["v"][i] > 0.01 and r[1 + i] is not None:
                    idx[i].append(r[1 + i] / b["v"][i])
            if b["t"] is not None and r[4] is not None:
                tdelta.append(r[4] - b["t"])
            shocks += bool(r[6] & SHOCK)
        if n < MIN_DAY:
            continue
        t_mid = time.mktime(time.strptime(d + " 12:00", "%Y-%m-%d %H:%M"))
        series.append({"day": d, "t": t_mid, "minutes": n, "index": [median(v) if v else None for v in idx],
                       "temp_delta": median(tdelta) if tdelta else None, "shocks": shocks,
                       "baseline": d in base_days})
    if len(series) < 2:
        out["findings"].append("Not enough running at the speeds we know yet to compare days.")
        return out

    recent = [s for s in series if s["t"] >= series[-1]["t"] - TREND_DAYS * 86400]
    if len(recent) < MIN_TREND:
        recent = series[-MIN_TREND:]
    worst_band = max(base, key=lambda b: max(base[b]["v"]))     # where the machine vibrates most
    hot_band = max((b for b in base if base[b]["t"] is not None), key=lambda b: base[b]["t"], default=None)
    statuses = []

    # ---- vibration, per axis
    for i, ax in enumerate(AXES):
        pts = [((s["t"] - now) / 86400, s["index"][i]) for s in recent if s["index"][i] is not None]
        ref = base[worst_band]["v"][i]
        if len(pts) < 2 or ref <= 0.01:
            continue
        if len(pts) >= MIN_TREND:
            slope, icept, r2 = fit(pts)
            last_x = pts[-1][0]
            now_idx = icept + slope * last_x
        else:
            slope, r2, now_idx = 0.0, 0.0, pts[-1][1]
        now_idx = max(0.0, now_idx)
        level, per_day = now_idx * ref, slope * ref
        dw, da = days_to(level, per_day, warn), days_to(level, per_day, alarm)
        pct = (now_idx - 1) * 100
        st = ("act" if level >= warn or (dw is not None and dw <= ACT_DAYS) or pct >= ACT_PCT else
              "watch" if pct >= WATCH_PCT or (dw is not None and dw <= WATCH_DAYS) else "ok")
        statuses.append(st)
        out["axes"][ax] = {"status": st, "change_pct": round(pct), "index": round(now_idx, 3),
                           "level": round(level, 2), "per_week": round(per_day * 7, 3),
                           "days_to_warning": None if dw is None else round(dw, 1),
                           "days_to_alarm": None if da is None else round(da, 1),
                           "normal": round(ref, 2), "r2": round(r2, 2), "points": len(pts)}

    # ---- temperature
    tpts = [((s["t"] - now) / 86400, s["temp_delta"]) for s in recent if s["temp_delta"] is not None]
    if hot_band is not None and len(tpts) >= 2:
        if len(tpts) >= MIN_TREND:
            tslope, ticept, _ = fit(tpts)
            tnow = ticept + tslope * tpts[-1][0]
        else:
            tslope, tnow = 0.0, tpts[-1][1]
        tlevel = base[hot_band]["t"] + tnow
        dtw = days_to(tlevel, tslope, twarn)
        tst = ("act" if tlevel >= twarn or (dtw is not None and dtw <= ACT_DAYS) or tnow >= 15 else
               "watch" if tnow >= 6 or (dtw is not None and dtw <= WATCH_DAYS) else "ok")
        statuses.append(tst)
        out["temp"] = {"status": tst, "above_normal": round(tnow, 1), "level": round(tlevel, 1),
                       "per_week": round(tslope * 7, 2), "days_to_warning": None if dtw is None else round(dtw, 1),
                       "normal": round(base[hot_band]["t"], 1)}

    # ---- shocks: last 7 days against normal, per running hour
    def rate(ss):
        mins = sum(s["minutes"] for s in ss)
        return sum(s["shocks"] for s in ss) / (mins / 60) if mins else 0.0
    base_rate = rate([s for s in series if s["baseline"]])
    last7 = [s for s in series if s["t"] >= series[-1]["t"] - 6 * 86400]
    last_rate = rate(last7)
    sst = ("watch" if last_rate >= 1 and last_rate >= 3 * max(base_rate, 0.1) else "ok")
    statuses.append(sst)
    out["shocks"] = {"status": sst, "per_hour": round(last_rate, 2), "normal_per_hour": round(base_rate, 2),
                     "last_7_days": sum(s["shocks"] for s in last7)}

    # ---- overall, and in words
    order = {"ok": 0, "watch": 1, "act": 2}
    out["status"] = max(statuses, key=order.get) if statuses else "ok"
    good_fit = [a for a in out["axes"].values() if a["points"] >= 7 and a["r2"] >= 0.3]
    out["confidence"] = "good" if len(days) >= 14 and good_fit else "fair" if len(days) >= 5 else "low"
    out["worst_speed"] = band_name(worst_band)
    out["series"] = [{"day": s["day"], "baseline": s["baseline"], "minutes": s["minutes"], "shocks": s["shocks"],
                      "level": [None if s["index"][i] is None else round(s["index"][i] * base[worst_band]["v"][i], 3)
                                for i in range(3)],
                      "temp_delta": None if s["temp_delta"] is None else round(s["temp_delta"], 1)} for s in series]
    out["findings"] = findings(out)
    return out


def when(days):
    if days is None:
        return None
    if days <= 0:
        return "now"
    if days < 1.5:
        return "about a day"
    if days < 14:
        return f"about {round(days)} days"
    if days < 70:
        return f"about {round(days / 7)} weeks"
    return "more than two months"


def findings(o):
    """The outlook in plain words, most urgent first."""
    lim, out = o["limits"], []
    axes = sorted(o["axes"].items(), key=lambda kv: (kv[1]["days_to_warning"] is None,
                                                       kv[1]["days_to_warning"] or 0, -kv[1]["change_pct"]))
    for ax, a in axes:
        name = f"{ax.upper()} axis vibration"
        trend = (f"rising {a['per_week']:.2f} mm/s a week" if a["per_week"] > 0.005 else
                 f"falling {-a['per_week']:.2f} mm/s a week" if a["per_week"] < -0.005 else "steady")
        if a["days_to_warning"] == 0:
            out.append(f"{name} is at the warning limit ({a['level']:.2f} of {lim['warning']} mm/s at "
                       f"{o['worst_speed']}). Plan a check now.")
        elif a["days_to_warning"] is not None and a["days_to_warning"] <= 120:
            out.append(f"{name} is {a['change_pct']:+d} % against normal and {trend}: {when(a['days_to_warning'])} "
                       f"to the warning limit ({lim['warning']} mm/s at {o['worst_speed']})"
                       + (f", {when(a['days_to_alarm'])} to the alarm limit." if a["days_to_alarm"] is not None else "."))
        elif a["status"] != "ok":
            out.append(f"{name} is {a['change_pct']:+d} % against normal, {trend}.")
    t = o["temp"]
    if t and t["status"] != "ok":
        out.append(f"Temperature runs {t['above_normal']:+.1f} °C against normal for the speed"
                   + (f": {when(t['days_to_warning'])} to the {lim['temp']} °C warning at this rate." if t["days_to_warning"] else "."))
    s = o["shocks"]
    if s and s["status"] != "ok":
        out.append(f"Shocks are more frequent: {s['per_hour']:.1f} an hour over the last week, against "
                   f"{s['normal_per_hour']:.1f} normally. Check for loose parts or something striking the machine.")
    if not out:
        out.append("Everything is close to normal for this machine. Nothing is heading for a limit.")
    if o["confidence"] == "low":
        out.append(f"Low confidence: only {o['days']} days of data. The forecast firms up after about two weeks.")
    return out


def email_text(o):
    """The outlook for the daily summary email."""
    head = {"ok": "Normal", "watch": "Keep an eye on it", "act": "Plan maintenance", "learning": "Learning"}[o["status"]]
    lines = [f"{head} ({o['days']} days of running, {o['confidence']} confidence)."
             if o["status"] != "learning" else head + "."]
    lines += ["- " + f for f in o["findings"]]
    for ax, a in o["axes"].items():
        lines.append(f"  {ax.upper()}: {a['level']:.2f} mm/s (normal {a['normal']:.2f}), {a['change_pct']:+d} %, "
                     f"{a['per_week']:+.2f} mm/s a week, warning {when(a['days_to_warning']) or 'not heading for it'}")
    return "\n".join(lines)


# ---- where the rows come from

class FileRows:
    """Every day file in the history folder, cached until a file changes."""

    def __init__(self, folder):
        self.folder = folder
        self.cache = {}                 # name -> (mtime, size, rows)
        self.lock = threading.Lock()

    def rows(self):
        out = []
        if not self.folder or not os.path.isdir(self.folder):
            return out
        with self.lock:
            names = sorted(n for n in os.listdir(self.folder) if n.endswith(".csv"))
            for name in names:
                p = os.path.join(self.folder, name)
                try:
                    st = os.stat(p)
                    c = self.cache.get(name)
                    if not c or c[0] != st.st_mtime or c[1] != st.st_size:
                        c = (st.st_mtime, st.st_size, read_csv(p))
                        self.cache[name] = c
                    out += c[2]
                except OSError:
                    continue
            for gone in set(self.cache) - set(names):
                del self.cache[gone]
        return out


def read_csv(path):
    from history import FLAGS
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                t = time.mktime(time.strptime(r["time"], "%Y-%m-%d %H:%M:%S"))
                num = lambda k: float(r[k]) if r[k] != "" else None   # noqa: E731
                rows.append([int(t), num("vib_x_mm_s"), num("vib_y_mm_s"), num("vib_z_mm_s"), num("temp_c"),
                             int(float(r["speed_rpm"] or 0)),
                             sum(int(r[k] == "1") << i for i, k in enumerate(FLAGS))])
            except (ValueError, KeyError):
                continue
    return rows


def demo_rows(now, days=42, seed=7):
    """Made-up history for simulation mode: four steady weeks, then a bearing starting to go.
    Day shifts (7:00-17:00, not Sundays) at 1200 rpm, with an hour at 600 rpm after lunch."""
    rnd = random.Random(seed)
    out = []
    start = now - days * 86400
    wear_from = start + 28 * 86400
    t = int(start // 60 * 60)
    while t < now:
        lt = time.localtime(t)
        hour = lt.tm_hour + lt.tm_min / 60
        on = 7 <= hour < 17 and lt.tm_wday != 6
        if on:
            speed = 600 if 13 <= hour < 14 else 1200
            k = 0.55 if speed == 600 else 1.0
            wear = max(0.0, (t - wear_from) / 86400)
            extra = 0.035 * wear + 0.0012 * wear * wear              # creeping, then faster
            v = (1.05 + 0.12 * math.sin(hour) + rnd.uniform(-0.08, 0.12)) * k
            vz = v + extra * k
            vx = 0.8 * v + 0.6 * extra * k
            vy = 0.6 * v + 0.15 * extra * k
            temp = 38 + 7 * min(1.0, (hour - 7) / 2) * k + 0.08 * wear + rnd.uniform(-0.4, 0.4)
            shock = rnd.random() < (0.002 + 0.0004 * wear)
            flags = RUNNING | (SHOCK if shock else 0) | (1 if max(vx, vy, vz) >= 2.8 else 0) | (2 if max(vx, vy, vz) >= 4.5 else 0)
            out.append([t, round(vx, 2), round(vy, 2), round(vz, 2), round(temp, 1), speed, flags])
        elif lt.tm_min % 10 == 0:
            out.append([t, 0.12, 0.1, 0.14, round(24 + rnd.uniform(-0.3, 0.3), 1), 0, 0])
        t += 60
    return out
