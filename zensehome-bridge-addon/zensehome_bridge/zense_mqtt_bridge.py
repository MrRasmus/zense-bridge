import os
import json
import time
import socket
import queue
import threading
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple

import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion


# ------------------ CONFIG (from env) ------------------
ZENSE_IP = os.getenv("ZENSE_IP", "192.168.1.235")
ZENSE_PORT = int(os.getenv("ZENSE_PORT", "10001"))
ZENSE_CODE = int(os.getenv("ZENSE_CODE", "16713"))

MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASS = os.getenv("MQTT_PASS", "")

BRIGHTNESS_SCALE = 100
SOCKET_TIMEOUT = float(os.getenv("SOCKET_TIMEOUT", "12"))

DEBOUNCE_MS = int(os.getenv("DEBOUNCE_MS", "120"))
CMD_GAP_SEC = float(os.getenv("CMD_GAP_SEC", "0.10"))

# Polling for wall-switch changes
STATE_POLL_SEC = int(os.getenv("STATE_POLL_SEC", "600"))  # 300=5 min, 600=10 min

# Ignore ON that arrives right after brightness/set
LEVEL_ON_WINDOW_SEC = float(os.getenv("LEVEL_ON_WINDOW_SEC", "1.0"))

DEBUG_MQTT = str(os.getenv("DEBUG_MQTT", "0")).strip().lower() in ("1", "true", "yes", "on")

DISCOVERY_PREFIX = os.getenv("DISCOVERY_PREFIX", "homeassistant")
BASE = os.getenv("BASE", "homeassistant/zense_bridge")
DOMAIN = os.getenv("DOMAIN", "light")
UID_PREFIX = os.getenv("UID_PREFIX", "zensebridge_")

AVAIL_ON = "online"
AVAIL_OFF = "offline"
AVAIL_TOPIC = f"{BASE}/availability"


def log(msg: str):
    print(f"[zense-bridge] {msg}", flush=True)


# ------------------ ZENSE CLIENT (AUTO RECOVER) ------------------
class ZenseClient:
    def __init__(self, ip: str, port: int, code: int):
        self.ip = ip
        self.port = port
        self.code = code
        self.sock: Optional[socket.socket] = None
        self.logged_in = False
        self.lock = threading.Lock()

    def _close(self):
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None
        self.logged_in = False

    def _connect(self) -> bool:
        self._close()
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(SOCKET_TIMEOUT)
            s.connect((self.ip, self.port))
            self.sock = s
            return True
        except Exception as e:
            self._close()
            log(f"connect failed: {e}")
            return False

    def _recv(self) -> str:
        buf = ""
        while True:
            part = self.sock.recv(1024).decode(errors="replace")
            if not part:
                break
            buf += part
            if "<<" in buf:
                break
        return buf

    def _send_raw(self, cmd: str) -> str:
        self.sock.sendall(cmd.encode())
        return self._recv()

    def _login(self) -> bool:
        if not self._connect():
            return False
        try:
            resp = self._send_raw(f">>Login {self.code}<<")
            if ">>Login Ok<<" in resp:
                self.logged_in = True
                return True
            log(f"login failed: {resp!r}")
        except Exception as e:
            log(f"login error: {e}")
        self._close()
        return False

    def send_command(self, cmd: str, retry: int = 1) -> str:
        with self.lock:
            for attempt in range(retry + 1):
                try:
                    if not self.logged_in or not self.sock:
                        if not self._login():
                            return ""
                    return self._send_raw(cmd)
                except (BrokenPipeError, ConnectionResetError, OSError) as e:
                    log(f"send error: {e} (attempt {attempt+1})")
                    self._close()
                    continue
                except Exception as e:
                    log(f"send error: {e}")
                    self._close()
                    return ""
            return ""

    def set_off(self, did: int) -> str:
        return self.send_command(f">>Set {did} 0<<")

    def set_on(self, did: int) -> str:
        return self.send_command(f">>Set {did} {BRIGHTNESS_SCALE}<<")

    def fade(self, did: int, level: int) -> str:
        level = max(0, min(BRIGHTNESS_SCALE, int(level)))
        return self.send_command(f">>Fade {did} {level}<<")

    def get_level(self, did: int) -> Optional[int]:
        resp = self.send_command(f">>Get {did}<<")
        if ">>Get " in resp:
            try:
                val = resp.split(">>Get ")[1].split("<<")[0].strip()
                return int(val)
            except Exception:
                return None
        return None

    def get_devices(self):
        resp = self.send_command(">>Get Devices<<")
        if ">>Get Devices " in resp:
            part = resp.split(">>Get Devices ")[1].split("<<")[0]
            ids = [x.strip() for x in part.split(",")]
            return [int(x) for x in ids if x.isdigit()]
        return []

    def get_name(self, did: int) -> str:
        resp = self.send_command(f">>Get Name {did}<<")
        if ">>Get Name " in resp:
            nm = resp.split(">>Get Name ")[1].split("<<")[0].strip().strip("'")
            if nm and nm.lower() != "timeout":
                return nm
        return f"Device_{did}"


