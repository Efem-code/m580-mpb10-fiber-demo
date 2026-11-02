"""Stand-in BMENUA0100: an OPC UA server with the demo's PLC variables, so the IT side can be rehearsed
without the module. Needs `python -m pip install asyncua`.

  python fake_nua.py                       values from the dashboard on this laptop (works in simulation)
  python fake_nua.py --plc 127.0.0.1:5020  values straight from the PLC (or fake_m580.py) over Modbus,
                                           so the two sides really are two separate paths
  python fake_nua.py --host 0.0.0.0        listen on every network, not just this laptop

Then in the dashboard: IP addresses tab -> BMENUA0100 control port = 127.0.0.1, and connect from the
"IT side · OPC UA" tab. Like the real module, every variable is read-only: the write test is refused.
Security None and Basic256Sha256 Sign & encrypt are offered; users: anonymous, or demo / demo.
"""
import argparse
import asyncio
import datetime
import json
import os
import socket
import urllib.request
from pathlib import Path

from asyncua import Server, ua
from asyncua.crypto.cert_gen import setup_self_signed_certificate
from asyncua.server.user_managers import UserManager
from asyncua.crypto.permission_rules import User, UserRole
from cryptography.x509.oid import ExtendedKeyUsageOID

from modbus import ModbusClient
from opcua_side import VARS, WRITE_TEST

HERE = os.path.dirname(os.path.abspath(__file__))
PKI = os.path.join(HERE, "opcua-pki")
TYPES = {"r": ua.VariantType.Float, "i": ua.VariantType.Int16, "x": ua.VariantType.Boolean}


class DemoUsers(UserManager):
    def get_user(self, iserver, username=None, password=None, certificate=None):
        if username is None:
            return User(role=UserRole.User)            # anonymous: read-only like any other user here
        if username == "demo" and password == "demo":
            return User(role=UserRole.User)
        return None                                    # -> BadUserAccessDenied on the client


def s16(v):
    return v - 65536 if v > 32767 else v


def from_registers(r):
    """Status block (%MW1000..) -> the PLC variables, as the M580 holds them."""
    st, db, pb = r[1], r[20], r[29]
    return {"iHeartbeat": s16(r[0]), "rVibVelocity": s16(r[2]) / 100, "rTemperature": s16(r[6]) / 10,
            "xVibWarning": bool(st & 4), "xVibAlarm": bool(st & 8), "xTempWarning": bool(st & 32),
            "iShockCount": s16(r[7]), "iATV_Speed": s16(r[22]), "iATV_SpeedRef": s16(r[23]),
            "iATV_Current": s16(r[24]), "xATV_Running": bool(db & 4), "xATV_Fault": bool(db & 8),
            "xDrop_Health": bool(pb & 1), "iRunSource": 1 if pb & 1024 else (2 if db & 4 else 0),
            "xDemoEnable": bool(pb & 16)}


def from_dashboard(s):
    m, d, p = s.get("mpb", {}), s.get("drive", {}), s.get("panel", {})
    c = s.get("control", {})
    return {"iHeartbeat": (s.get("plc") or {}).get("heartbeat") or 0, "rVibVelocity": m.get("v") or 0.0,
            "rTemperature": m.get("temp") or 0.0, "xVibWarning": bool(m.get("warn")), "xVibAlarm": bool(m.get("alarm")),
            "xTempWarning": bool(m.get("temp_warn")), "iShockCount": m.get("shocks") or 0,
            "iATV_Speed": round(d.get("speed") or 0), "iATV_SpeedRef": round(d.get("ref") or 0),
            "iATV_Current": round((d.get("current") or 0) * 10), "xATV_Running": bool(d.get("running")),
            "xATV_Fault": bool(d.get("fault")), "xDrop_Health": bool(p.get("drop")),
            "iRunSource": 1 if p.get("from_panel") else (2 if c.get("run") and d.get("running") else 0),
            "xDemoEnable": bool(p.get("enable"))}


async def main():
    ap = argparse.ArgumentParser(description="Stand-in BMENUA0100 OPC UA server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=4840)
    ap.add_argument("--plc", help="read the status block over Modbus from HOST:PORT instead of the dashboard")
    ap.add_argument("--dashboard", default="http://127.0.0.1:8780")
    ap.add_argument("--base", type=int, default=1000)
    ap.add_argument("--unit", type=int, default=255)
    a = ap.parse_args()

    os.makedirs(PKI, exist_ok=True)
    cert, key = Path(PKI, "standin-cert.der"), Path(PKI, "standin-key.pem")
    if not (cert.exists() and key.exists()):
        await setup_self_signed_certificate(key, cert, "urn:guillevin:m580-demo:standin-nua", socket.gethostname(),
                                            [ExtendedKeyUsageOID.SERVER_AUTH],
                                            {"countryName": "CA", "organizationName": "Guillevin demo",
                                             "commonName": "Stand-in BMENUA0100"})
    srv = Server(user_manager=DemoUsers())
    await srv.init()
    await srv.set_application_uri("urn:guillevin:m580-demo:standin-nua")   # matches its certificate
    srv.set_endpoint(f"opc.tcp://{a.host}:{a.port}")
    srv.set_server_name("Stand-in BMENUA0100 (demo)")
    await srv.set_build_info("urn:guillevin:m580-demo:standin-nua", "Schneider Electric (stand-in)",
                             "BMENUA0100 stand-in", "1.0", "0", datetime.datetime.now())
    srv.set_security_policy([ua.SecurityPolicyType.NoSecurity, ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt])
    await srv.load_certificate(str(cert))
    await srv.load_private_key(str(key))
    ns = await srv.register_namespace("urn:schneider:bmenua0100:standin")
    plc = await srv.nodes.objects.add_folder(ua.NodeId("M580", ns), ua.QualifiedName("M580", ns))
    app = await plc.add_folder(ua.NodeId("M580.Application", ns), ua.QualifiedName("Application", ns))
    nodes = {}
    for name in [n for n, _ in VARS] + [WRITE_TEST]:
        vt = TYPES[name[0]]
        init = False if vt == ua.VariantType.Boolean else (0.0 if vt == ua.VariantType.Float else 0)
        nodes[name] = await app.add_variable(ua.NodeId(f"Application.{name}", ns), ua.QualifiedName(name, ns),
                                             ua.Variant(init, vt))       # not set_writable(): read-only
    mb = None
    if a.plc:
        host, _, port = a.plc.partition(":")
        mb = ModbusClient(host, int(port or 502), a.unit, timeout=1.0)
    print(f"Stand-in BMENUA0100 on opc.tcp://{a.host}:{a.port}  (values from {a.plc or a.dashboard})")
    async with srv:
        while True:
            try:
                if mb:
                    vals = from_registers(await asyncio.to_thread(mb.read_holding, a.base, 32))
                else:
                    raw = await asyncio.to_thread(lambda: urllib.request.urlopen(a.dashboard + "/api/state", timeout=2).read())
                    vals = from_dashboard(json.loads(raw))
                now = datetime.datetime.now(datetime.timezone.utc)
                for name, node in nodes.items():
                    vt = TYPES[name[0]]
                    v = vals.get(name)
                    v = bool(v) if vt == ua.VariantType.Boolean else float(v) if vt == ua.VariantType.Float else max(-32768, min(32767, int(v)))
                    await srv.write_attribute_value(node.nodeid, ua.DataValue(ua.Variant(v, vt), SourceTimestamp=now, ServerTimestamp=now))
            except Exception as e:  # noqa: BLE001 - keep serving; the values just stop updating
                print("source:", e)
                if mb:
                    mb.close()
            await asyncio.sleep(0.5)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
