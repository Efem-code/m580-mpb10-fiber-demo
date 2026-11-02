"""IT side of the demo: read the same PLC values over OPC UA from the BMENUA0100 control port.

Optional. Needs `python -m pip install asyncua`; without it the rest of the dashboard runs as before and the
"IT side" tab says how to add it. Runs its own asyncio loop in a background thread.

The variables are found by name (browsing from Objects), not by NodeId, because the NodeIds the BMENUA0100
gives the PLC variables depend on the project and firmware. Secured mode uses a self-signed client
certificate made on first use in ./opcua-pki/ (the NUA must be told to trust it).
"""
import asyncio
import os
import socket
import threading
import time

try:
    from asyncua import Client, ua
    AVAILABLE = True
except ImportError:          # asyncua not installed: the tab explains how to add it
    AVAILABLE = False

HERE = os.path.dirname(os.path.abspath(__file__))
PKI = os.path.join(HERE, "opcua-pki")
APP_URI = "urn:guillevin:m580-demo:dashboard"

# PLC variables to show, with what each means. Expose these in Control Expert (HMI column).
VARS = [
    ("iHeartbeat", "PLC heartbeat"),
    ("rVibVelocity", "Vibration velocity RMS, mm/s"),
    ("rTemperature", "Motor temperature, °C"),
    ("xVibWarning", "Vibration warning"),
    ("xVibAlarm", "Vibration alarm"),
    ("xTempWarning", "Temperature warning"),
    ("iShockCount", "Shock count"),
    ("iATV_Speed", "Motor speed, rpm"),
    ("iATV_SpeedRef", "Speed reference, rpm"),
    ("iATV_Current", "Motor current, 0.1 A"),
    ("xATV_Running", "Drive running"),
    ("xATV_Fault", "Drive fault"),
    ("xDrop_Health", "RIO drop healthy"),
    ("iRunSource", "Who started the motor (0 none, 1 panel, 2 dashboard, 3 table)"),
]
NAMES = {n for n, _ in VARS}
WRITE_TEST = "xDemoEnable"       # the write test writes back the value it already holds: nothing changes
BROWSE_MAX = 3000


class OpcUaSide:
    def __init__(self, get_cfg, event):
        self.get_cfg, self.event = get_cfg, event
        self.lock = threading.Lock()
        self.state = {"available": AVAILABLE, "vars": [list(v) for v in VARS], "status": "off", "values": {}, "found": 0, "missing": [],
                      "error": "", "server": "", "endpoint": "", "security": "", "latency_ms": None, "write": None}
        self.write_req = False
        if AVAILABLE:
            threading.Thread(target=lambda: asyncio.run(self._main()), daemon=True, name="opcua").start()

    def snapshot(self):
        with self.lock:
            return dict(self.state, values=dict(self.state["values"]))

    def request_write_test(self):
        if not AVAILABLE:
            return "OPC UA support isn’t installed on this laptop"
        if self.state["status"] != "connected":
            return "Not connected to the BMENUA0100"
        self.write_req = True
        return None

    def _set(self, **kw):
        with self.lock:
            self.state.update(kw)

    @staticmethod
    def _key(cfg):
        return (cfg["opcua_on"], cfg["nua_ctrl_host"], cfg["nua_ctrl_port"], cfg["opcua_security"],
                cfg["opcua_user"], cfg["opcua_password"])

    async def _main(self):
        last_err = ""
        while True:
            cfg = self.get_cfg()
            if not cfg["opcua_on"]:
                self._set(status="off", values={}, error="", latency_ms=None, write=None)
                await asyncio.sleep(0.5)
                continue
            key = self._key(cfg)
            url = f"opc.tcp://{cfg['nua_ctrl_host']}:{cfg['nua_ctrl_port']}"
            self._set(status="connecting", endpoint=url, error="", write=None)
            try:
                await self._session(cfg, url, key)
            except Exception as e:  # noqa: BLE001 - every failure is shown on the page
                msg = explain(e, cfg)
                self._set(status="error", error=msg, values={}, latency_ms=None)
                if msg != last_err:
                    self.event("warn", f"OPC UA (IT side): {msg}")
                    last_err = msg
                for _ in range(10):                      # retry after 5 s, sooner if the settings change
                    await asyncio.sleep(0.5)
                    if self._key(self.get_cfg()) != key:
                        break
                continue
            last_err = ""

    async def _session(self, cfg, url, key):
        client = Client(url, timeout=4)
        client.application_uri = APP_URI
        client.name = "Guillevin M580 demo dashboard"
        sec = "None"
        if cfg["opcua_security"] == "sign_encrypt":
            cert, pkey = await client_certificate()
            await client.set_security_string(f"Basic256Sha256,SignAndEncrypt,{cert},{pkey}")
            sec = "Basic256Sha256 · Sign & encrypt"
        if cfg["opcua_user"]:
            client.set_user(cfg["opcua_user"])
            client.set_password(cfg["opcua_password"])
        async with client:
            try:
                prod = await client.get_node(ua.NodeId(ua.ObjectIds.Server_ServerStatus_BuildInfo_ProductName)).read_value()
            except Exception:  # noqa: BLE001 - product name is a nicety
                prod = ""
            nodes = await find_by_name(client, NAMES | {WRITE_TEST})
            found = [n for n, _ in VARS if n in nodes]
            missing = [n for n, _ in VARS if n not in nodes]
            self._set(status="connected", server=prod or "OPC UA server", security=sec + (f" · user {cfg['opcua_user']}" if cfg["opcua_user"] else " · anonymous"),
                      found=len(found), missing=missing, error="" if found else
                      "Connected, but none of the demo variables are visible: tick them in the HMI column and rebuild")
            self.event("ok", f"OPC UA (IT side) connected to {url}: {len(found)} of {len(VARS)} variables found")
            order = [nodes[n] for n in found]
            while self._key(self.get_cfg()) == key:
                t0 = time.perf_counter()
                dvs = await client.read_attributes(order, ua.AttributeIds.Value) if order else []
                ms = round((time.perf_counter() - t0) * 1000, 1)
                vals = {}
                for name, dv in zip(found, dvs):
                    ts = dv.SourceTimestamp or dv.ServerTimestamp
                    vals[name] = {"v": plain(dv.Value.Value if dv.Value else None), "good": dv.StatusCode.is_good(),
                                  "ts": ts.timestamp() if ts else None}
                self._set(values=vals, latency_ms=ms)
                if self.write_req:
                    self.write_req = False
                    await self._write_test(nodes)
                await asyncio.sleep(0.5)
        self.event("info", "OPC UA (IT side) disconnected: settings changed")

    async def _write_test(self, nodes):
        name = WRITE_TEST if WRITE_TEST in nodes else next(iter(n for n, _ in VARS if n in nodes), None)
        if not name:
            self._set(write={"ok": None, "name": "", "text": "No variable to try"})
            return
        node = nodes[name]
        try:
            dv = await node.read_data_value()
            await node.write_value(ua.DataValue(dv.Value))     # the value it already has: nothing changes
            res = {"ok": True, "name": name, "text": f"Write to {name} was ACCEPTED (same value written back, nothing changed). "
                   "The IT side can change this variable: make it read-only for OPC UA if it shouldn’t."}
            self.event("warn", f"OPC UA write test: {name} is writable from the IT side")
        except ua.UaStatusCodeError as e:
            res = {"ok": False, "name": name, "text": f"Write to {name} was REFUSED by the server: {e.__class__.__name__}. "
                   "The IT side can read the machine but not change it."}
            self.event("ok", f"OPC UA write test: {name} refused from the IT side ({e.__class__.__name__})")
        except Exception as e:  # noqa: BLE001
            res = {"ok": None, "name": name, "text": f"Write test failed: {e}"}
        res["t"] = time.time()
        self._set(write=res)


