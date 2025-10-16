import os
import sys
import time
import json
import atexit
import signal
import threading
from typing import Optional, List, Dict, Set, Tuple
import cv2
from flask import Flask, render_template, request, jsonify, Response
import serial
import requests
import logging
import yaml
from camera import BaslerCamera # MODIFIED: Import BaslerCamera

# ========= Logging (console + file) =========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("app.log", mode="a", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("ivc")

def rid() -> str:
    return f"[{time.strftime('%H:%M:%S')}.{int((time.time()%1)*1000):03d}]"

# ========= GPIO and LED control =========
import Jetson.GPIO as GPIO
import atexit

def gpio_cleanup():
    logger.debug(f"[GPIO][gpio_cleanup] GPIO cleanup")
    GPIO.cleanup()
class LED:
    def __init__(self, pin=7):
        self.led_pin = pin
        self.led_status = True
        GPIO.setmode(GPIO.BOARD)
        GPIO.setup(self.led_pin, GPIO.OUT, initial=GPIO.LOW)
        atexit.register(GPIO.cleanup)  # auto cleanup on exit
        log.info("[GPIO][LED] Initialized")

    def switch_led(self):
        self.led_status = not self.led_status
        GPIO.output(self.led_pin, GPIO.HIGH if self.led_status else GPIO.LOW)
        log.info(f"[GPIO][LED] Led status: {self.led_status}")

# Example usage
# led = LED(7)
# for i in range(100):
#     led.switch_led()  # toggle LED
#     time.sleep(2)


# ========= Serial & timing config =========
RS485_PORT = os.getenv("RS485_PORT", "/dev/ttyUSB0")
RS485_BAUD = int(os.getenv("RS485_BAUD", "115200"))
RS485_BYTESIZE = 8
RS485_PARITY = "O"
RS485_STOPBITS = 1
RS485_TIMEOUT = float(os.getenv("RS485_TIMEOUT", "0.5"))
INTER_CMD_DELAY = float(os.getenv("INTER_CMD_DELAY", "0.100"))
READ_DEADLINE_DEFAULT = float(os.getenv("RS485_READ_DEADLINE", "60.0"))
IDLE_GAP_DEFAULT = float(os.getenv("RS485_IDLE_GAP", "1.0"))

# ========= Track motion params =========
CLICK_VEL = 5000
CLICK_ACC_MS = 100
CLICK_DEC_MS = 100
# CLICK_Y_STOPS_CM = [165 - (10 - i)*18.1 for i in range(1, 11)]
CLICK_Y_STOPS_CM = [166 - (10 - i)*18.1 for i in range(1, 11)]

# ========= AGV proxy config =========
AGV_BASE_URL = os.getenv("AGV_BASE_URL", "http://192.168.0.172:8080")
AGV_ID = int(os.getenv("AGV_ID", "1"))
AGV_TARGETS = json.loads(os.getenv("AGV_TARGETS_JSON", json.dumps({
    "1": "1002", "2": "1003", "3": "1004", "4": "1005", "5": "1006", "6": "1007", "7": "1009"
})))
AGV_REQ_TIMEOUT_S = float(os.getenv("AGV_REQ_TIMEOUT_S", "3.0"))
AGV_REQ_RETRIES = int(os.getenv("AGV_REQ_RETRIES", "3"))

CR = b"\r"
app = Flask(__name__)
log.info(f"[BOOT] AGV_BASE_URL = {AGV_BASE_URL}")

# ========= Persistent serial state =========
SER: Optional[serial.Serial] = None
SER_LOCK = threading.Lock()
_TRACK_LOCK = threading.Lock()
_SHUTDOWN_EVENT = threading.Event()

# ========= MODIFIED: Global Camera Instance =========
try:
    with open("config.yaml", "r", encoding="utf-8") as f:
        _CFG = yaml.safe_load(f) or {}
    CAM_CFG = _CFG.get("Camera", {}) or {}
except Exception as e:
    log.warning(f"[CAM] load config.yaml failed: {e}")
    CAM_CFG = {}

CAMERA: Optional[BaslerCamera] = None

