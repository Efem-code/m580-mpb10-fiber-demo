package ca.guillevin.m580tv;

import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.Socket;

/** Modbus TCP reader for the M580. Function 3 only: this app never writes to the PLC. */
class PlcClient {
    private static final int STATUS_BASE = 1000, STATUS_WORDS = 32;
    private static final int ALARM_BASE = 1300, ALARM_HEAD = 60, ALARM_WORDS = 444;

    private final String host;
    private final int port, unit;
    private Socket socket;
    private DataInputStream in;
    private DataOutputStream out;
    private int tid;
    private int[] alarms;
    private long alarmsRead;

    PlcClient(String host, int port, int unit) {
        this.host = host;
        this.port = port;
        this.unit = unit;
    }

    /** One poll, as JSON for the page. Throws if the PLC can't be read. */
    String poll() throws IOException {
        int[] status = read(STATUS_BASE, STATUS_WORDS);
        long now = System.currentTimeMillis();
        if (alarms == null || now - alarmsRead >= 1000) {
            alarmsRead = now;
            int[] head = read(ALARM_BASE, ALARM_HEAD);
            if (alarms == null || alarms[0] != head[0] || alarms[5] != head[5]) {
                alarms = new int[ALARM_WORDS];
                for (int ofs = ALARM_HEAD; ofs < ALARM_WORDS; ofs += 120) {
                    int[] part = read(ALARM_BASE + ofs, Math.min(120, ALARM_WORDS - ofs));
                    System.arraycopy(part, 0, alarms, ofs, part.length);
                }
            }
            System.arraycopy(head, 0, alarms, 0, ALARM_HEAD);
        }
        return "{\"ok\":true,\"status\":" + json(status) + ",\"alarms\":" + json(alarms) + "}";
    }

    void close() {
        try {
            if (socket != null) socket.close();
        } catch (IOException ignored) {
        }
        socket = null;
        alarms = null;
    }

    private static String json(int[] v) {
        StringBuilder b = new StringBuilder("[");
        for (int i = 0; i < v.length; i++) {
            if (i > 0) b.append(',');
            b.append(v[i]);
        }
        return b.append(']').toString();
    }

    private void connect() throws IOException {
        if (socket != null) return;
        Socket s = new Socket();
        s.setSoTimeout(2000);
        s.setTcpNoDelay(true);
        s.connect(new InetSocketAddress(host, port), 3000);
        socket = s;
        in = new DataInputStream(s.getInputStream());
        out = new DataOutputStream(s.getOutputStream());
    }

    private int[] read(int addr, int count) throws IOException {
        try {
            connect();
            tid = (tid + 1) & 0xFFFF;
            out.writeShort(tid);
            out.writeShort(0);
            out.writeShort(6);
            out.writeByte(unit);
            out.writeByte(3);
            out.writeShort(addr);
            out.writeShort(count);
            out.flush();
            int gotTid = in.readUnsignedShort();
            in.readUnsignedShort();
            int length = in.readUnsignedShort();
            in.readUnsignedByte();
            int fc = in.readUnsignedByte();
            if (gotTid != tid) throw new IOException("Modbus reply out of step");
            if (fc != 3) throw new IOException("PLC refused the read (exception " + in.readUnsignedByte() + ")");
            int bytes = in.readUnsignedByte();
            if (bytes != count * 2 || length != bytes + 3) throw new IOException("Modbus reply is the wrong size");
            int[] v = new int[count];
            for (int i = 0; i < count; i++) v[i] = in.readUnsignedShort();
            return v;
        } catch (IOException e) {
            close();
            throw e;
        }
    }

    /** Desktop test: java PlcClient host port unit */
    public static void main(String[] a) throws Exception {
        PlcClient c = new PlcClient(a[0], Integer.parseInt(a[1]), Integer.parseInt(a[2]));
        System.out.println(c.poll().substring(0, 120));
        Thread.sleep(1100);
        String s = c.poll();
        System.out.println(s.length() + " chars, ok=" + s.startsWith("{\"ok\":true"));
        c.close();
    }
}