# ------------------ MQTT TOPICS ------------------
def uid(did: int) -> str:
    return f"{UID_PREFIX}{did}"

def topics(u: str) -> Dict[str, str]:
    return {
        "cmd": f"{BASE}/{u}/set",
        "state": f"{BASE}/{u}/state",
        "b_cmd": f"{BASE}/{u}/brightness/set",
        "b_state": f"{BASE}/{u}/brightness/state",
        "disc": f"{DISCOVERY_PREFIX}/{DOMAIN}/{u}/config",
    }


@dataclass
class Pending:
    off: bool = False
    on: bool = False
    level: Optional[int] = None  # level wins over on


def _to_int(v: Any) -> Optional[int]:
    try:
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return int(round(v))
        s = str(v).strip()
        if s == "":
            return None
        return int(round(float(s)))
    except Exception:
        return None

def scale_brightness(raw: int) -> int:
    if raw < 0:
        return 0
    if raw <= BRIGHTNESS_SCALE:
        return raw
    raw = min(raw, 255)
    return int(round((raw / 255.0) * BRIGHTNESS_SCALE))


# ------------------ BRIDGE ------------------
class Bridge:
    def __init__(self):
        self.mq = mqtt.Client(
            client_id=f"zense-bridge-{int(time.time())}",
            clean_session=True,
            callback_api_version=CallbackAPIVersion.VERSION2,
        )
        if MQTT_USER:
            self.mq.username_pw_set(MQTT_USER, MQTT_PASS)

        self.mq.on_connect = self.on_connect
        self.mq.on_message = self.on_message
        self.mq.on_disconnect = self.on_disconnect

        self.cmd_q: "queue.Queue[Tuple[str, int, Optional[int]]]" = queue.Queue()
        self.pending: Dict[int, Pending] = {}
        self.pending_lock = threading.Lock()

        self.last_level_pub: Dict[str, int] = {}
        self.last_level_ts: Dict[int, float] = {}

        self.z = ZenseClient(ZENSE_IP, ZENSE_PORT, ZENSE_CODE)
        self.known: List[int] = []

        self.worker_th = threading.Thread(target=self.worker_loop, daemon=True)
        self.poller_th = threading.Thread(target=self.poller_loop, daemon=True)

    def pub(self, topic: str, payload: str, retain: bool = False):
        self.mq.publish(topic, payload=payload, retain=retain, qos=0)

    def pub_avail(self, online: bool):
        self.pub(AVAIL_TOPIC, AVAIL_ON if online else AVAIL_OFF, retain=True)

    def pub_state(self, did: int, level: int):
        lvl = max(0, min(BRIGHTNESS_SCALE, int(level)))
        u = uid(did)
        if self.last_level_pub.get(u) == lvl:
            return
        self.last_level_pub[u] = lvl
        t = topics(u)
        self.pub(t["state"], "ON" if lvl > 0 else "OFF", retain=True)
        self.pub(t["b_state"], str(lvl), retain=True)

    def pub_discovery(self, did: int, name_str: str):
        u = uid(did)
        t = topics(u)
        payload = {
            "name": f"{name_str} (Zense)",
            "unique_id": u,
            "command_topic": t["cmd"],
            "state_topic": t["state"],
            "brightness_command_topic": t["b_cmd"],
            "brightness_state_topic": t["b_state"],
            "brightness_scale": BRIGHTNESS_SCALE,
            "payload_on": "ON",
            "payload_off": "OFF",
            "availability_topic": AVAIL_TOPIC,
            "payload_available": AVAIL_ON,
            "payload_not_available": AVAIL_OFF,
            "optimistic": False,
            "qos": 0,
        }
        self.pub(t["disc"], json.dumps(payload), retain=True)

    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        log(f"MQTT connected reason_code={reason_code}")
        client.subscribe("homeassistant/status")
        client.subscribe(f"{BASE}/+/set")
        client.subscribe(f"{BASE}/+/brightness/set")
        self.pub_avail(True)
        self.cmd_q.put(("discover", 0, None))

    def on_disconnect(self, client, userdata, reason_code, properties=None):
        log(f"MQTT disconnected reason_code={reason_code}")

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        payload = msg.payload.decode(errors="replace").strip()

        if topic == "homeassistant/status" and payload == "online":
            self.cmd_q.put(("discover", 0, None))
            return

        parts = topic.split("/")
        if len(parts) < 4:
            return
        u = parts[2]
        if not u.startswith(UID_PREFIX):
            return
        try:
            did = int(u[len(UID_PREFIX):])
        except Exception:
            return

        if DEBUG_MQTT and (topic.endswith("/set") or topic.endswith("/brightness/set")):
            log(f"RX topic={topic} payload={payload!r}")

        if topic.endswith("/brightness/set"):
            raw = _to_int(payload)
            if raw is None:
                return
            br = scale_brightness(raw)
            self.last_level_ts[did] = time.time()
            self.cmd_q.put(("level", did, br))
            return

        if topic.endswith("/set"):
            up = payload.upper()
            if up == "OFF":
                self.cmd_q.put(("off", did, None))
                return
            if up == "ON":
                ts = self.last_level_ts.get(did, 0.0)
                if (time.time() - ts) <= LEVEL_ON_WINDOW_SEC:
                    return
                self.cmd_q.put(("on", did, None))
                return

    def poller_loop(self):
        while True:
            time.sleep(max(60, STATE_POLL_SEC))
            if self.known:
                self.cmd_q.put(("refresh", 0, None))

    def worker_loop(self):
        debounce_s = DEBOUNCE_MS / 1000.0
        while True:
            kind, did, val = self.cmd_q.get()
            self._accumulate(kind, did, val)
            time.sleep(debounce_s)
            self._drain()
            self._execute()

    def _accumulate(self, kind: str, did: int, val: Optional[int]):
        if kind in ("discover", "refresh"):
            with self.pending_lock:
                p = self.pending.get(0) or Pending()
                if kind == "discover":
                    p.on = True
                else:
                    p.off = True
                self.pending[0] = p
            return

        with self.pending_lock:
            p = self.pending.get(did) or Pending()
            if kind == "off":
                p.off = True
                p.on = False
                p.level = None
            elif kind == "on":
                if not p.off and p.level is None:
                    p.on = True
            elif kind == "level":
                lvl = max(0, min(BRIGHTNESS_SCALE, int(val or 0)))
                if lvl == 0:
                    p.off = True
                    p.on = False
                    p.level = None
                else:
                    p.level = lvl
                    p.on = False
                    p.off = False
            self.pending[did] = p

    def _drain(self):
        for _ in range(200):
            try:
                kind, did, val = self.cmd_q.get_nowait()
                self._accumulate(kind, did, val)
            except queue.Empty:
                break

    def _execute(self):
        with self.pending_lock:
            items = list(self.pending.items())
            self.pending.clear()

        do_discover = any(did == 0 and p.on for did, p in items)
        do_refresh = any(did == 0 and p.off for did, p in items)

        if do_discover:
            ids = self.z.get_devices()
            if ids:
                self.known = ids
                log(f"discovered: {ids}")
                for did in ids:
                    nm = self.z.get_name(did)
                    self.pub_discovery(did, nm)
                    time.sleep(CMD_GAP_SEC)

        if do_refresh and self.known:
            for did in self.known:
                lvl = self.z.get_level(did)
                if lvl is not None:
                    self.pub_state(did, lvl)
                time.sleep(CMD_GAP_SEC)

        for did, p in items:
            if did == 0:
                continue
            if p.off:
                if DEBUG_MQTT:
                    log(f"TX OFF did={did}")
                resp = self.z.set_off(did)
                if resp:
                    self.pub_state(did, 0)
                time.sleep(CMD_GAP_SEC)
            elif p.level is not None:
                if DEBUG_MQTT:
                    log(f"TX FADE did={did} level={p.level}")
                resp = self.z.fade(did, p.level)
                if resp:
                    self.pub_state(did, p.level)
                time.sleep(CMD_GAP_SEC)
            elif p.on:
                if DEBUG_MQTT:
                    log(f"TX ON did={did}")
                resp = self.z.set_on(did)
                if resp:
                    self.pub_state(did, BRIGHTNESS_SCALE)
                time.sleep(CMD_GAP_SEC)

    def start(self):
        log(f"MQTT connect {MQTT_HOST}:{MQTT_PORT} (user={'yes' if MQTT_USER else 'no'})")
        self.mq.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
        self.worker_th.start()
        self.poller_th.start()
        self.mq.loop_forever()


if __name__ == "__main__":
    Bridge().start()