def camera_init():
    global CAMERA
    if CAMERA is None:
        log.info("[CAM] Initializing BaslerCamera instance...")
        try:
            CAMERA = BaslerCamera(camera_config=CAM_CFG)
            if not CAMERA.is_camera_initialized:
                log.error("[CAM] Camera instance created, but failed to initialize device.")
                CAMERA = None # Set back to None if it failed
        except Exception as e:
            log.error(f"[CAM] Failed to instantiate BaslerCamera: {e}", exc_info=True)
            CAMERA = None

def camera_close():
    global CAMERA
    if CAMERA:
        log.info("[CAM] Closing camera...")
        CAMERA.close()
        CAMERA = None
# --- End of Camera modifications ---

def track_busy() -> bool:
    return _TRACK_LOCK.locked()

def open_serial_port() -> serial.Serial:
    import serial as _serial
    byte_size_map = {5: _serial.FIVEBITS, 6: _serial.SIXBITS, 7: _serial.SEVENBITS, 8: _serial.EIGHTBITS}
    parity_map = {'N': _serial.PARITY_NONE, 'E': _serial.PARITY_EVEN, 'O': _serial.PARITY_ODD,
                  'M': _serial.PARITY_MARK, 'S': _serial.PARITY_SPACE}
    stop_map = {1: _serial.STOPBITS_ONE, 2: _serial.STOPBITS_TWO}
    ser = _serial.Serial(
        port=RS485_PORT,
        baudrate=RS485_BAUD,
        bytesize=byte_size_map.get(RS485_BYTESIZE, _serial.EIGHTBITS),
        parity=parity_map.get(RS485_PARITY.upper(), _serial.PARITY_ODD),
        stopbits=stop_map.get(RS485_STOPBITS, _serial.STOPBITS_ONE),
        timeout=RS485_TIMEOUT,
        write_timeout=RS485_TIMEOUT,
        rtscts=False, dsrdtr=False, xonxoff=False
    )
    return ser

def serial_init(retries: int = 3, delay_s: float = 1.0):
    global SER
    if SER and getattr(SER, "is_open", False):
        log.info("[BOOT] Serial port already open.")
        return SER
    for i in range(retries):
        try:
            log.info(f"[BOOT] Attempt {i+1}/{retries} to open serial {RS485_PORT} {RS485_BAUD}bps 8{RS485_PARITY}1 timeout={RS485_TIMEOUT}s")
            SER = open_serial_port()
            log.info("[BOOT] Serial opened successfully.")
            return SER
        except serial.SerialException as e:
            log.warning(f"[BOOT] Serial open failed: {e}. Retrying in {delay_s}s...")
            SER = None
            time.sleep(delay_s)
        except Exception as e:
            log.error(f"[BOOT] Unexpected error during serial init: {e}")
            SER = None
            break
    log.error(f"[BOOT] Failed to open serial port after {retries} attempts.")
    return None

def serial_close():
    global SER
    _SHUTDOWN_EVENT.set()
    log.info("[SHUTDOWN] Signaled worker threads to stop.")
    with SER_LOCK:
        try:
            if SER and getattr(SER, "is_open", False):
                log.info("[SHUTDOWN] closing serial")
                SER.close()
                log.info("[SHUTDOWN] serial closed")
        except Exception as e:
            log.warning(f"[SHUTDOWN] serial close error: {e}")
        finally:
            SER = None

def serial_get_or_raise() -> serial.Serial:
    global SER
    if SER and getattr(SER, "is_open", False):
        return SER
    log.warning("[SERIAL] Serial port not open, attempting re-initialization.")
    SER = serial_init(retries=1, delay_s=0.5)
    if SER and getattr(SER, "is_open", False):
        return SER
    raise serial.SerialException("Serial port is not available or could not be re-opened.")

MSG_CLASS: Dict[str, Dict[str, str]] = { "DONE": {"type": "success", "text": "Movement completed." }, "HOME_OK": {"type": "success", "text": "Homing completed successfully." }, "RESTART_OK": {"type": "success", "text": "Controller restarted successfully." }, "ERROR_HOME": {"type": "error",   "text": "Homing failed." }, "ERROR_REPEAT": {"type": "error",   "text": "Repeated/duplicate command ignored." }, "ERROR_DRIVER": {"type": "error",   "text": "Motor driver fault." }, "ERROR_INPOS":  {"type": "error",   "text": "Position not reached / in-position error." }, "NOT_HOME_OK":  {"type": "error",   "text": "Not at HOME (reported as OK)." }, "OK":           {"type": "success", "text": "Command acknowledged/OK." } }
ERROR_CODES: Set[str] = {k for k, v in MSG_CLASS.items() if v["type"] == "error"}
TERMINAL_OK: Set[str] = {"DONE", "HOME_OK", "RESTART_OK"}

