"""Minimal Modbus TCP server + client (stdlib only).

Supports FC 01, 02, 03, 04, 05, 06, 15, 16 -- everything the Lexium Cobot
address table uses. The server is what the simulator exposes, so the PC
controller *and later the TM262 PLC* can talk to the sim exactly as they
would talk to the real cobot.
"""
import socket
import socketserver
import struct
import threading


class ModbusError(Exception):
    pass


class DataStore:
    """Four Modbus areas, each a sparse dict address->value, thread-safe."""

    def __init__(self):
        self.lock = threading.RLock()
        self.coils = {}
        self.discrete = {}
        self.holding = {}
        self.inputs = {}
        self.on_write = None  # callback(area, address, values)

    def read(self, area, addr, count):
        with self.lock:
            d = getattr(self, area)
            return [d.get(addr + i, 0) for i in range(count)]

    def write(self, area, addr, values, external=False):
        with self.lock:
            d = getattr(self, area)
            for i, v in enumerate(values):
                d[addr + i] = v
        if external and self.on_write:
            self.on_write(area, addr, values)


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf += chunk
    return buf


# ------------------------------------------------------------------ server
class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        store = self.server.store
        sock = self.request
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            while True:
                hdr = _recv_exact(sock, 7)
                tid, pid, length, uid = struct.unpack(">HHHB", hdr)
                pdu = _recv_exact(sock, length - 1)
                resp = self._process(store, pdu)
                sock.sendall(struct.pack(">HHHB", tid, 0, len(resp) + 1, uid) + resp)
        except (ConnectionError, OSError):
            return

    @staticmethod
    def _process(store, pdu):
        fc = pdu[0]
        try:
            if fc in (1, 2):
                addr, cnt = struct.unpack(">HH", pdu[1:5])
                if not 1 <= cnt <= 2000:
                    return bytes([fc | 0x80, 3])
                bits = store.read("coils" if fc == 1 else "discrete", addr, cnt)
                data = bytearray((cnt + 7) // 8)
                for i, b in enumerate(bits):
                    if b:
                        data[i // 8] |= 1 << (i % 8)
                return bytes([fc, len(data)]) + bytes(data)
            if fc in (3, 4):
                addr, cnt = struct.unpack(">HH", pdu[1:5])
                if not 1 <= cnt <= 125:
                    return bytes([fc | 0x80, 3])
                regs = store.read("holding" if fc == 3 else "inputs", addr, cnt)
                return bytes([fc, cnt * 2]) + struct.pack(">%dH" % cnt, *regs)
            if fc == 5:
                addr, val = struct.unpack(">HH", pdu[1:5])
                store.write("coils", addr, [1 if val == 0xFF00 else 0], external=True)
                return pdu[:5]
            if fc == 6:
                addr, val = struct.unpack(">HH", pdu[1:5])
                store.write("holding", addr, [val], external=True)
                return pdu[:5]
            if fc == 15:
                addr, cnt, nbytes = struct.unpack(">HHB", pdu[1:6])
                raw = pdu[6:6 + nbytes]
                bits = [(raw[i // 8] >> (i % 8)) & 1 for i in range(cnt)]
                store.write("coils", addr, bits, external=True)
                return bytes([fc]) + struct.pack(">HH", addr, cnt)
            if fc == 16:
                addr, cnt, nbytes = struct.unpack(">HHB", pdu[1:6])
                regs = list(struct.unpack(">%dH" % cnt, pdu[6:6 + 2 * cnt]))
                store.write("holding", addr, regs, external=True)
                return bytes([fc]) + struct.pack(">HH", addr, cnt)
        except struct.error:
            return bytes([fc | 0x80, 3])
        return bytes([fc | 0x80, 1])  # illegal function


class ModbusServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, host, port, store):
        self.store = store
        super().__init__((host, port), _Handler)

    def start(self):
        t = threading.Thread(target=self.serve_forever, daemon=True, name="modbus-server")
        t.start()
        return t


# ------------------------------------------------------------------ client
class ModbusClient:
    def __init__(self, host, port=502, unit=1, timeout=2.0):
        self.host, self.port, self.unit, self.timeout = host, port, unit, timeout
        self.sock = None
        self.tid = 0
        self.lock = threading.Lock()

    def connect(self):
        self.close()
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def _req(self, pdu):
        with self.lock:
            if not self.sock:
                self.connect()
            self.tid = (self.tid + 1) & 0xFFFF
            try:
                self.sock.sendall(struct.pack(">HHHB", self.tid, 0, len(pdu) + 1, self.unit) + pdu)
                hdr = _recv_exact(self.sock, 7)
                tid, _, length, _ = struct.unpack(">HHHB", hdr)
                resp = _recv_exact(self.sock, length - 1)
            except (OSError, ConnectionError):
                self.close()
                raise
        if resp[0] & 0x80:
            raise ModbusError(f"exception code {resp[1]} for FC{pdu[0]}")
        return resp

    def _read_bits(self, fc, addr, cnt):
        r = self._req(struct.pack(">BHH", fc, addr, cnt))
        data = r[2:]
        return [(data[i // 8] >> (i % 8)) & 1 for i in range(cnt)]

    def _read_regs(self, fc, addr, cnt):
        r = self._req(struct.pack(">BHH", fc, addr, cnt))
        return list(struct.unpack(">%dH" % cnt, r[2:2 + 2 * cnt]))

    def read_coils(self, addr, cnt):
        return self._read_bits(1, addr, cnt)

    def read_discrete(self, addr, cnt):
        return self._read_bits(2, addr, cnt)

    def read_holding(self, addr, cnt):
        return self._read_regs(3, addr, cnt)

    def read_inputs(self, addr, cnt):
        out = []
        while cnt > 0:  # FC04 max 125 words per request
            n = min(cnt, 120)
            out += self._read_regs(4, addr, n)
            addr += n
            cnt -= n
        return out

    def write_coil(self, addr, on):
        self._req(struct.pack(">BHH", 5, addr, 0xFF00 if on else 0))

    def write_register(self, addr, val):
        self._req(struct.pack(">BHH", 6, addr, val & 0xFFFF))

    def write_registers(self, addr, vals):
        self._req(struct.pack(">BHHB", 16, addr, len(vals), 2 * len(vals)) +
                  struct.pack(">%dH" % len(vals), *vals))
