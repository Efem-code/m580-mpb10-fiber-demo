package ca.guillevin.m580tv;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.LinkedList;
import java.util.List;
import java.util.Map;

/**
 * The dashboard's server.py for the TV: same status and command maps, same drive rules, same JSON for the
 * page. Live mode only. Keep the two in step. Writes the drive command block only when "Allow drive
 * control" is ticked; the PLC decides whether the drive runs, and its watchdog stops the motor if this stops.
 */
class Engine {
    interface Store {
        String load();
        void save(String json);
    }

    private static final int NA = -32768, BLOCK = 32;
    private static final int ALARM_BASE = 1300, ALARM_CMD = 1290, LIMITS = 1280, ENABLE = 1286, LOG_START = 60, ALARM_SIZE = 444, SLOTS = 64;
    private static final String[] ALARM_NAMES = {"", "Drive fault", "Drive connection lost", "SIG300 connection lost",
        "MPB10 data lost", "RIO drop lost", "RIO ring open", "Vibration warning", "Vibration alarm", "Temperature warning",
        "Dashboard link lost", "Shock detected", "Spare"};
    private static final int[] ALARM_PRIO = {0, 1, 1, 2, 2, 1, 2, 2, 1, 2, 2, 2, 2};
    private static final String[] ALARM_EVENTS = {"?", "raised", "cleared", "acknowledged"};
    private static final String[][] REACH = {{"plc_host", "502"}, {"noc_host", "502"}, {"cra_host", "502"},
        {"sig300_host", "80"}, {"atv_host", "502"}, {"ap_host", "80"}};

    private final Store store;
    private final Object lock = new Object();
    private Map<String, Object> cfg = defaults();
    private PlcClient plc;
    private volatile boolean running = true;
    private Map<String, Object> state = new LinkedHashMap<String, Object>();
    private Map<String, Object> prev;
    private final ArrayDeque<List<Object>> history = new ArrayDeque<List<Object>>();
    private final LinkedList<Map<String, Object>> events = new LinkedList<Map<String, Object>>();
    private final ArrayDeque<List<Object>> marks = new ArrayDeque<List<Object>>();
    private final ArrayDeque<Double> reads = new ArrayDeque<Double>();
    private Map<String, Object> reach = new LinkedHashMap<String, Object>();
    private Integer lastHb;
    private double hbChanged = now();
    private int errors, seq, cmdHb, viewers;
    private String lastError = "";
    private double lastViewer;
    private boolean paused;
    // the command block
    private boolean run, reverse, jogRev;
    private int speed = 900;
    private double stopUntil, resetUntil, jogUntil;
    // demo sequence
    private boolean progOn, progWait, progRev;
    private int progI, progCycle;
    private double progT0, progWaitSince;
    // alarms
    private int[] alarmBlock;
    private double alarmRead;
    private boolean limitsDirty = true;

    private static double now() {
        return System.currentTimeMillis() / 1000.0;
    }

    private static Map<String, Object> step(String dir, int rpm, int hold) {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        m.put("dir", dir);
        m.put("rpm", rpm);
        m.put("hold", hold);
        return m;
    }

    private static Map<String, Object> defaults() {
        Map<String, Object> c = new LinkedHashMap<String, Object>();
        c.put("mode", "live");
        c.put("laptop_ip", "192.168.10.100");
        c.put("plc_host", "192.168.10.10");
        c.put("noc_host", "192.168.20.10");
        c.put("cra_host", "192.168.10.20");
        c.put("cra_drop", 3);
        c.put("sig300_host", "192.168.20.20");
        c.put("atv_host", "192.168.20.30");
        c.put("netmask", "255.255.255.0");
        c.put("ap_host", "192.168.20.1");
        c.put("cobot_host", "192.168.20.94");
        c.put("gateway", "");
        c.put("plc_port", 502);
        c.put("unit_id", 255);
        c.put("base", 1000);
        c.put("cmd_base", 1100);
        c.put("zone_ab", 1.4);
        c.put("zone_bc", 2.8);
        c.put("zone_cd", 4.5);
        c.put("temp_warn", 70.0);
        c.put("asset", "Demo motor");
        c.put("allow_control", false);
        c.put("max_rpm", 1500);
        c.put("nominal_rpm", 1500);
        c.put("jog_rpm", 150);
        c.put("accel_s", 5.0);
        c.put("decel_s", 5.0);
        c.put("sequence", new ArrayList<Object>(Arrays.asList(step("fwd", 600, 8), step("fwd", 1200, 8), step("fwd", 300, 6),
                step("stop", 0, 6), step("rev", 900, 8), step("rev", 1500, 6), step("stop", 0, 6))));
        c.put("seq_loop", true);
        return c;
    }