def agv_url(path: str) -> str:
    base = AGV_BASE_URL.rstrip("/")
    if not path.startswith("/"): path = "/" + path
    return f"{base}{path}"

def http_get(url: str, timeout: float, req_id: str = "") -> requests.Response:
    # log.info(f"{req_id} [AGV] GET {url}")
    resp = requests.get(url, timeout=timeout)
    # log.info(f"{req_id} [AGV] <- HTTP {resp.status_code} {resp.text[:300]}")
    return resp

def http_post_json(url: str, payload: dict, timeout: float, req_id: str = "") -> requests.Response:
    body = json.dumps(payload, ensure_ascii=False)
    log.info(f"{req_id} [AGV] POST {url} body={body}")
    resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=timeout)
    log.info(f"{req_id} [AGV] <- HTTP {resp.status_code} {resp.text[:300]}")
    return resp

def agv_fetch_all(req_id: str = "") -> dict:
    url = agv_url("/YIDAGV/api/agv/data")
    resp = http_get(url, AGV_REQ_TIMEOUT_S, req_id=req_id)
    resp.raise_for_status()
    return resp.json()

def send_task_to_agv(target: str, req_id: str = "") -> dict:
    url = agv_url("/YIDAGV/api/task/send-task")
    last_err = None
    for i in range(1, AGV_REQ_RETRIES + 1):
        try:
            resp = http_post_json(url, {"agvId": AGV_ID, "target": target}, AGV_REQ_TIMEOUT_S, req_id=req_id)
            if resp.status_code == 200:
                return resp.json() if resp.content else {"ok": True}
            try: data = resp.json()
            except Exception: data = {"error": resp.text}
            msg = (data.get("message") or data.get("error") or "")
            if resp.status_code == 400 and "current location is already the target tag number" in msg:
                logging.warning(f"{req_id} [AGV] send-task noop: already at target ({target})")
                return {"ok": True, "noop": True, "target": target, "message": msg}
            raise requests.HTTPError(f"send-task HTTP {resp.status_code}: {data}")
        except Exception as e:
            last_err = e
            logging.warning(f"{req_id} [AGV] send-task attempt {i}/{AGV_REQ_RETRIES} failed: {e}")
            time.sleep(min(1.0 * i, 2.0))
    raise last_err

def agv_pick_one(agvs: list, agv_id: int) -> Optional[dict]:
    for a in agvs:
        if int(a.get("id", -1)) == agv_id:
            return a
    return None

def agv_is_busy(agv: dict) -> bool:
    if not agv: return True
    t = agv.get("task")
    working = bool(agv.get("working", False))
    if not t: return working
    status = str(t.get("status", "")).upper()
    
    # A task is NOT busy only if it's explicitly completed, failed, idle, or has no status.
    # We now consider 'WAITING' as a busy state, as the AGV is not yet ready for a new command.
    is_not_busy = status in ("COMPLETED", "FAILED", "CANCELLED", "IDLE", "NONE")
    
    return not is_not_busy

def agv_task_status(agv: dict) -> Tuple[str, Optional[str]]:
    t = (agv or {}).get("task")
    if not t: return ("NONE", None)
    return (str(t.get("status", "NONE")).upper(), str(t.get("taskNumber")) if "taskNumber" in t else None)

def make_command(head: str, *args: int) -> bytes:
    head = head.strip().upper()
    parts = [head] + [str(int(v)) for v in args]
    return ",".join(parts).encode("ascii") + CR

def cm_to_units(cm: float) -> int:
    return int(round(cm * 1000.0))

