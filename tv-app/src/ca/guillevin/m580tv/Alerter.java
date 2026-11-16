package ca.guillevin.m580tv;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Calendar;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Random;

import javax.net.ssl.SSLParameters;
import javax.net.ssl.SSLSocket;
import javax.net.ssl.SSLSocketFactory;

/**
 * Email alerts for the TV: dashboard/alerts.py in Java (keep the two in step). Critical alarms go at once,
 * warnings at most every 15 minutes, a daily summary with the last 24 h of history as CSV. The laptop and the
 * TV claim each message in %MW1297-1299 first, so it goes once: [0] newest alarm event sent, [1] who claimed
 * (1 laptop, 2 TV), [2] MMDD of the last summary.
 */
class Alerter {
    static final int CLAIM = 1297, ME = 2;
    private static final double BATCH_S = 15 * 60;

    private Integer lastSeq;
    private final List<Map<String, Object>> queue = new ArrayList<Map<String, Object>>();
    private double lastBatch;
    private Integer summaryDay;
    private final Random rnd = new Random();
    final Map<String, Object> status = new LinkedHashMap<String, Object>();

    Alerter() {
        status.put("sent", 0);
        status.put("last_sent", null);
        status.put("last_subject", "");
        status.put("error", "");
        status.put("error_at", null);
    }

    static boolean newer(int a, int b) {
        int d = ((a - b) % 30000 + 30000) % 30000;
        return d > 0 && d < 15000;
    }

    private static String s(Map<String, Object> m, String k) {
        Object v = m.get(k);
        return v == null ? "" : String.valueOf(v);
    }