    Engine(Store store) {
        this.store = store;
        String saved = store.load();
        if (saved != null) {
            try {
                Map<String, Object> s = map(Json.parse(saved));
                for (Map.Entry<String, Object> e : s.entrySet()) {
                    if (cfg.containsKey(e.getKey()) && !"mode".equals(e.getKey())) cfg.put(e.getKey(), coerce(e.getKey(), e.getValue()));
                }
            } catch (RuntimeException ignored) {            // a bad settings file: start from the defaults
            }
        }
        thread("poll", new Runnable() {
            @Override public void run() {
                loop();
            }
        });
        thread("reach", new Runnable() {
            @Override public void run() {
                reachLoop();
            }
        });
    }

    private static void thread(String name, Runnable r) {
        Thread t = new Thread(r, name);
        t.setDaemon(true);
        t.start();
    }

    void stop() {
        running = false;
    }

    /** The app is off screen: no page is watching, so the run request drops and the writes stop. */
    void setPaused(boolean p) {
        synchronized (lock) {
            paused = p;
        }
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> map(Object o) {
        return (Map<String, Object>) o;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> list(Object o) {
        return (List<Object>) o;
    }

    private double d(String k) {
        return ((Number) cfg.get(k)).doubleValue();
    }

    private int i(String k) {
        return ((Number) cfg.get(k)).intValue();
    }

    private boolean allowed() {
        return Boolean.TRUE.equals(cfg.get("allow_control"));
    }

    private static boolean truthy(Object v) {
        if (v instanceof Boolean) return (Boolean) v;
        String s = String.valueOf(v).toLowerCase();
        return s.equals("1") || s.equals("true") || s.equals("on") || s.equals("yes");
    }

    private Object coerce(String k, Object v) {
        if (k.equals("sequence")) return cleanSequence(v);
        Object def = defaults().get(k);
        if (def instanceof Boolean) return truthy(v);
        if (def instanceof Integer) return (int) Double.parseDouble(String.valueOf(v));
        if (def instanceof Double) return Double.parseDouble(String.valueOf(v));
        return String.valueOf(v);
    }

    private static List<Object> cleanSequence(Object v) {
        if (!(v instanceof List) || list(v).isEmpty() || list(v).size() > 20) throw new IllegalArgumentException("A sequence needs 1 to 20 steps");
        List<Object> out = new ArrayList<Object>();
        for (Object o : list(v)) {
            Map<String, Object> st = map(o);
            String dir = String.valueOf(st.containsKey("dir") ? st.get("dir") : "stop");
            if (!dir.equals("fwd") && !dir.equals("rev") && !dir.equals("stop")) throw new IllegalArgumentException("Unknown direction “" + dir + "”");
            double rpm = st.get("rpm") == null ? 0 : Double.parseDouble(String.valueOf(st.get("rpm")));
            double hold = st.get("hold") == null ? 0 : Double.parseDouble(String.valueOf(st.get("hold")));
            if (hold < 1 || hold > 300) throw new IllegalArgumentException("Hold times must be 1 to 300 s");
            if (!dir.equals("stop") && rpm <= 0) throw new IllegalArgumentException("Forward and reverse steps need a speed above 0");
            Map<String, Object> m = new LinkedHashMap<String, Object>();
            m.put("dir", dir);
            m.put("rpm", dir.equals("stop") ? 0 : (int) rpm);
            m.put("hold", Math.round(hold * 10) / 10.0);
            out.add(m);
        }
        return out;
    }

    private void save() {
        store.save(Json.write(cfg));
    }

    private void event(String level, String text) {
        Map<String, Object> e = new LinkedHashMap<String, Object>();
        e.put("t", now());
        e.put("level", level);
        e.put("text", text);
        events.addFirst(e);
        while (events.size() > 40) events.removeLast();
    }

    // ---- settings
    Map<String, Object> config() {
        synchronized (lock) {
            Map<String, Object> c = new LinkedHashMap<String, Object>(cfg);
            c.put("_warnings", new ArrayList<Object>());
            c.put("_local", true);
            c.put("_app", true);
            return c;
        }
    }

    Map<String, Object> configure(Map<String, Object> changes) {
        synchronized (lock) {
            Map<String, Object> trial = new LinkedHashMap<String, Object>(cfg);
            for (Map.Entry<String, Object> e : changes.entrySet()) {
                if (cfg.containsKey(e.getKey()) && !e.getKey().equals("mode")) trial.put(e.getKey(), coerce(e.getKey(), e.getValue()));
            }
            boolean source = false;
            for (String k : new String[] {"plc_host", "plc_port", "unit_id", "base", "cmd_base"}) source |= !trial.get(k).equals(cfg.get(k));
            for (String k : new String[] {"zone_bc", "zone_cd", "temp_warn"}) limitsDirty |= !trial.get(k).equals(cfg.get(k));
            boolean was = allowed(), is = truthy(trial.get("allow_control"));
            if (is && !was) event("warn", "Drive control from the TV switched ON");
            if (!is && was) {
                run = false;
                event("info", "Drive control from the TV switched off");
            }
            cfg = trial;
            save();
            if (source) {
                if (plc != null) plc.close();
                plc = null;
                run = false;
                lastHb = null;
                prev = null;
                alarmBlock = null;
                event("info", "Connecting to the M580 at " + cfg.get("plc_host"));
            }
            Map<String, Object> r = new LinkedHashMap<String, Object>();
            r.put("errors", new ArrayList<Object>());
            r.put("warnings", new ArrayList<Object>());
            r.put("config", config());
            return r;
        }
    }

    // ---- drive commands from the page; returns an error message, or null
    String drive(String action, Object value) {
        synchronized (lock) {
            if (!allowed()) return "Drive control from the TV is off (Setup tab)";
            if (progOn && Arrays.asList("run", "run_fwd", "run_rev", "jog", "speed", "reverse").contains(action)) {
                return "The demo sequence is running: press Stop sequence (or STOP) first";
            }
            double t = now();
            if (action.equals("plc_enable")) {
                if (plc == null) return "not connected to the PLC";
                boolean dash = "dash".equals(map(value).get("which")), on = truthy(map(value).get("on"));
                try {
                    int addr = ENABLE + (dash ? 1 : 0);
                    plc.write(addr, on ? 1 : 2);
                    sleep(300);                  // S02 clears the word within a scan
                    if (plc.read(addr, 1)[0] != 0) {
                        plc.write(addr, 0);
                        return "The PLC didn’t respond: load the S02 section with aEnableCmd first";
                    }
                } catch (IOException e) {
                    return "couldn't reach the PLC: " + e.getMessage();
                }
                event(on ? "warn" : "info", (dash ? "Dashboard permission" : "Demo enable") + " switched " + (on ? "ON" : "OFF") + " from this screen");
                return null;
            }
            if (action.equals("seq_start")) return seqStart();
            if (action.equals("seq_stop")) {
                seqHalt("stopped by the presenter", "info");
                run = false;
                return null;
            }
            if (action.equals("seq_save")) {
                if (progOn) return "Stop the sequence before editing it";
                try {
                    cfg.put("sequence", cleanSequence(map(value).get("steps")));
                } catch (IllegalArgumentException e) {
                    return e.getMessage();
                }
                cfg.put("seq_loop", truthy(map(value).get("loop")));
                save();
                return null;
            }
            if (action.equals("run") || action.equals("run_fwd") || action.equals("run_rev")) {
                if (!action.equals("run")) {
                    boolean rev = action.equals("run_rev");
                    if (run && rev != reverse) return "Stop the motor before changing direction";
                    reverse = rev;
                }
                run = true;
                stopUntil = 0;
                jogUntil = 0;
                event("info", "Start " + (reverse ? "reverse" : "forward") + " requested at " + speed + " rpm");
            } else if (action.equals("jog")) {
                if (run) return "Jog only works with the motor stopped";
                boolean rev = "rev".equals(value);
                if (t >= jogUntil) event("info", "Jog " + (rev ? "reverse" : "forward") + " at " + i("jog_rpm") + " rpm");
                jogUntil = t + 0.5;
                jogRev = rev;
                stopUntil = 0;
            } else if (action.equals("jog_stop")) {
                jogUntil = 0;
            } else if (action.equals("ramps")) {
                double acc = Double.parseDouble(String.valueOf(map(value).get("accel")));
                double dec = Double.parseDouble(String.valueOf(map(value).get("decel")));
                if (acc < 0.1 || acc > 60 || dec < 0.1 || dec > 60) return "Ramp times must be 0.1 to 60 s";
                cfg.put("accel_s", Math.round(acc * 10) / 10.0);
                cfg.put("decel_s", Math.round(dec * 10) / 10.0);
                save();
                event("info", String.format("Ramps set: accel %.1f s, decel %.1f s", acc, dec));
            } else if (action.equals("stop")) {
                run = false;
                stopUntil = t + 1;
                jogUntil = 0;
                event("info", "Stop requested");
                seqHalt("STOP pressed", "info");
            } else if (action.equals("speed")) {
                speed = Math.max(0, Math.min((int) Double.parseDouble(String.valueOf(value)), i("max_rpm")));
            } else if (action.equals("reverse")) {
                if (run) return "Stop the motor before changing direction";
                reverse = truthy(value);
            } else if (action.equals("reset")) {
                resetUntil = t + 1;
                event("info", "Fault reset requested");
            } else {
                return "unknown action";
            }
            return null;
        }
    }

    // ---- demo sequence
    private List<Object> steps() {
        return list(cfg.get("sequence"));
    }

    private Map<String, Object> sub(String k) {
        Object o = state.get(k);
        return o == null ? new LinkedHashMap<String, Object>() : map(o);
    }

    private String seqStart() {
        if (Boolean.TRUE.equals(sub("drive").get("fault"))) return "Clear the drive fault first";
        jogUntil = 0;
        progOn = true;
        progI = -1;
        progCycle = 1;
        progWait = false;
        event("info", "Demo sequence started (" + steps().size() + " steps" + (truthy(cfg.get("seq_loop")) ? ", looping" : "") + ")");
        seqNext(now());
        return null;
    }

    private void seqHalt(String why, String level) {
        if (progOn) {
            progOn = false;
            event(level, "Demo sequence ended: " + why);
        }
    }

    private void seqNext(double t) {
        progI++;
        if (progI >= steps().size()) {
            if (!truthy(cfg.get("seq_loop"))) {
                run = false;
                seqHalt("finished", "info");
                return;
            }
            progI = 0;
            progCycle++;
        }
        Map<String, Object> st = map(steps().get(progI));
        progT0 = t;
        progWait = false;
        if ("stop".equals(st.get("dir"))) {
            run = false;
            return;
        }
        boolean rev = "rev".equals(st.get("dir"));
        speed = Math.min(((Number) st.get("rpm")).intValue(), i("max_rpm"));
        Object sp = sub("drive").get("speed");
        int cur = sp == null ? 0 : ((Number) sp).intValue();
        if ((run && reverse != rev) || (Math.abs(cur) > 30 && (cur < 0) != rev)) {
            run = false;
            progWait = true;
            progWaitSince = t;
            progRev = rev;
        } else {
            reverse = rev;
            run = true;
        }
    }

    private void seqTick(double t, boolean alive) {
        if (!progOn) return;
        Map<String, Object> dr = sub("drive"), st = map(steps().get(progI));
        if (!allowed()) {
            seqHalt("drive control switched off", "warn");
            return;
        }
        if (Boolean.TRUE.equals(dr.get("fault"))) {
            run = false;
            seqHalt("drive fault", "bad");
            return;
        }
        if (!Boolean.TRUE.equals(sub("links").get("fresh"))) {
            run = false;
            seqHalt("lost the M580", "bad");
            return;
        }
        if (!alive) {
            seqHalt("no dashboard page open", "warn");
            return;
        }
        Object sp = dr.get("speed");
        int cur = sp == null ? 0 : ((Number) sp).intValue();
        if (progWait) {
            if (Math.abs(cur) < 30 || t - progWaitSince > 20) {
                reverse = progRev;
                run = true;
                progWait = false;
                progT0 = t;
            }
            return;
        }
        boolean moving = !"stop".equals(st.get("dir"));
        if (moving && !run) {
            seqHalt("the run was stopped by the PLC", "warn");
            return;
        }
        if (moving && t - progT0 > 4 && !Boolean.TRUE.equals(dr.get("running"))) {
            run = false;
            seqHalt("the drive didn’t start: check the Ready to run list", "bad");
            return;
        }
        if (t - progT0 >= ((Number) st.get("hold")).doubleValue()) seqNext(t);
    }

    private Map<String, Object> seqState(double t) {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        m.put("on", progOn);
        if (progOn) {
            Map<String, Object> st = map(steps().get(progI));
            m.put("i", progI);
            m.put("n", steps().size());
            m.put("cycle", progCycle);
            m.put("wait", progWait);
            m.put("left", progWait ? null : (Object) (Math.round(Math.max(0, ((Number) st.get("hold")).doubleValue() - (t - progT0)) * 10) / 10.0));
            m.put("step", st);
        }
        return m;
    }

    // ---- loops
    private void reachLoop() {
        while (running) {
            Map<String, Object> out = new LinkedHashMap<String, Object>();
            Map<String, Object> c;
            synchronized (lock) {
                c = new LinkedHashMap<String, Object>(cfg);
            }
            for (String[] r : REACH) {
                Map<String, Object> m = new LinkedHashMap<String, Object>();
                int port = Integer.parseInt(r[1]);
                long t0 = System.currentTimeMillis();
                boolean ok = false;
                Socket s = new Socket();
                try {
                    s.connect(new InetSocketAddress(String.valueOf(c.get(r[0])), port), 600);
                    ok = true;
                } catch (IOException | RuntimeException ignored) {
                } finally {
                    try {
                        s.close();
                    } catch (IOException ignored) {
                    }
                }
                m.put("ok", ok);
                m.put("ms", ok ? (Object) (System.currentTimeMillis() - t0) : null);
                m.put("port", port);
                out.put(r[0], m);
            }
            out.put("local", new ArrayList<Object>());
            synchronized (lock) {
                reach = out;
            }
            sleep(3000);
        }
    }

    private static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException ignored) {
        }
    }