def classify_messages(lines: List[str]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for raw in lines:
        key = raw.strip()
        meta = MSG_CLASS.get(key)
        if meta is None: out.append({"code": key, "type": "info", "text": key})
        else: out.append({"code": key, "type": meta["type"], "text": meta["text"]})
    return out

def read_messages_until(ser: serial.Serial, overall_deadline_s: float, idle_gap_s: Optional[float], req_id: str = "") -> Optional[List[str]]:
    lines: List[str] = []
    buf = bytearray()
    start_time = time.monotonic()
    last_rx_time = start_time
    got_any_data = False
    while True:
        now = time.monotonic()
        if _SHUTDOWN_EVENT.is_set(): log.info(f"{req_id} [TRACK] read: Shutdown."); return None
        if (now - start_time) > overall_deadline_s:
            log.warning(f"{req_id} [TRACK] read: deadline {overall_deadline_s:.1f}s exceeded."); break
        if got_any_data and idle_gap_s is not None and (now - last_rx_time) > idle_gap_s:
            log.info(f"{req_id} [TRACK] read: idle gap {idle_gap_s:.2f}s reached."); break
        try:
            b = ser.read(1)
        except serial.SerialTimeoutException:
            if not got_any_data: log.debug(f"{req_id} [TRACK] read: initial no byte.")
            continue
        if b:
            got_any_data = True
            buf.extend(b)
            last_rx_time = now
            if b == CR:
                line = bytes(buf[:-1]).decode("ascii", errors="replace").strip()
                buf = bytearray()
                if line:
                    lines.append(line)
                    log.info(f"{req_id} [TRACK] <- {line}")
                    if line in TERMINAL_OK or line in ERROR_CODES:
                        log.info(f"{req_id} [TRACK] read: Terminal '{line}'."); break
    if not lines:
        log.warning(f"{req_id} [TRACK] No complete lines."); return None
    return lines

def send_and_receive_multi(ser: serial.Serial, cmd_bytes: bytes, overall_deadline_s: float, idle_gap_s: Optional[float], req_id: str = "") -> Optional[List[str]]:
    ser.reset_input_buffer(); ser.reset_output_buffer()
    log.info(f"{req_id} [TRACK] -> {cmd_bytes!r}")
    try:
        ser.write(cmd_bytes); ser.flush()
    except serial.SerialException as e:
        log.error(f"{req_id} [TRACK] Write failed: {e}"); return None
    if _SHUTDOWN_EVENT.is_set(): log.info(f"{req_id} [TRACK] Shutdown before delay."); return None
    time.sleep(INTER_CMD_DELAY)
    return read_messages_until(ser, overall_deadline_s=overall_deadline_s, idle_gap_s=idle_gap_s, req_id=req_id)

def track_move_worker(y: int, req_id: str, deadline: float, idle_gap: float):
    if not _TRACK_LOCK.acquire(blocking=False):
        log.warning(f"{req_id} [TRACK] start denied: busy."); return
    try:
        if _SHUTDOWN_EVENT.is_set(): log.info(f"{req_id} [TRACK] shutdown."); return
        try: cm = CLICK_Y_STOPS_CM[y - 1]
        except IndexError: log.error(f"{req_id} [TRACK] invalid y={y}."); return
        pos_units = cm_to_units(cm)
        cmd = make_command("ABS", pos_units, CLICK_VEL, CLICK_ACC_MS, CLICK_DEC_MS)
        log.info(f"{req_id} [TRACK] MOVE y={y} -> {cm} cm ({pos_units})")
        try: ser = serial_get_or_raise()
        except Exception as e: log.error(f"{req_id} [TRACK] serial unavailable: {e}"); return
        with SER_LOCK:
            if _SHUTDOWN_EVENT.is_set(): log.info(f"{req_id} [TRACK] shutdown before I/O."); return
            lines = send_and_receive_multi(ser, cmd, overall_deadline_s=deadline, idle_gap_s=idle_gap, req_id=req_id)
        if lines is None:
            if _SHUTDOWN_EVENT.is_set(): log.info(f"{req_id} [TRACK] terminated by shutdown.")
            else: log.error(f"{req_id} [TRACK] no reply within {deadline:.1f}s.")
            return
        parsed = classify_messages(lines)
        ok = all(p.get("type") != "error" for p in parsed)
        log.info(f"{req_id} [TRACK] parsed={parsed} ok={ok}")
        if not ok: log.error(f"{req_id} [TRACK] Track command failed: {parsed}")
    except Exception as e:
        log.exception(f"{req_id} [TRACK] worker error: {e}")
    finally:
        _TRACK_LOCK.release()

# MODIFIED: mjpeg_generator now uses the CAMERA instance
def mjpeg_generator(jpeg_quality=80):
    if CAMERA is None or not CAMERA.is_camera_initialized:
        log.warning("[CAM] MJPEG stream requested but camera is not available.")
        return
    try:
        import itertools
        for _ in itertools.count():
            if _SHUTDOWN_EVENT.is_set(): break
            ret, img = CAMERA.read()
            if not ret or img is None:
                time.sleep(0.02)
                continue

            q = max(50, min(95, int(jpeg_quality)))
            ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), q])
            if not ok: continue

            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n"
                   b"Cache-Control: no-cache\r\n"
                   b"Pragma: no-cache\r\n"
                   b"\r\n" + buf.tobytes() + b"\r\n")
    except GeneratorExit:
        log.info("[CAM] MJPEG client disconnected.")
    except Exception as e:
        log.warning(f"[CAM] generator error: {e}")


