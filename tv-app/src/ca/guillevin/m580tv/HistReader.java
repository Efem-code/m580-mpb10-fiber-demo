package ca.guillevin.m580tv;

import java.io.IOException;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.TreeMap;

/**
 * The PLC's historian (S07_History) for the TV: the same reading as dashboard/history.py, kept in memory only.
 * The PLC holds 3 days, so after a restart the TV simply copies them again.
 *
 *   %MW1900 header: [0] next slot [1] samples kept [2] seconds per sample [3] samples so far (0-29999)
 *                   [4] year of the newest (BCD) [5] slots (4320) [6] window's first slot [7] request answered
 *   %MW1910 request: [0] first slot, [1] a new request number
 *   %MW1912 window: 14 samples of 8 words
 */
class HistReader {
    interface Host {
        /** {host, port, unit} of the PLC now */
        Object[] plc();
    }

    private static final int BASE = 1900, REQ = 1910, WORDS = 124, SLOTS = 4320, PER_WIN = 14, SEQ_WRAP = 30000, NA = -32768;
    private static final long KEEP_S = 4 * 86400;

    private final Host host;
    private final Object lock = new Object();
    private final TreeMap<Long, List<Object>> rows = new TreeMap<Long, List<Object>>();
    private final Map<String, Object> status = new LinkedHashMap<String, Object>();
    private Integer lastSeq;
    private int req = new Random().nextInt(20000) + 5000;      // away from the laptop's numbers
    private volatile boolean copyAll;

    HistReader(Host host) {
        this.host = host;
        status.put("ok", false);
        status.put("error", "");
        status.put("samples", 0);
        status.put("period", 60);
        status.put("slots", SLOTS);
        status.put("last_copy", null);
        status.put("clock_set", true);
        status.put("folder", null);
        status.put("files", 0);
        status.put("copying", false);
        Thread t = new Thread(new Runnable() {
            @Override public void run() {
                loop();
            }
        }, "history");
        t.setDaemon(true);
        t.start();
    }

    void copyAll() {
        copyAll = true;
    }

    private void loop() {
        PlcClient c = null;
        String where = null;
        double next = 0;
        while (true) {
            try {
                Thread.sleep(500);
            } catch (InterruptedException e) {
                return;
            }
            double now = System.currentTimeMillis() / 1000.0;
            boolean full = copyAll;
            if (!full && now < next) continue;
            next = now + 15;
            copyAll = false;
            Object[] p = host.plc();
            String key = p[0] + ":" + p[1] + ":" + p[2];
            try {
                if (!key.equals(where)) {
                    if (c != null) c.close();
                    c = new PlcClient(String.valueOf(p[0]), (Integer) p[1], (Integer) p[2]);
                    where = key;
                    lastSeq = null;
                }
                poll(c, full);
            } catch (IOException | RuntimeException e) {
                synchronized (lock) {
                    status.put("ok", false);
                    status.put("error", e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage());
                    status.put("copying", false);
                }
                if (c != null) c.close();
                c = null;
                where = null;
            }
        }
    }

    private static int s16(int v) {
        return v >= 0x8000 ? v - 0x10000 : v;
    }

    private static int unbcd(int v) {
        String h = String.format("%04X", v & 0xFFFF);
        return h.matches("\\d{4}") ? Integer.parseInt(h) : -1;
    }

    private int[] window(PlcClient c, int slot) throws IOException {
        for (int attempt = 0; attempt < 3; attempt++) {         // the laptop may ask at the same moment: ask again
            req = req % 30000 + 1;
            c.write(REQ, slot, req);
            for (int i = 0; i < 15; i++) {
                int[] b = c.read(BASE, WORDS);
                if (b[7] == req && b[6] == slot) return b;
                try {
                    Thread.sleep(25);
                } catch (InterruptedException e) {
                    throw new IOException("stopped");
                }
            }
        }
        throw new IOException("the PLC didn't answer the history request: is S07_History loaded?");
    }