    private void loop() {
        double lastHist = 0;
        while (running) {
            sleep(200);
            double t = now();
            synchronized (lock) {
                boolean alive = !paused && (viewers > 0 || t - lastViewer < 2);
                if (!alive && run) {
                    run = false;
                    event("warn", "No dashboard page open: run request dropped");
                }
                boolean jog = alive && t < jogUntil && !run;
                boolean cmdRun = run || jog, cmdStop = t < stopUntil, cmdRev = jog ? jogRev : reverse, cmdReset = t < resetUntil;
                int cmdSpeed = jog ? Math.min(i("jog_rpm"), i("max_rpm")) : speed;
                int[] regs = null;
                String err = "";
                try {
                    if (plc == null) plc = new PlcClient(String.valueOf(cfg.get("plc_host")), i("plc_port"), i("unit_id"));
                    regs = plc.read(i("base"), BLOCK);
                    if (allowed() && !paused) {
                        cmdHb = (cmdHb + 1) & 0x7FFF;
                        boolean r = cmdRun && alive && !cmdStop;
                        int bits = (r ? 1 : 0) | (cmdStop ? 2 : 0) | (cmdReset ? 4 : 0) | (cmdRev ? 8 : 0) | (jog ? 16 : 0);
                        plc.write(i("cmd_base"), cmdHb, bits, Math.min(Math.max(cmdSpeed, 0), i("max_rpm")),
                                (int) Math.round(d("accel_s") * 10), (int) Math.round(d("decel_s") * 10));
                    }
                    reads.addLast(t);
                    while (reads.size() > 50) reads.removeFirst();
                } catch (IOException | RuntimeException e) {
                    err = e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
                    errors++;
                    lastError = err;
                    regs = null;
                }
                state = decode(regs, err, t, alive, jog, cmdRun && alive && !cmdStop);
                alarmsTick(t);
                diffEvents();
                seqTick(t, alive);
                state.put("prog", seqState(t));
                Map<String, Object> m = sub("mpb"), dr = sub("drive");
                if (t - lastHist >= 0.5 && (m.get("v") != null || dr.get("speed") != null)) {
                    lastHist = t;
                    history.addLast(new ArrayList<Object>(Arrays.asList(Math.round(t * 10) / 10.0, m.get("v"), m.get("vx"), m.get("vy"),
                            m.get("vz"), m.get("temp"), dr.get("speed"), dr.get("ref"))));
                    while (history.size() > 600) history.removeFirst();
                }
                seq++;
            }
        }
        synchronized (lock) {
            if (plc != null) plc.close();
        }
    }