# ========= Routes =========
@app.route("/")
def index():
    return render_template("index.html")

@app.get("/status")
def status():
    open_flag = bool(SER and getattr(SER, "is_open", False))
    return jsonify({"track_busy": track_busy(), "serial_open": open_flag, "port": RS485_PORT})

@app.get("/agv/test")
def agv_test():
    base = request.args.get("base") or AGV_BASE_URL
    url = (base.rstrip("/") + "/YIDAGV/api/agv/data")
    rid_ = rid()
    try:
        log.info(f"{rid_} [AGV] TEST GET {url}")
        resp = requests.get(url, timeout=AGV_REQ_TIMEOUT_S)
        return jsonify({"ok": True, "status": resp.status_code, "body": resp.text[:1000], "url": url})
    except Exception as e:
        log.error(f"{rid_} [AGV] TEST error: {e}")
        return jsonify({"ok": False, "error": str(e), "url": url}), 502

@app.get("/agv/data")
def api_agv_data():
    rid_ = rid()
    try:
        data = agv_fetch_all(req_id=rid_)
        return jsonify(data)
    except requests.HTTPError as e:
        log.error(f"{rid_} [AGV] data HTTPError: {e}")
        return jsonify({"ok": False, "error": str(e)}), 502
    except Exception as e:
        log.error(f"{rid_} [AGV] data error: {e}")
        return jsonify({"ok": False, "error": f"agv data fetch failed: {e}"}), 502

@app.get("/agv/status-summary")
def api_agv_status_summary():
    rid_ = rid()
    is_track_busy = track_busy()
    is_agv_busy = False
    
    try:
        data = agv_fetch_all(req_id=rid_)
        agvs = data if isinstance(data, list) else data.get("data", [])
        agv = agv_pick_one(agvs, AGV_ID)

        if not agv:
            msg = f"AGV id={AGV_ID} not found"
            log.error(f"{rid_} [AGV] status-summary: {msg}")
            return jsonify({
                "ok": False, "system_busy": True, "details": { "track_busy": is_track_busy, "agv_busy": True, "message": msg },
                "error": msg, "agv_raw_status": {}
            }), 500

        is_agv_busy = agv_is_busy(agv)
        system_is_busy = is_agv_busy or is_track_busy
        status_text, task_number = agv_task_status(agv)

        agv_msg_part = f"AGV: {status_text}"
        if task_number: agv_msg_part += f" (Task: {task_number})"
        track_msg_part = "Track: MOVING" if is_track_busy else "Track: Idle"
        combined_message = f"{agv_msg_part} | {track_msg_part}"

        return jsonify({
            "ok": True, "system_busy": system_is_busy, "details": { "track_busy": is_track_busy, "agv_busy": is_agv_busy, "message": combined_message },
            "agv_raw_status": agv
        })
    except requests.exceptions.RequestException as e:
        log.error(f"{rid_} [AGV] status-summary: HTTP request failed: {e}")
        return jsonify({
            "ok": False, "system_busy": True, "details": { "track_busy": is_track_busy, "agv_busy": True, "message": "Failed to connect to AGV system." },
            "error": str(e),
        }), 502
    except Exception as e:
        log.error(f"{rid_} [AGV] status-summary: Unexpected error: {e}")
        return jsonify({
            "ok": False, "system_busy": True, "details": { "track_busy": is_track_busy, "agv_busy": True, "message": "Server error checking AGV status." },
            "error": str(e),
        }), 500

