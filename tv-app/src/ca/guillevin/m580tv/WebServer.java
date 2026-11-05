package ca.guillevin.m580tv;

import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;

/** Serves the dashboard page and its /api to the app's own WebView. Listens on this device only. */
class WebServer {
    private static final int PAGE_WIDTH = 1600;

    interface Files {
        InputStream open(String name) throws IOException;
    }

    private final Engine eng;
    private final Files files;
    private final ServerSocket server;

    WebServer(Engine eng, Files files, int port) throws IOException {
        this.eng = eng;
        this.files = files;
        server = new ServerSocket();
        server.setReuseAddress(true);
        server.bind(new InetSocketAddress(InetAddress.getByName("127.0.0.1"), port));
        Thread t = new Thread(new Runnable() {
            @Override public void run() {
                accept();
            }
        }, "web");
        t.setDaemon(true);
        t.start();
    }

    void close() {
        try {
            server.close();
        } catch (IOException ignored) {
        }
    }

    private void accept() {
        while (!server.isClosed()) {
            try {
                final Socket s = server.accept();
                Thread t = new Thread(new Runnable() {
                    @Override public void run() {
                        try {
                            handle(s);
                        } catch (IOException | RuntimeException ignored) {
                        } finally {
                            try {
                                s.close();
                            } catch (IOException ignored) {
                            }
                        }
                    }
                }, "web-client");
                t.setDaemon(true);
                t.start();
            } catch (IOException ignored) {
            }
        }
    }