    private static int s16(int v) {
        return v >= 0x8000 ? v - 0x10000 : v;
    }

    private static Object scaled(int v, double k) {
        int s = s16(v);
        return s == NA ? null : (Object) (Math.round(s / k * 100) / 100.0);
    }

    private static String cia402(Integer eta) {
        if (eta == null) return "–";
        if ((eta & 8) != 0) return "Fault";
        if ((eta & 0x4F) == 0x40) return "Switch on disabled";
        switch (eta & 0x6F) {
            case 0x21: return "Ready to switch on";
            case 0x23: return "Switched on";
            case 0x27: return "Operation enabled";
            case 0x07: return "Quick stop active";
            default: return String.format("0x%04X", eta);
        }
    }

    private Map<String, Object> decode(int[] r, String err, double t, boolean alive, boolean jog, boolean runReq) {
        boolean plcOk = r != null;
        if (plcOk && (lastHb == null || r[0] != lastHb)) {
            lastHb = r[0];
            hbChanged = t;
        }
        boolean fresh = plcOk && t - hbChanged < 2;
        int st = plcOk ? r[1] : 0;
        boolean eip = fresh && (st & 1) != 0, present = eip && (st & 2) != 0;
        Object v = present ? scaled(r[2], 100) : null;
        Map<String, Object> mpb = new LinkedHashMap<String, Object>();
        mpb.put("v", v);
        mpb.put("vx", present ? scaled(r[3], 100) : null);
        mpb.put("vy", present ? scaled(r[4], 100) : null);
        mpb.put("vz", present ? scaled(r[5], 100) : null);
        mpb.put("temp", present ? scaled(r[6], 10) : null);
        mpb.put("shocks", plcOk ? (Object) r[7] : null);
        mpb.put("alarm_word", present ? (Object) r[8] : null);
        mpb.put("warn", (st & 4) != 0);
        mpb.put("alarm", (st & 8) != 0);
        mpb.put("shock", (st & 16) != 0);
        mpb.put("temp_warn", (st & 32) != 0);
        List<Object> raw = null;
        if (present) {
            raw = new ArrayList<Object>();
            for (int i = 9; i < 19; i++) {
                raw.add(r[i] >> 8);
                raw.add(r[i] & 0xFF);
            }
        }
        mpb.put("raw", raw);
        int db = fresh ? r[20] : 0;
        boolean dOk = (db & 1) != 0;
        Map<String, Object> drive = new LinkedHashMap<String, Object>();
        drive.put("healthy", dOk);
        drive.put("ready", (db & 2) != 0);
        drive.put("running", (db & 4) != 0);
        drive.put("fault", (db & 8) != 0);
        drive.put("plc_allows", (db & 16) != 0);
        drive.put("watchdog_ok", (db & 32) != 0);
        drive.put("reverse", (db & 64) != 0);
        drive.put("eta", dOk ? (Object) r[21] : null);
        drive.put("speed", dOk ? (Object) s16(r[22]) : null);
        drive.put("ref", dOk ? (Object) s16(r[23]) : null);
        drive.put("current", dOk ? (Object) (s16(r[24]) / 10.0) : null);
        drive.put("torque", dOk ? (Object) s16(r[25]) : null);
        drive.put("thermal", dOk ? (Object) s16(r[26]) : null);
        drive.put("fault_code", dOk ? (Object) r[27] : null);
        drive.put("cmd", fresh ? (Object) r[28] : null);
        drive.put("accel", dOk ? (Object) (r[30] / 10.0) : null);
        drive.put("decel", dOk ? (Object) (r[31] / 10.0) : null);
        drive.put("state", dOk ? cia402(r[21]) : "No connection");
        int pb = fresh ? r[29] : 0;
        Map<String, Object> panel = new LinkedHashMap<String, Object>();
        panel.put("drop", (pb & 1) != 0);
        panel.put("enable", (pb & 16) != 0);
        panel.put("green", (pb & 32) != 0);
        panel.put("amber", (pb & 64) != 0);
        panel.put("red", (pb & 128) != 0);
        panel.put("ring", (pb & 256) != 0);
        panel.put("ring_known", (pb & 512) != 0);
        if (fresh && (run || jog) && (pb & 16) == 0) {
            run = false;
            jogUntil = 0;
            event("info", "Run dropped: demo enable (xDemoEnable) is off in the PLC");
        }
        String zone = null, health;
        boolean warn = (st & 4) != 0, alarm = (st & 8) != 0, tw = (st & 32) != 0;
        if (v != null) {
            double x = ((Number) v).doubleValue();
            zone = x < d("zone_ab") ? "A" : x < d("zone_bc") ? "B" : x < d("zone_cd") ? "C" : "D";
        }
        health = !present ? "offline" : "D".equals(zone) || alarm ? "alarm" : "C".equals(zone) || warn || tw ? "warning" : "good";
        double rate = 0;
        if (reads.size() > 1) {
            double span = reads.peekLast() - reads.peekFirst();
            rate = span > 0 ? (reads.size() - 1) / span : 0;
        }
        Map<String, Object> s = new LinkedHashMap<String, Object>();
        s.put("t", t);
        s.put("mode", "live");
        s.put("seq", seq);
        Map<String, Object> links = new LinkedHashMap<String, Object>();
        links.put("plc", plcOk);
        links.put("fresh", fresh);
        links.put("eip", eip);
        links.put("iolink", present);
        links.put("atv", fresh && dOk);
        links.put("drop", fresh && (pb & 1) != 0);
        s.put("links", links);
        Map<String, Object> p = new LinkedHashMap<String, Object>();
        p.put("heartbeat", plcOk ? (Object) r[0] : null);
        p.put("rate", Math.round(rate * 10) / 10.0);
        p.put("errors", errors);
        p.put("error", err);
        p.put("last_error", lastError);
        p.put("host", cfg.get("plc_host"));
        s.put("plc", p);
        s.put("mpb", mpb);
        s.put("zone", zone);
        s.put("health", health);
        s.put("drive", drive);
        s.put("panel", panel);
        Map<String, Object> c = new LinkedHashMap<String, Object>();
        c.put("allowed", allowed());
        c.put("writing", allowed());
        c.put("run", run);
        c.put("speed", speed);
        c.put("reverse", reverse);
        c.put("jog", jog);
        c.put("jog_rev", jogRev);
        c.put("jog_rpm", cfg.get("jog_rpm"));
        c.put("accel_s", cfg.get("accel_s"));
        c.put("decel_s", cfg.get("decel_s"));
        c.put("alive", alive);
        c.put("max_rpm", cfg.get("max_rpm"));
        s.put("control", c);
        s.put("sim", null);
        return s;
    }