@app.post("/agv/send-task")
def api_agv_send_task():
    rid_ = rid()
    payload = request.get_json(silent=True) or {}
    target = str(payload.get("target", "")).strip()
    if not target: return jsonify({"ok": False, "error": "missing target"}), 400
    try:
        data = send_task_to_agv(target, req_id=rid_)
        return jsonify({"ok": True, **(data if isinstance(data, dict) else {})})
    except Exception as e:
        log.error(f"{rid_} [AGV] send-task error: {e}")
        return jsonify({"ok": False, "error": f"send-task failed: {e}"}), 502

@app.post("/agv/home")
def api_agv_home():
    rid_ = rid()
    try:
        data = send_task_to_agv("1001", req_id=rid_)
        return jsonify({"ok": True, **(data if isinstance(data, dict) else {})})
    except Exception as e:
        log.error(f"{rid_} [AGV] home error: {e}")
        return jsonify({"ok": False, "error": f"home failed: {e}"}), 502

@app.post("/click")
def click():
    rid_ = rid()
    if track_busy():
        log.warning(f"{rid_} [/click] Rejected: Track is busy.")
        return jsonify({"ok": False, "error": "Track is currently busy", "code": "TRACK_BUSY"}), 409
    try:
        agv_data = agv_fetch_all(req_id=rid_)
        agvs = agv_data if isinstance(agv_data, list) else agv_data.get("data", [])
        agv = agv_pick_one(agvs, AGV_ID)
        if agv and agv_is_busy(agv):
            st, tn = agv_task_status(agv)
            error_msg = f"AGV is currently busy (status={st}, task={tn})"
            log.warning(f"{rid_} [/click] Rejected: {error_msg}")
            return jsonify({"ok": False, "error": error_msg, "code": "AGV_BUSY"}), 409
    except Exception as e:
        log.error(f"{rid_} [AGV] Pre-click AGV status check failed: {e}")
        return jsonify({"ok": False, "error": f"Failed to get AGV status before command: {e}", "code": "AGV_CHECK_FAILED"}), 502

    payload = request.get_json(force=True) or {}
    x = int(payload.get("x", 0))
    y = int(payload.get("y", 0))
    log.info(f"{rid_} [/click] recv x={x}, y={y} from {request.remote_addr}")
    if not (1 <= y <= 10):
        return jsonify({"ok": False, "error": f"invalid y={y} (expected 1..10)"}), 400

    agv_result = {"ok": False, "skipped": False, "base": AGV_BASE_URL}
    try:
        # AGV_TARGETS maps "1".."6" to target IDs. "7" is also defined in AGV_TARGETS for the right side click,
        # but the patrol uses x=1..6.
        target_key = str(min(max(x, 1), 7)) # Original logic: ensures x is within mapped range, includes 7 for right face
        # For the patrol, we explicitly limit x to 1-6 in JS, so this will map correctly.
        target = AGV_TARGETS.get(target_key)
        if not target:
            msg = f"no target mapped for x={x} (mapped to {target_key})"
            log.error(f"{rid_} [AGV] {msg}")
            agv_result = {"ok": False, "error": msg, "base": AGV_BASE_URL}
        else:
            st_resp = send_task_to_agv(target, req_id=rid_)
            agv_result = {"ok": True, **(st_resp if isinstance(st_resp, dict) else {})}
    except Exception as e:
        log.error(f"{rid_} [AGV] send error: {e}")
        agv_result = {"ok": False, "error": str(e), "base": AGV_BASE_URL}
        return jsonify({
            "ok": False, "x": x, "y": y, "error": "AGV task dispatch failed, track movement cancelled.",
            "agv_result": agv_result, "track_job_started": False
        }), 502

    deadline = READ_DEADLINE_DEFAULT
    idle_gap = IDLE_GAP_DEFAULT
    if not track_busy():
        th = threading.Thread(target=track_move_worker, args=(y, rid(), deadline, idle_gap), daemon=True)
        th.start()
        track_started = True
    else:
        track_started = False

    return jsonify({
        "ok": bool(agv_result.get("ok")), "x": x, "y": y,
        "agv_result": agv_result, "track_job_started": track_started
    })

