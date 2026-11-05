package ca.guillevin.m580tv;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.Socket;

/** Modbus TCP client for the M580: function 3 to read, function 16 to write. */
class PlcClient {
    private final String host;
    private final int port, unit;
    private Socket socket;
    private DataInputStream in;
    private DataOutputStream out;
    private int tid;

    PlcClient(String host, int port, int unit) {
        this.host = host;
        this.port = port;
        this.unit = unit;
    }

    synchronized void close() {
        try {
            if (socket != null) socket.close();
        } catch (IOException ignored) {
        }
        socket = null;
    }

    private void connect() throws IOException {
        if (socket != null) return;
        Socket s = new Socket();
        s.setSoTimeout(1000);
        s.setTcpNoDelay(true);
        s.connect(new InetSocketAddress(host, port), 1000);
        socket = s;
        in = new DataInputStream(s.getInputStream());
        out = new DataOutputStream(s.getOutputStream());
    }

    synchronized int[] read(int addr, int count) throws IOException {
        byte[] r = request(3, addr, count, null);
        if (r.length != 1 + count * 2 || (r[0] & 0xFF) != count * 2) throw new IOException("Modbus reply is the wrong size");
        int[] v = new int[count];
        for (int i = 0; i < count; i++) v[i] = (r[1 + i * 2] & 0xFF) << 8 | (r[2 + i * 2] & 0xFF);
        return v;
    }

    synchronized void write(int addr, int... values) throws IOException {
        request(16, addr, values.length, values);
    }

    private byte[] request(int fc, int addr, int count, int[] values) throws IOException {
        try {
            connect();
            ByteArrayOutputStream pdu = new ByteArrayOutputStream();
            DataOutputStream d = new DataOutputStream(pdu);
            d.writeByte(fc);
            d.writeShort(addr);
            d.writeShort(count);
            if (values != null) {
                d.writeByte(values.length * 2);
                for (int v : values) d.writeShort(v);
            }
            tid = (tid + 1) & 0xFFFF;
            out.writeShort(tid);
            out.writeShort(0);
            out.writeShort(pdu.size() + 1);
            out.writeByte(unit);
            pdu.writeTo(out);
            out.flush();
            int gotTid = in.readUnsignedShort();
            in.readUnsignedShort();
            int length = in.readUnsignedShort();
            in.readUnsignedByte();
            int gotFc = in.readUnsignedByte();
            byte[] body = new byte[length - 2];
            in.readFully(body);
            if (gotTid != tid) throw new IOException("Modbus reply out of step");
            if (gotFc != fc) throw new IOException("PLC refused the request (exception " + (body[0] & 0xFF) + ")");
            return body;
        } catch (IOException e) {
            close();
            throw e;
        }
    }
}