    private static boolean b(Map<String, Object> m, String k) {
        return Boolean.TRUE.equals(m.get(k));
    }

    private void diffEvents() {
        Map<String, Object> s = state, p = prev;
        Map<String, Object> L = map(s.get("links"));
        if (p == null && !b(L, "plc")) event("bad", "Can’t reach the M580 at " + cfg.get("plc_host") + " (" + map(s.get("plc")).get("error") + ")");
        if (p != null) {
            Map<String, Object> PL = map(p.get("links")), P = map(s.get("panel")), PP = map(p.get("panel"));
            Map<String, Object> D = map(s.get("drive")), PD = map(p.get("drive")), M = map(s.get("mpb")), PM = map(p.get("mpb"));
            if (b(PL, "plc") && !b(L, "plc")) event("bad", "Lost the M580 at " + cfg.get("plc_host") + " (" + map(s.get("plc")).get("error") + ")");
            if (!b(PL, "plc") && b(L, "plc")) event("ok", "M580 connected");
            if (b(PL, "fresh") && b(L, "plc") && !b(L, "fresh")) event("bad", "PLC heartbeat stopped: data is stale");
            if (b(PL, "eip") && !b(L, "eip") && b(L, "fresh")) event("bad", "SIG300 lost: EtherNet/IP connection down (cable on NOC ETH3?)");
            if (!b(PL, "eip") && b(L, "eip")) event("ok", "SIG300 back online");
            if (b(PL, "iolink") && !b(L, "iolink") && b(L, "eip")) event("bad", "MPB10 disconnected from port S1");
            if (!b(PL, "iolink") && b(L, "iolink") && b(PL, "eip")) event("ok", "MPB10 detected on S1: parameters restored from Data Storage");
            if (b(PL, "atv") && !b(L, "atv") && b(L, "fresh")) event("bad", "ATV930 lost: EtherNet/IP connection down");
            if (!b(PL, "atv") && b(L, "atv") && b(PL, "fresh")) event("ok", "ATV930 connected");
            if (b(PL, "drop") && !b(L, "drop") && b(L, "fresh")) event("bad", "RIO drop (BMECRA31210) lost: both fiber paths down");
            if (!b(PL, "drop") && b(L, "drop") && b(PL, "fresh")) event("ok", "RIO drop (BMECRA31210) back");
            if (b(P, "ring_known") && b(L, "drop") && b(PL, "drop")) {
                if (b(PP, "ring") && !b(P, "ring")) event("warn", "RIO ring opened (a fiber pulled?): drop still running on the other path");
                if (!b(PP, "ring") && b(P, "ring")) event("ok", "RIO ring closed again: redundancy restored");
            }
            if (b(P, "enable") != b(PP, "enable") && b(L, "fresh") && b(PL, "fresh")) {
                event(b(P, "enable") ? "info" : "warn", "Demo enable (xDemoEnable) " + (b(P, "enable") ? "ON" : "OFF"));
            }
            Object ref = D.get("ref"), pref = PD.get("ref");
            if (ref != null && pref != null && !ref.equals(pref)) {
                int r = ((Number) ref).intValue();
                String label = r == 0 ? "STOP" : (r < 0 ? "REV " : "FWD ") + Math.abs(r);
                double t = ((Number) s.get("t")).doubleValue();
                if (!marks.isEmpty() && t - ((Number) marks.peekLast().get(0)).doubleValue() < 1.5) marks.removeLast();
                marks.addLast(new ArrayList<Object>(Arrays.asList((Object) (Math.round(t * 10) / 10.0), label)));
                while (marks.size() > 60) marks.removeFirst();
            }
            if (b(D, "running") && !b(PD, "running")) event("ok", "Motor running");
            if (b(PD, "running") && !b(D, "running") && b(L, "atv")) event("info", "Motor stopped");
            if (b(D, "fault") && !b(PD, "fault")) event("bad", "Drive fault (see the keypad)");
            if (b(PD, "fault") && !b(D, "fault") && b(L, "atv")) event("ok", "Drive fault cleared");
            String h = String.valueOf(s.get("health")), ph = String.valueOf(p.get("health"));
            boolean hot = h.equals("warning") || h.equals("alarm");
            if (!h.equals(ph) && hot && b(L, "iolink")) {
                event(h.equals("warning") ? "warn" : "bad", String.format("Vibration %s: %.2f mm/s (zone %s)", h.toUpperCase(), ((Number) M.get("v")).doubleValue(), s.get("zone")));
            }
            if ((ph.equals("warning") || ph.equals("alarm")) && h.equals("good")) event("ok", "Vibration back to normal");
            if (b(M, "shock") && !b(PM, "shock")) event("warn", "Shock detected (count " + M.get("shocks") + ")");
            if (b(M, "temp_warn") && !b(PM, "temp_warn")) event("warn", String.format("Temperature high: %.1f °C", ((Number) M.get("temp")).doubleValue()));
        }
        prev = s;
    }