@app.get("/video.mjpg")
def video_mjpg():
    # MODIFIED: Ensure camera is initialized before streaming
    if CAMERA is None or not CAMERA.check_open():
        log.warning("[CAM] /video.mjpg requested but camera not open, attempting re-init for stream.")
        camera_init() # Try to init camera if not already open
        if CAMERA is None or not CAMERA.check_open():
             return jsonify({"ok": False, "error": "camera is not initialized or could not be opened"}), 503

    return Response(mjpeg_generator(80),
                    mimetype="multipart/x-mixed-replace; boundary=frame",
                    headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

# ---- NEW: Capture Endpoint ----
@app.post("/capture")
def capture_image():
    rid_ = rid()
    # log.info(f"{rid_} [/capture] Received capture request.")
    
    # MODIFIED: Get x and y from JSON body
    payload = request.get_json(silent=True) or {}
    x_pos = payload.get('x', 'unknown')
    y_pos = payload.get('y', 'unknown')
    # log.info(f"{rid_} [/capture] Received capture request. x={x_pos}, y={y_pos}")

    # log the position, datetime of the request. I want to analyze from log to know if the patrol is working for each position for a long time.
    log.info(f"{rid_} [/capture] Capture requested at position x={x_pos}, y={y_pos} at {time.strftime('%Y-%m-%d %H:%M:%S')}")

    return jsonify({"ok": True, "message": "Image captured successfully", "filename": "filename"})


    # if CAMERA is None or not CAMERA.is_camera_initialized:
    #     log.error(f"{rid_} [/capture] Aborted: Camera is not available.")
    #     return jsonify({"ok": False, "error": "Camera not available"}), 503

    # try:
    #     ret, img = CAMERA.read()
    #     if not ret or img is None:
    #         log.error(f"{rid_} [/capture] Failed to read frame from camera.")
    #         return jsonify({"ok": False, "error": "Failed to read frame from camera"}), 500

    #     capture_dir = "captured"
    #     os.makedirs(capture_dir, exist_ok=True)
        
    #     timestamp = time.strftime("%Y%m%d_%H%M%S")
        
    #     # MODIFIED: Filename format to include x and y
    #     filename = f"captured_position{int(x_pos):02}{int(y_pos):02}_{timestamp}.jpg"
    #     filepath = os.path.join(capture_dir, filename)
        
    #     # Save the image
    #     cv2.imwrite(filepath, img)
    #     log.info(f"{rid_} [/capture] Image successfully saved to {filepath}")
        
    #     return jsonify({"ok": True, "message": "Image captured successfully", "filename": filename})

    # except Exception as e:
    #     log.exception(f"{rid_} [/capture] An error occurred during capture: {e}")
    #     return jsonify({"ok": False, "error": f"An unexpected error occurred: {e}"}), 500

# ========= Startup / Shutdown =========
def _cleanup_all():
    camera_close()
    serial_close()

@atexit.register
def _atexit_cleanup():
    _cleanup_all()

def _sig_handler(signum, frame):
    log.info(f"[SIGNAL] received {signum}, initiating graceful shutdown...")
    _SHUTDOWN_EVENT.set()
    _cleanup_all()
    raise KeyboardInterrupt()

for _sig in (signal.SIGINT, signal.SIGTERM):
    try:
        signal.signal(_sig, _sig_handler)
    except Exception as e:
        log.warning(f"[SIGNAL] Could not register handler for {_sig}: {e}")

if __name__ == "__main__":
    try:
        serial_init()
    except Exception as e:
        log.error(f"[BOOT] Initial serial port setup failed: {e}")
    
    # MODIFIED: Initialize camera on startup
    camera_init()

    log.info("[BOOT] Starting Flask application...")
    app.run(host="0.0.0.0", port=8000, debug=False, use_reloader=False, threaded=True)