    private static String line(InputStream in) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        int c;
        while ((c = in.read()) >= 0 && c != '\n') {
            if (c != '\r') b.write(c);
        }
        if (c < 0 && b.size() == 0) throw new IOException("closed");
        return new String(b.toByteArray(), StandardCharsets.UTF_8);
    }

    private static void head(OutputStream o, int code, String type, long length) throws IOException {
        String h = "HTTP/1.1 " + code + (code == 200 ? " OK" : " Error") + "\r\nContent-Type: " + type
                + "\r\nCache-Control: no-store\r\nConnection: close\r\n" + (length >= 0 ? "Content-Length: " + length + "\r\n" : "") + "\r\n";
        o.write(h.getBytes(StandardCharsets.UTF_8));
    }

    private static void json(OutputStream o, int code, Object v) throws IOException {
        byte[] b = Json.write(v).getBytes(StandardCharsets.UTF_8);
        head(o, code, "application/json", b.length);
        o.write(b);
        o.flush();
    }

    private static Map<String, Object> msg(String key, Object v) {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        m.put(key, v);
        return m;
    }

    private static void result(OutputStream o, String err) throws IOException {
        json(o, err == null ? 200 : 409, err == null ? msg("ok", true) : msg("error", err));
    }

    @SuppressWarnings("unchecked")
    private void handle(Socket s) throws IOException {
        InputStream in = s.getInputStream();
        OutputStream o = new BufferedOutputStream(s.getOutputStream());
        String[] req = line(in).split(" ");
        int length = 0;
        for (String h = line(in); !h.isEmpty(); h = line(in)) {
            if (h.toLowerCase().startsWith("content-length:")) length = Integer.parseInt(h.substring(15).trim());
        }
        byte[] body = new byte[length];
        for (int got = 0; got < length; ) {
            int n = in.read(body, got, length - got);
            if (n < 0) break;
            got += n;
        }
        String path = req[1].split("\\?")[0];
        if (req[0].equals("POST")) {
            Map<String, Object> b;
            try {
                Object v = Json.parse(new String(body, StandardCharsets.UTF_8));
                b = v instanceof Map ? (Map<String, Object>) v : new LinkedHashMap<String, Object>();
            } catch (RuntimeException e) {
                b = new LinkedHashMap<String, Object>();
            }
            String action = String.valueOf(b.get("action"));
            if (path.equals("/api/drive")) {
                String err;
                try {
                    err = eng.drive(action, b.get("value"));
                } catch (RuntimeException e) {
                    err = "bad request: " + e;
                }
                result(o, err);
            } else if (path.equals("/api/alarm")) {
                result(o, eng.alarmCommand(action, b.get("id")));
            } else if (path.equals("/api/config")) {
                try {
                    json(o, 200, eng.configure(b));
                } catch (RuntimeException e) {
                    Map<String, Object> m = msg("errors", java.util.Collections.singletonList(String.valueOf(e.getMessage())));
                    m.put("warnings", new java.util.ArrayList<Object>());
                    json(o, 400, m);
                }
            } else if (path.equals("/api/clear")) {
                eng.clear();
                result(o, null);
            } else {
                json(o, 404, msg("error", "not found"));
            }
            return;
        }
        if (path.equals("/api/state") || path.equals("/api/history")) {
            byte[] b = (path.equals("/api/state") ? eng.snapshotJson() : eng.historyJson()).getBytes(StandardCharsets.UTF_8);
            head(o, 200, "application/json", b.length);
            o.write(b);
            o.flush();
        } else if (path.equals("/api/config")) {
            json(o, 200, eng.config());
        } else if (path.equals("/api/stream")) {
            stream(o);
        } else {
            file(o, path.equals("/") ? "index.html" : path.substring(1));
        }
    }

    private void stream(OutputStream o) throws IOException {
        head(o, 200, "text/event-stream", -1);
        o.flush();
        int seq = -1;
        eng.viewerOpened();
        try {
            while (true) {
                int now = eng.seq();
                if (now != seq) {
                    seq = now;
                    o.write(("data: " + eng.snapshotJson() + "\n\n").getBytes(StandardCharsets.UTF_8));
                    o.flush();
                }
                Thread.sleep(200);
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } finally {
            eng.viewerClosed();
        }
    }

    private void file(OutputStream o, String name) throws IOException {
        if (name.contains("..")) {
            json(o, 404, msg("error", "not found"));
            return;
        }
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        try (InputStream f = files.open(name)) {
            byte[] buf = new byte[16384];
            for (int n; (n = f.read(buf)) > 0; ) b.write(buf, 0, n);
        } catch (IOException e) {
            json(o, 404, msg("error", "not found"));
            return;
        }
        String type = name.endsWith(".html") ? "text/html; charset=utf-8" : name.endsWith(".js") ? "text/javascript; charset=utf-8"
                : name.endsWith(".css") ? "text/css; charset=utf-8" : name.endsWith(".png") ? "image/png" : "application/octet-stream";
        byte[] out = b.toByteArray();
        if (name.equals("index.html")) {
            // the TV's panel: lay the page out this wide, then fit it to the screen, whatever Android's density
            out = new String(out, StandardCharsets.UTF_8).replace("width=device-width, initial-scale=1", "width=" + PAGE_WIDTH)
                    .getBytes(StandardCharsets.UTF_8);
        }
        head(o, 200, type, out.length);
        o.write(out);
        o.flush();
    }

    /** Desktop test: java ...WebServer staticDir settingsFile port */
    public static void main(final String[] a) throws Exception {
        final File settings = new File(a[1]);
        Engine e = new Engine(new Engine.Store() {
            @Override public String load() {
                try (InputStream f = new FileInputStream(settings)) {
                    ByteArrayOutputStream b = new ByteArrayOutputStream();
                    byte[] buf = new byte[4096];
                    for (int n; (n = f.read(buf)) > 0; ) b.write(buf, 0, n);
                    return new String(b.toByteArray(), StandardCharsets.UTF_8);
                } catch (IOException x) {
                    return null;
                }
            }

            @Override public void save(String json) {
                try (OutputStream f = new FileOutputStream(settings)) {
                    f.write(json.getBytes(StandardCharsets.UTF_8));
                } catch (IOException ignored) {
                }
            }
        });
        new WebServer(e, new Files() {
            @Override public InputStream open(String name) throws IOException {
                return new FileInputStream(new File(a[0], name));
            }
        }, Integer.parseInt(a[2]));
        Thread.sleep(Long.MAX_VALUE);
    }
}