    // ---- alarms and limits, as in alarms.py
    private void alarmsTick(double t) {
        try {
            if (plc != null && t - alarmRead >= 1) {
                alarmRead = t;
                limitsSync();
                int[] head = plc.read(ALARM_BASE, LOG_START);
                if (alarmBlock == null || alarmBlock[0] != head[0] || alarmBlock[5] != head[5]) {
                    int[] b = new int[ALARM_SIZE];
                    for (int ofs = LOG_START; ofs < ALARM_SIZE; ofs += 120) {
                        int[] part = plc.read(ALARM_BASE + ofs, Math.min(120, ALARM_SIZE - ofs));
                        System.arraycopy(part, 0, b, ofs, part.length);
                    }
                    alarmBlock = b;
                }
                System.arraycopy(head, 0, alarmBlock, 0, LOG_START);
            }
        } catch (IOException | RuntimeException e) {
            lastError = e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
        }
        state.put("alarms", alarmBlock == null ? null : alarmsDecode(alarmBlock));
    }

    private void limitsSync() throws IOException {
        int[] ours = {(int) Math.round(d("zone_bc") * 100), (int) Math.round(d("zone_cd") * 100), (int) Math.round(d("temp_warn") * 10)};
        int[] p = plc.read(LIMITS, 3);
        if (limitsDirty || p[0] == 0 || p[1] == 0 || p[2] == 0) {
            plc.write(LIMITS, ours);
            limitsDirty = false;
        } else if (!Arrays.equals(p, ours)) {
            cfg.put("zone_bc", p[0] / 100.0);
            cfg.put("zone_cd", p[1] / 100.0);
            cfg.put("temp_warn", p[2] / 10.0);
        }
    }