    private void poll(PlcClient c, boolean full) throws IOException {
        double now = System.currentTimeMillis() / 1000.0;
        int[] hdr = c.read(BASE, 10);
        if (hdr[5] != SLOTS || hdr[9] != 1) throw new IOException("no historian at %MW1900: load the S07_History section");
        int head = hdr[0], count = hdr[1], period = hdr[2], seq = hdr[3];
        int want = full || lastSeq == null ? count : Math.min(count, ((seq - lastSeq) % SEQ_WRAP + SEQ_WRAP) % SEQ_WRAP);
        List<List<Object>> got = new ArrayList<List<Object>>();
        boolean clockSet = true;
        if (want > 0) {
            synchronized (lock) {
                status.put("copying", true);
            }
            int start = ((head - want) % SLOTS + SLOTS) % SLOTS;
            List<int[]> words = new ArrayList<int[]>();
            for (int k = 0; k < want; k += PER_WIN) {
                int[] b = window(c, (start + k) % SLOTS);
                for (int i = 0; i < PER_WIN && words.size() < want; i++) {
                    int[] w = new int[8];
                    System.arraycopy(b, 12 + 8 * i, w, 0, 8);
                    words.add(w);
                }
            }
            int year = unbcd(c.read(BASE, 10)[4]);
            int newestMmdd = unbcd(words.get(words.size() - 1)[0]);
            for (int i = 0; i < words.size(); i++) {
                int[] w = words.get(i);
                Long t = time(w, year, newestMmdd);
                if (t == null) {
                    clockSet = false;
                    t = (long) (now - (long) (words.size() - 1 - i) * period);
                }
                List<Object> r = new ArrayList<Object>();
                r.add(t);
                boolean ok = s16(w[2]) != NA;
                for (int j = 2; j <= 4; j++) r.add(ok ? s16(w[j]) / 100.0 : null);
                r.add(ok ? s16(w[5]) / 10.0 : null);
                r.add(s16(w[6]));
                r.add(w[7] & 0xFF);
                got.add(r);
            }
        }
        synchronized (lock) {
            for (List<Object> r : got) {
                Long t = (Long) r.get(0);
                if (!rows.containsKey(t)) rows.put(t, r);
            }
            long cut = (long) now - KEEP_S;
            while (!rows.isEmpty() && rows.firstKey() < cut) rows.remove(rows.firstKey());
            lastSeq = seq;
            if (want > 0 || status.get("last_copy") == null) status.put("last_copy", now);
            status.put("ok", true);
            status.put("error", "");
            status.put("samples", count);
            status.put("period", period);
            status.put("copying", false);
            if (want > 0) status.put("clock_set", clockSet);
        }
    }

    /** When a sample was taken, from its BCD date and time; null if the PLC clock wasn't set. */
    private static Long time(int[] w, int year, int newestMmdd) {
        int mmdd = unbcd(w[0]), hhmm = unbcd(w[1]), sec = unbcd(w[7] >> 8 & 0xFF);
        if (year < 2020 || mmdd < 101 || mmdd > 1231 || hhmm < 0 || sec < 0) return null;
        if (mmdd > newestMmdd) year--;                         // newest in January, this one in December
        Calendar cal = Calendar.getInstance();
        cal.clear();
        cal.set(year, mmdd / 100 - 1, mmdd % 100, hhmm / 100, hhmm % 100, Math.min(sec, 59));
        return cal.getTimeInMillis() / 1000;
    }

    String json(double hours) {
        long cut = (long) (System.currentTimeMillis() / 1000.0 - hours * 3600);
        synchronized (lock) {
            Map<String, Object> m = new LinkedHashMap<String, Object>();
            m.put("status", new LinkedHashMap<String, Object>(status));
            m.put("rows", new ArrayList<Object>(rows.tailMap(cut).values()));
            m.put("mode", "live");
            return Json.write(m);
        }
    }
}