async def find_by_name(client, names):
    """Browse down from Objects and return {BrowseName.Name: Node} for the names asked for."""
    out, seen, queue, count = {}, set(), [client.nodes.objects], 0
    while queue and count < BROWSE_MAX and len(out) < len(names):
        node = queue.pop(0)
        try:
            refs = await node.get_references(refs=ua.ObjectIds.HierarchicalReferences,
                                             direction=ua.BrowseDirection.Forward)
        except Exception:  # noqa: BLE001 - some nodes refuse browsing; skip them
            continue
        for r in refs:
            nid = r.NodeId.to_string()
            if nid in seen or r.NodeId.NamespaceIndex == 0:
                continue                               # namespace 0 is the server's own standard nodes
            seen.add(nid)
            count += 1
            child = client.get_node(r.NodeId)
            name = r.BrowseName.Name.split(".")[-1]
            if r.NodeClass == ua.NodeClass.Variable and name in names and name not in out:
                out[name] = child
            if r.NodeClass in (ua.NodeClass.Object, ua.NodeClass.Variable):
                queue.append(child)
    return out


async def client_certificate():
    from asyncua.crypto.cert_gen import setup_self_signed_certificate
    from cryptography.x509.oid import ExtendedKeyUsageOID
    os.makedirs(PKI, exist_ok=True)
    cert, key = os.path.join(PKI, "dashboard-cert.der"), os.path.join(PKI, "dashboard-key.pem")
    if not (os.path.exists(cert) and os.path.exists(key)):
        from pathlib import Path
        await setup_self_signed_certificate(Path(key), Path(cert), APP_URI, socket.gethostname(),
                                            [ExtendedKeyUsageOID.CLIENT_AUTH],
                                            {"countryName": "CA", "organizationName": "Guillevin demo",
                                             "commonName": "M580 demo dashboard"})
    return cert, key


def plain(v):
    if isinstance(v, float):
        return round(v, 3)
    return v if isinstance(v, (int, bool, str)) or v is None else str(v)


def explain(e, cfg):
    """Turn library errors into something a presenter can act on."""
    s = f"{e.__class__.__name__}: {e}".strip(": ")
    host = cfg["nua_ctrl_host"]
    if isinstance(e, (ConnectionRefusedError,)) or "refused" in s.lower():
        return f"{host} refused the connection on TCP {cfg['nua_ctrl_port']}: is the OPC UA server enabled on the BMENUA0100?"
    if isinstance(e, (asyncio.TimeoutError, TimeoutError, OSError)) and "BadUser" not in s:
        return (f"No answer from {host}:{cfg['nua_ctrl_port']}. Check the USB adapter is plugged into the NUA control "
                f"port and set to the same subnet (e.g. 192.168.30.100), with the NUA powered.")
    if "BadUserAccessDenied" in s or "BadIdentityToken" in s:
        return "The BMENUA0100 rejected the user name or password"
    if "BadSecurity" in s or "BadCertificate" in s:
        return ("The BMENUA0100 rejected the security settings or our certificate: trust the dashboard’s "
                "certificate on the module, or try Security: None (Standard mode)")
    return s[:200]