    private static int unbcd(int v) {
        String h = String.format("%04X", v & 0xFFFF);
        return h.matches("\\d{4}") ? Integer.parseInt(h) : 0;
    }

    private static String stamp(int[] b, int ofs) {
        if (b[ofs] == 0 && b[ofs + 1] == 0 && b[ofs + 2] == 0 && b[ofs + 3] == 0) return null;
        int mmdd = unbcd(b[ofs + 1]), hhmm = unbcd(b[ofs + 2]), sec = unbcd(b[ofs + 3]);
        if (sec > 59) sec /= 100;
        return String.format("%04d-%02d-%02d %02d:%02d:%02d", unbcd(b[ofs]), mmdd / 100, mmdd % 100, hhmm / 100, hhmm % 100, sec);
    }

    private static Map<String, Object> alarmsDecode(int[] b) {
        List<Object> items = new ArrayList<Object>(), log = new ArrayList<Object>();
        for (int n = 1; n <= 12; n++) {
            boolean active = (b[1] >> (n - 1) & 1) != 0, unack = (b[2] >> (n - 1) & 1) != 0;
            Map<String, Object> m = new LinkedHashMap<String, Object>();
            m.put("id", n);
            m.put("text", ALARM_NAMES[n]);
            m.put("prio", ALARM_PRIO[n]);
            m.put("state", active && unack ? 1 : active ? 2 : unack ? 3 : 0);
            m.put("raised", stamp(b, 10 + 4 * (n - 1)));
            items.add(m);
        }
        for (int k = 1; k <= SLOTS; k++) {
            int ofs = LOG_START + 6 * (((b[5] - k) % SLOTS + SLOTS) % SLOTS), code = b[ofs + 1];
            if (code == 0) continue;
            int n = code % 100, type = code / 100;
            boolean known = n >= 1 && n <= 12;
            Map<String, Object> m = new LinkedHashMap<String, Object>();
            m.put("seq", b[ofs]);
            m.put("type", type >= 1 && type <= 3 ? ALARM_EVENTS[type] : "?");
            m.put("id", n);
            m.put("text", known ? ALARM_NAMES[n] : "Alarm " + n);
            m.put("prio", known ? ALARM_PRIO[n] : 2);
            m.put("at", stamp(b, ofs + 2));
            log.add(m);
        }
        Map<String, Object> a = new LinkedHashMap<String, Object>();
        a.put("items", items);
        a.put("log", log);
        a.put("active", b[3]);
        a.put("unack", b[4]);
        a.put("seq", b[0]);
        a.put("clock", stamp(b, 6));
        a.put("clock_set", unbcd(b[6]) >= 2020);
        return a;
    }