    private static boolean on(Map<String, Object> cfg, String k) {
        return Boolean.TRUE.equals(cfg.get(k));
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> m(Object o) {
        return o instanceof Map ? (Map<String, Object>) o : new LinkedHashMap<String, Object>();
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> l(Object o) {
        return o instanceof List ? (List<Map<String, Object>>) o : new ArrayList<Map<String, Object>>();
    }

    private static int seq(Map<String, Object> e) {
        return ((Number) e.get("seq")).intValue();
    }

    // ---- claiming in the PLC
    private boolean claim(PlcClient c, int word, int value) throws IOException {
        try {
            Thread.sleep(rnd.nextInt(1500));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        int[] cur = c.read(CLAIM, 3);
        if (word == 0 && !newer(value, cur[0])) return false;
        if (word == 2 && cur[2] == value) return false;
        c.write(CLAIM + word, value);
        c.write(CLAIM + 1, ME);
        try {
            Thread.sleep(400);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        return c.read(CLAIM + 1, 1)[0] == ME;
    }

    private void sent(String subject) {
        synchronized (status) {
            status.put("sent", ((Integer) status.get("sent")) + 1);
            status.put("last_sent", System.currentTimeMillis() / 1000.0);
            status.put("last_subject", subject);
            status.put("error", "");
        }
    }

    void failed(Exception e) {
        synchronized (status) {
            status.put("error", e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage());
            status.put("error_at", System.currentTimeMillis() / 1000.0);
        }
    }

    Map<String, Object> status(boolean on) {
        synchronized (status) {
            Map<String, Object> m = new LinkedHashMap<String, Object>(status);
            m.put("on", on);
            return m;
        }
    }

    interface Rows {
        List<List<Object>> rows24h();
        int period();
    }

    /** Every couple of seconds. c is the claim connection to the PLC. */
    void tick(Map<String, Object> cfg, Map<String, Object> state, PlcClient c, Rows rows) throws IOException {
        Map<String, Object> a = m(state.get("alarms"));
        if (!on(cfg, "alerts_on") || a.isEmpty()) {
            if (!a.isEmpty()) lastSeq = ((Number) a.get("seq")).intValue();
            return;
        }
        int now = ((Number) a.get("seq")).intValue();
        if (lastSeq == null || (now != lastSeq && !newer(now, lastSeq))) lastSeq = now;   // start, or the history was cleared
        List<Map<String, Object>> fresh = new ArrayList<Map<String, Object>>();
        for (Map<String, Object> e : l(a.get("log"))) {
            if (newer(seq(e), lastSeq) && !newer(seq(e), now)) fresh.add(0, e);           // the log is newest first
        }
        final int base = lastSeq;
        java.util.Collections.sort(fresh, new java.util.Comparator<Map<String, Object>>() {
            @Override public int compare(Map<String, Object> x, Map<String, Object> y) {
                return ((seq(x) - base) % 30000 + 30000) % 30000 - ((seq(y) - base) % 30000 + 30000) % 30000;
            }
        });
        lastSeq = now;
        List<Map<String, Object>> urgent = new ArrayList<Map<String, Object>>();
        for (Map<String, Object> e : fresh) {
            boolean raised = "raised".equals(e.get("type"));
            if (raised && ((Number) e.get("prio")).intValue() == 1) urgent.add(e);
            else if ((raised && on(cfg, "alert_warnings")) || ("cleared".equals(e.get("type")) && on(cfg, "alert_cleared"))) queue.add(e);
        }
        String asset = s(cfg, "asset");
        double t = System.currentTimeMillis() / 1000.0;
        if (!urgent.isEmpty()) {
            int top = 0;
            for (Map<String, Object> e : fresh) top = seq(e);
            if (claim(c, 0, top)) {
                String subject = "ALARM on " + asset + ": " + names(urgent);
                List<Map<String, Object>> all = new ArrayList<Map<String, Object>>(urgent);
                all.addAll(queue);
                send(cfg, subject, body(a, state, all), null, null);
                sent(subject);
                lastBatch = t;
            }
            queue.clear();
        }
        if (!queue.isEmpty() && t - lastBatch >= BATCH_S) {
            int top = seq(queue.get(queue.size() - 1));
            if (claim(c, 0, top)) {
                List<Map<String, Object>> raised = new ArrayList<Map<String, Object>>();
                for (Map<String, Object> e : queue) if ("raised".equals(e.get("type"))) raised.add(e);
                String subject = "Warning on " + asset + ": " + (raised.isEmpty() ? "alarms cleared" : names(raised));
                send(cfg, subject, body(a, state, queue), null, null);
                sent(subject);
            }
            queue.clear();
            lastBatch = t;
        }
        int hour = ((Number) cfg.get("summary_hour")).intValue();
        Calendar cal = Calendar.getInstance();
        int day = Integer.parseInt(String.format(Locale.US, "%02d%02d", cal.get(Calendar.MONTH) + 1, cal.get(Calendar.DAY_OF_MONTH)), 16);
        if (hour >= 0 && hour <= 23 && cal.get(Calendar.HOUR_OF_DAY) >= hour && (summaryDay == null || summaryDay != day)) {
            summaryDay = day;
            if (claim(c, 2, day)) {
                List<List<Object>> r = rows.rows24h();
                String subject = "Daily summary for " + asset + ", " + new SimpleDateFormat("EEE d MMM", Locale.US).format(new Date());
                String text = "The last 24 hours on " + asset + ", from the PLC's history:\n\n" + summary(r, rows.period())
                        + "\n\nNow:\n" + readings(state) + "\n\nActive alarms: " + active(a) + "\n\nThe samples are attached as CSV.";
                send(cfg, subject, text, "machine-history-" + new SimpleDateFormat("yyyy-MM-dd", Locale.US).format(new Date()) + ".csv", csv(r));
                sent(subject);
            }
        }
    }

    String test(Map<String, Object> cfg, Map<String, Object> state) {
        String subject = "Test from the " + s(cfg, "asset") + " TV";
        try {
            send(cfg, subject, "The email alerts are set up. Alarms and the daily summary will come to this address.\n\nNow:\n"
                    + readings(state), null, null);
        } catch (IOException | RuntimeException e) {
            failed(e);
            return e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
        }
        sent(subject);
        return null;
    }

    // ---- the text
    private static String names(List<Map<String, Object>> evs) {
        LinkedHashSet<String> n = new LinkedHashSet<String>();
        for (Map<String, Object> e : evs) n.add(s(e, "text"));
        return android_join(n);
    }

    private static String android_join(Iterable<String> parts) {
        StringBuilder b = new StringBuilder();
        for (String p : parts) b.append(b.length() == 0 ? "" : ", ").append(p);
        return b.toString();
    }

    private static String active(Map<String, Object> a) {
        List<String> n = new ArrayList<String>();
        for (Map<String, Object> i : l(a.get("items"))) {
            int st = ((Number) i.get("state")).intValue();
            if (st == 1 || st == 2) n.add(s(i, "text"));
        }
        return n.isEmpty() ? "none" : android_join(n);
    }

    private static String f(Object v, String unit, int dp) {
        return v == null ? "–" : String.format(Locale.US, "%." + dp + "f%s", ((Number) v).doubleValue(), unit);
    }

    private static String readings(Map<String, Object> state) {
        Map<String, Object> mp = m(state.get("mpb")), d = m(state.get("drive"));
        return "Vibration " + f(mp.get("v"), " mm/s", 2) + " (X " + f(mp.get("vx"), "", 2) + ", Y " + f(mp.get("vy"), "", 2) + ", Z "
                + f(mp.get("vz"), "", 2) + ")\nTemperature " + f(mp.get("temp"), " °C", 1) + "\nDrive: " + s(d, "state") + ", "
                + (d.get("speed") == null ? "–" : d.get("speed")) + " rpm";
    }

    private static String body(Map<String, Object> a, Map<String, Object> state, List<Map<String, Object>> evs) {
        StringBuilder b = new StringBuilder("What happened (PLC clock):\n");
        for (Map<String, Object> e : evs) {
            b.append(e.get("at") == null ? "–" : e.get("at")).append("  ").append(s(e, "text")).append(' ').append(s(e, "type"))
                    .append(((Number) e.get("prio")).intValue() == 1 ? "  (critical)" : "").append('\n');
        }
        return b + "\nNow:\n" + readings(state) + "\n\nActive alarms: " + active(a) + "\n\nAcknowledge on the dashboard's Alarms tab.";
    }

    private static String summary(List<List<Object>> rows, int period) {
        double per = period / 60.0, vmax = -1;
        long at = 0;
        int warn = 0, alarm = 0, shock = 0, run = 0;
        for (List<Object> r : rows) {
            for (int i = 1; i <= 3; i++) {
                if (r.get(i) != null && ((Number) r.get(i)).doubleValue() > vmax) {
                    vmax = ((Number) r.get(i)).doubleValue();
                    at = ((Number) r.get(0)).longValue();
                }
            }
            int fl = ((Number) r.get(6)).intValue();
            if ((fl & 2) != 0) alarm++;
            else if ((fl & 1) != 0) warn++;
            if ((fl & 8) != 0) shock++;
            if ((fl & 32) != 0) run++;
        }
        return "Highest vibration: " + (vmax < 0 ? "–" : String.format(Locale.US, "%.2f mm/s at ", vmax)
                + new SimpleDateFormat("EEE HH:mm", Locale.US).format(new Date(at * 1000))) + "\nTime in vibration warning: " + hm(warn * per)
                + "\nTime in vibration alarm: " + hm(alarm * per) + "\nMinutes with a shock: " + shock + "\nMotor running: " + hm(run * per)
                + "\nSamples: " + rows.size();
    }

    private static String hm(double mins) {
        int m = (int) mins;
        return String.format(Locale.US, "%d h %02d min", m / 60, m % 60);
    }

    private static String csv(List<List<Object>> rows) {
        StringBuilder b = new StringBuilder("time,vib_x_mm_s,vib_y_mm_s,vib_z_mm_s,temp_c,speed_rpm,vib_warning,vib_alarm,temp_warning,shock,data_lost,running,drive_fault\r\n");
        SimpleDateFormat df = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US);
        for (List<Object> r : rows) {
            b.append(df.format(new Date(((Number) r.get(0)).longValue() * 1000)));
            for (int i = 1; i <= 5; i++) b.append(',').append(r.get(i) == null ? "" : r.get(i));
            int fl = ((Number) r.get(6)).intValue();
            for (int i = 0; i < 7; i++) b.append(',').append(fl >> i & 1);
            b.append("\r\n");
        }
        return b.toString();
    }

    // ---- SMTP: just what sending one message needs
    private static String b64(String t) {
        return Base64.getEncoder().encodeToString(t.getBytes(StandardCharsets.UTF_8));
    }

    private static String mime64(String t) {
        return Base64.getMimeEncoder().encodeToString(t.getBytes(StandardCharsets.UTF_8));
    }

    private static class Conn {
        Socket sock;
        BufferedReader in;
        OutputStream out;

        void attach(Socket s) throws IOException {
            sock = s;
            sock.setSoTimeout(20000);
            in = new BufferedReader(new InputStreamReader(s.getInputStream(), StandardCharsets.UTF_8));
            out = s.getOutputStream();
        }

        String reply(int... ok) throws IOException {
            String line, all = "";
            do {
                line = in.readLine();
                if (line == null) throw new IOException("the mail server closed the connection");
                all += line + " ";
            } while (line.length() > 3 && line.charAt(3) == '-');
            int code = Integer.parseInt(line.substring(0, 3));
            for (int c : ok) if (c == code) return all;
            throw new IOException("the mail server said: " + all.trim());
        }

        String cmd(String c, int... ok) throws IOException {
            out.write((c + "\r\n").getBytes(StandardCharsets.UTF_8));
            out.flush();
            return reply(ok);
        }
    }

    private static SSLSocket tls(Socket under, String host, int port) throws IOException {
        SSLSocketFactory f = (SSLSocketFactory) SSLSocketFactory.getDefault();
        SSLSocket s = (SSLSocket) (under == null ? f.createSocket(host, port) : f.createSocket(under, host, port, true));
        SSLParameters p = s.getSSLParameters();
        p.setEndpointIdentificationAlgorithm("HTTPS");             // check the certificate is for this host
        s.setSSLParameters(p);
        s.startHandshake();
        return s;
    }

    static void send(Map<String, Object> cfg, String subject, String body, String attName, String attData) throws IOException {
        List<String> to = new ArrayList<String>();
        for (String r : s(cfg, "mail_to").replace(';', ',').split(",")) if (!r.trim().isEmpty()) to.add(r.trim());
        String host = s(cfg, "smtp_host").trim(), sec = s(cfg, "smtp_security"), user = s(cfg, "smtp_user");
        int port = ((Number) cfg.get("smtp_port")).intValue();
        if (host.isEmpty() || to.isEmpty()) throw new IOException("fill in the mail server and at least one recipient");
        String from = s(cfg, "mail_from").isEmpty() ? user : s(cfg, "mail_from");
        Conn c = new Conn();
        try {
            if (sec.equals("ssl")) {
                c.attach(tls(null, host, port));
            } else {
                Socket p = new Socket();
                p.connect(new InetSocketAddress(host, port), 15000);
                c.attach(p);
            }
            c.reply(220);
            c.cmd("EHLO m580-tv", 250);
            if (sec.equals("starttls")) {
                c.cmd("STARTTLS", 220);
                c.attach(tls(c.sock, host, port));
                c.cmd("EHLO m580-tv", 250);
            }
            if (!user.isEmpty()) {
                c.cmd("AUTH LOGIN", 334);
                c.cmd(b64(user), 334);
                c.cmd(b64(s(cfg, "smtp_password")), 235);
            }
            c.cmd("MAIL FROM:<" + from + ">", 250);
            for (String r : to) c.cmd("RCPT TO:<" + r + ">", 250, 251);
            c.cmd("DATA", 354);
            String b = "MIXED-" + Long.toHexString(System.nanoTime());
            StringBuilder m = new StringBuilder();
            m.append("From: ").append(from).append("\r\nTo: ").append(android_join(to)).append("\r\nSubject: =?UTF-8?B?").append(b64(subject))
                    .append("?=\r\nDate: ").append(new SimpleDateFormat("EEE, dd MMM yyyy HH:mm:ss Z", Locale.US).format(new Date()))
                    .append("\r\nMIME-Version: 1.0\r\n");
            if (attName == null) {
                m.append("Content-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: base64\r\n\r\n").append(mime64(body)).append("\r\n");
            } else {
                m.append("Content-Type: multipart/mixed; boundary=\"").append(b).append("\"\r\n\r\n--").append(b)
                        .append("\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: base64\r\n\r\n").append(mime64(body))
                        .append("\r\n--").append(b).append("\r\nContent-Type: text/csv; charset=utf-8\r\nContent-Disposition: attachment; filename=\"")
                        .append(attName).append("\"\r\nContent-Transfer-Encoding: base64\r\n\r\n").append(mime64(attData)).append("\r\n--")
                        .append(b).append("--\r\n");
            }
            m.append(".");                                     // base64 lines never start with a dot: no stuffing needed
            c.cmd(m.toString(), 250);
            try {
                c.cmd("QUIT", 221);
            } catch (IOException ignored) {
            }
        } finally {
            if (c.sock != null) {
                try {
                    c.sock.close();
                } catch (IOException ignored) {
                }
            }
        }
    }
}