    String alarmCommand(String action, Object id) {
        int mask = 0, clear = 0;
        if (action.equals("ack")) {
            mask = id == null ? 0x0FFF : 1 << (((Number) id).intValue() - 1);
        } else if (action.equals("clear")) {
            clear = 1;
        } else {
            return "unknown alarm command";
        }
        synchronized (lock) {
            if (plc == null) return "not connected to the PLC";
            try {
                plc.write(ALARM_CMD, mask, clear);
            } catch (IOException e) {
                return "couldn't reach the PLC: " + e.getMessage();
            }
            alarmRead = 0;
        }
        return null;
    }

    // ---- for the web server
    void viewerOpened() {
        synchronized (lock) {
            viewers++;
        }
    }

    void viewerClosed() {
        synchronized (lock) {
            viewers--;
            lastViewer = now();
        }
    }

    int seq() {
        synchronized (lock) {
            return seq;
        }
    }

    String snapshotJson() {
        synchronized (lock) {
            Map<String, Object> s = new LinkedHashMap<String, Object>(state);
            s.put("events", events.subList(0, Math.min(14, events.size())));
            s.put("reach", reach);
            s.put("marks", new ArrayList<Object>(marks));
            return Json.write(s);
        }
    }

    String historyJson() {
        synchronized (lock) {
            return Json.write(new ArrayList<Object>(history));
        }
    }

    void clear() {
        synchronized (lock) {
            history.clear();
            events.clear();
        }
    }
}
