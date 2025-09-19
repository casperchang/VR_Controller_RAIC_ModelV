import os
import sys
import time
import json
import atexit
import signal
import threading
from typing import Optional, List, Dict, Set, Tuple

from flask import Flask, render_template, request, jsonify
import serial
import requests
import logging

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
    # simple request id like [HH:MM:SS.mmm]
    return f"[{time.strftime('%H:%M:%S')}.{int((time.time()%1)*1000):03d}]"

# ========= Serial & timing config =========
RS485_PORT = os.getenv("RS485_PORT", "/dev/ttyUSB0")
RS485_BAUD = int(os.getenv("RS485_BAUD", "115200"))
RS485_BYTESIZE = 8        # 8O1
RS485_PARITY = "O"
RS485_STOPBITS = 1
RS485_TIMEOUT = float(os.getenv("RS485_TIMEOUT", "0.5"))     # per-byte timeout (s), reduced for quicker error detection
INTER_CMD_DELAY = float(os.getenv("INTER_CMD_DELAY", "0.100")) # Increased to 100ms for RS-485 turnaround
READ_DEADLINE_DEFAULT = float(os.getenv("RS485_READ_DEADLINE", "60.0"))
IDLE_GAP_DEFAULT = float(os.getenv("RS485_IDLE_GAP", "1.0"))

# ========= Track motion params =========
CLICK_VEL = 5000
CLICK_ACC_MS = 100
CLICK_DEC_MS = 100

# ========= Y→position (cm) table =========
# CLICK_Y_STOPS_CM = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]  # index: y-1
# [2.1, 20.2, 38.3, 56.4, 74.5, 92.6, 110.7, 128.8, 146.9, 165.0]
CLICK_Y_STOPS_CM = [165 - (10 - i)*18.1 for i in range(1, 11)]


# ========= AGV proxy config =========
# per your note: base is 192.168.0.175:8080 (change via env if needed)
AGV_BASE_URL = os.getenv("AGV_BASE_URL", "http://192.168.0.172:8080")
AGV_ID = int(os.getenv("AGV_ID", "1"))

# Map X to AGV targets (strings). Adjust as needed.
AGV_TARGETS = json.loads(os.getenv("AGV_TARGETS_JSON", json.dumps({
    "1": "1002", "2": "1003", "3": "1004", "4": "1005", "5": "1006", "6": "1007", "7": "1009"
})))

# HTTP timeouts & retries
AGV_REQ_TIMEOUT_S = float(os.getenv("AGV_REQ_TIMEOUT_S", "3.0"))  # per request
AGV_REQ_RETRIES = int(os.getenv("AGV_REQ_RETRIES", "3"))          # send-task retries

CR = b"\r"
app = Flask(__name__)

log.info(f"[BOOT] AGV_BASE_URL = {AGV_BASE_URL}")

# ========= Persistent serial state =========
SER: Optional[serial.Serial] = None
SER_LOCK = threading.Lock()     # guards read/write to SER
_TRACK_LOCK = threading.Lock()  # ensures only one track job at a time

# Event to signal worker threads to stop
_SHUTDOWN_EVENT = threading.Event()

def track_busy() -> bool:
    return _TRACK_LOCK.locked()

def open_serial_port() -> serial.Serial:
    byte_size_map = {5: serial.FIVEBITS, 6: serial.SIXBITS, 7: serial.SEVENBITS, 8: serial.EIGHTBITS}
    parity_map = {'N': serial.PARITY_NONE, 'E': serial.PARITY_EVEN, 'O': serial.PARITY_ODD,
                  'M': serial.PARITY_MARK, 'S': serial.PARITY_SPACE}
    stop_map = {1: serial.STOPBITS_ONE, 2: serial.STOPBITS_TWO}
    ser = serial.Serial(
        port=RS485_PORT,
        baudrate=RS485_BAUD,
        bytesize=byte_size_map.get(RS485_BYTESIZE, serial.EIGHTBITS),
        parity=parity_map.get(RS485_PARITY.upper(), serial.PARITY_ODD),
        stopbits=stop_map.get(RS485_STOPBITS, serial.STOPBITS_ONE),
        timeout=RS485_TIMEOUT,
        write_timeout=RS485_TIMEOUT,
        rtscts=False, dsrdtr=False, xonxoff=False # Typically false for RS-485
    )
    return ser

def serial_init(retries: int = 3, delay_s: float = 1.0):
    """Open the RS-485 port, with retries, and keep it for the whole app lifetime."""
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
            SER = None # Ensure SER is None if opening fails
            time.sleep(delay_s)
        except Exception as e:
            log.error(f"[BOOT] Unexpected error during serial init: {e}")
            SER = None
            break
    log.error(f"[BOOT] Failed to open serial port after {retries} attempts.")
    return None

def serial_close():
    """Close the port at shutdown."""
    global SER
    # Signal workers to stop gracefully before trying to close serial
    _SHUTDOWN_EVENT.set() 
    log.info("[SHUTDOWN] Signaled worker threads to stop.")

    with SER_LOCK: # Acquire lock before accessing SER to prevent race conditions during shutdown
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
    """Return open serial or raise if not available. Attempts re-init if closed."""
    global SER
    if SER and getattr(SER, "is_open", False):
        return SER
    
    log.warning("[SERIAL] Serial port not open, attempting re-initialization.")
    SER = serial_init(retries=1, delay_s=0.5) # Try to re-init once
    if SER and getattr(SER, "is_open", False):
        return SER
    raise serial.SerialException("Serial port is not available or could not be re-opened.")

# ========= TRACK helpers =========
def make_command(head: str, *args: int) -> bytes:
    head = head.strip().upper()
    parts = [head] + [str(int(v)) for v in args]
    return ",".join(parts).encode("ascii") + CR

def read_messages_until(
    ser: serial.Serial,
    overall_deadline_s: float,
    idle_gap_s: Optional[float] = None,
    req_id: str = ""
) -> Optional[List[str]]:
    lines: List[str] = []
    buf = bytearray()
    start_time = time.monotonic()
    last_rx_time = start_time
    got_any_data = False

    while True:
        now = time.monotonic()

        # Check for shutdown signal
        if _SHUTDOWN_EVENT.is_set():
            log.info(f"{req_id} [TRACK] read: Shutdown event received, stopping read.")
            return None

        # Check overall deadline
        if (now - start_time) > overall_deadline_s:
            log.warning(f"{req_id} [TRACK] read: overall deadline ({overall_deadline_s:.1f}s) exceeded.")
            break

        # Check idle gap only if we've received some data
        if got_any_data and idle_gap_s is not None and (now - last_rx_time) > idle_gap_s:
            log.info(f"{req_id} [TRACK] read: idle gap ({idle_gap_s:.2f}s) reached (stopping read).")
            break

        try:
            # Use a smaller timeout for serial.read() if a shutdown event is possible
            # This allows the loop to frequently check _SHUTDOWN_EVENT
            read_timeout = min(RS485_TIMEOUT, 0.1) # Check every 100ms
            b = ser.read(1) # This uses the per-byte timeout (RS485_TIMEOUT)
        except serial.SerialTimeoutException:
            # This can happen if no byte arrives within read_timeout
            # If we haven't received anything yet, it's a "no data" issue
            if not got_any_data:
                log.debug(f"{req_id} [TRACK] read: No initial byte within {read_timeout:.1f}s.")
            continue # Continue checking deadlines

        if b:
            got_any_data = True
            buf.extend(b)
            last_rx_time = now # Update last received time
            
            if b == CR:
                line = bytes(buf[:-1]).decode("ascii", errors="replace").strip()
                buf = bytearray() # Reset buffer for next line
                if line:
                    lines.append(line)
                    log.info(f"{req_id} [TRACK] <- {line}")
                    if line in TERMINAL_OK or line in ERROR_CODES:
                        log.info(f"{req_id} [TRACK] read: Terminal message '{line}' received.")
                        break
        # If no byte received (b is empty), the loop will continue and check deadlines.
        # This occurs if the per-byte timeout (RS485_TIMEOUT) expires.
    
    if not lines:
        log.warning(f"{req_id} [TRACK] No complete lines received within deadline or before idle gap.")
        return None
    return lines


def send_and_receive_multi(
    ser: serial.Serial,
    cmd_bytes: bytes,
    overall_deadline_s: float,
    idle_gap_s: Optional[float],
    req_id: str = ""
) -> Optional[List[str]]:
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    log.info(f"{req_id} [TRACK] -> {cmd_bytes!r}")
    
    try:
        ser.write(cmd_bytes)
        ser.flush()
    except serial.SerialException as e:
        log.error(f"{req_id} [TRACK] Write failed: {e}")
        return None
    
    # Check shutdown event before sleeping
    if _SHUTDOWN_EVENT.is_set():
        log.info(f"{req_id} [TRACK] Shutdown event received before INTER_CMD_DELAY, stopping.")
        return None

    time.sleep(INTER_CMD_DELAY) # Crucial delay for RS-485 turnaround
    
    return read_messages_until(ser, overall_deadline_s=overall_deadline_s, idle_gap_s=idle_gap_s, req_id=req_id)

def cm_to_units(cm: float) -> int:
    return int(round(cm * 1000.0))  # cm → 0.01mm

def classify_messages(lines: List[str]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for raw in lines:
        key = raw.strip()
        meta = MSG_CLASS.get(key)
        if meta is None:
            out.append({"code": key, "type": "info", "text": key})
        else:
            out.append({"code": key, "type": meta["type"], "text": meta["text"]})
    return out

# ========= Message classes =========
MSG_CLASS: Dict[str, Dict[str, str]] = {
    "DONE":         {"type": "success", "text": "Movement completed."},
    "HOME_OK":      {"type": "success", "text": "Homing completed successfully."},
    "RESTART_OK":   {"type": "success", "text": "Controller restarted successfully."},
    "ERROR_HOME":   {"type": "error",   "text": "Homing failed."},
    "ERROR_REPEAT": {"type": "error",   "text": "Repeated/duplicate command ignored."},
    "ERROR_DRIVER": {"type": "error",   "text": "Motor driver fault."},
    "ERROR_INPOS":  {"type": "error",   "text": "Position not reached / in-position error."},
    "NOT_HOME_OK":  {"type": "error",   "text": "Not at HOME (reported as OK)."},
    "OK":           {"type": "success", "text": "Command acknowledged/OK."} # Added for general acknowledgements
}
ERROR_CODES: Set[str] = {k for k, v in MSG_CLASS.items() if v["type"] == "error"}
# AFTER — remove "OK", keep it only as an informational ack
TERMINAL_OK: Set[str] = {"DONE", "HOME_OK", "RESTART_OK"}

# ========= AGV helpers =========
def agv_url(path: str) -> str:
    base = AGV_BASE_URL.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}"

def http_get(url: str, timeout: float, req_id: str = "") -> requests.Response:
    log.info(f"{req_id} [AGV] GET {url}")
    resp = requests.get(url, timeout=timeout)
    log.info(f"{req_id} [AGV] <- HTTP {resp.status_code} {resp.text[:300]}")
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
            try:
                data = resp.json()
            except Exception:
                data = {"error": resp.text}
            # 同站點 → 視為成功（no-op），避免前端當成錯誤
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
    if not agv:
        return True  # 沒資料就保守當成忙

    t = agv.get("task")
    working = bool(agv.get("working", False))

    # 如果沒有任務，就單看 working
    if not t:
        return working

    status = str(t.get("status", "")).upper()

    # 任務完成/失敗/取消/空閒 → 都算不忙
    if status in ("COMPLETED", "FAILED", "CANCELLED", "NONE", "IDLE", "WAITING"):
        return False

    # 其他情況（RUNNING, STARTING, 等等）都算忙
    return True


def agv_task_status(agv: dict) -> Tuple[str, Optional[str]]:
    t = (agv or {}).get("task")
    if not t:
        return ("NONE", None)
    return (str(t.get("status", "NONE")).upper(), str(t.get("taskNumber")) if "taskNumber" in t else None)

# ========= Track worker (background thread) =========
def track_move_worker(y: int, req_id: str, deadline: float, idle_gap: float):
    # only one track job at a time
    if not _TRACK_LOCK.acquire(blocking=False): # Non-blocking acquire
        log.warning(f"{req_id} [TRACK] Attempted to start move for y={y}, but track is already busy.")
        return # Track is busy, exit worker early

    try:
        # Check for shutdown signal immediately
        if _SHUTDOWN_EVENT.is_set():
            log.info(f"{req_id} [TRACK] Shutdown event received, worker not starting.")
            return

        # map Y -> position
        try:
            cm = CLICK_Y_STOPS_CM[y - 1]
        except IndexError:
            log.error(f"{req_id} [TRACK] invalid Y={y} (no mapping)")
            return
        pos_units = cm_to_units(cm)
        cmd = make_command("ABS", pos_units, CLICK_VEL, CLICK_ACC_MS, CLICK_DEC_MS)
        log.info(f"{req_id} [TRACK] MOVE y={y} -> {cm} cm (pos={pos_units})")

        # use the shared serial and guard I/O
        try:
            ser = serial_get_or_raise()
        except serial.SerialException as e:
            log.error(f"{req_id} [TRACK] serial unavailable: {e}")
            return
        except Exception as e:
            log.error(f"{req_id} [TRACK] unexpected error getting serial: {e}")
            return

        with SER_LOCK:
            # Check shutdown event again before serial I/O
            if _SHUTDOWN_EVENT.is_set():
                log.info(f"{req_id} [TRACK] Shutdown event received before serial command, worker aborting.")
                return

            lines = send_and_receive_multi(ser, cmd,
                                           overall_deadline_s=deadline,
                                           idle_gap_s=idle_gap,
                                           req_id=req_id)
        if lines is None:
            # Check if shutdown caused the early exit
            if _SHUTDOWN_EVENT.is_set():
                log.info(f"{req_id} [TRACK] Worker terminated due to shutdown event during serial communication.")
            else:
                log.error(f"{req_id} [TRACK] no reply within {deadline:.1f}s (or before idle gap).")
            return
        
        parsed = classify_messages(lines)
        ok = all(p.get("type") != "error" for p in parsed)
        log.info(f"{req_id} [TRACK] parsed={parsed} ok={ok}")

        if not ok:
            log.error(f"{req_id} [TRACK] Track command failed, received error messages: {parsed}")

    except Exception as e:
        log.exception(f"{req_id} [TRACK] An unhandled error occurred in track_move_worker: {e}")
    finally:
        _TRACK_LOCK.release() # Ensure lock is always released

# ========= Routes =========
@app.route("/")
def index():
    return render_template("index.html")

@app.get("/status")
def status():
    open_flag = bool(SER and getattr(SER, "is_open", False))
    return jsonify({"track_busy": track_busy(), "serial_open": open_flag, "port": RS485_PORT})

# Optional: quick connectivity test
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

# Optional proxy passthroughs
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

# Add this new route to your app.py, typically after your existing /agv/data route.
@app.get("/agv/status-summary")
def api_agv_status_summary():
    rid_ = rid()
    busy_track = track_busy()   # default
    busy_agv = False
    busy_combined = busy_track

    try:
        data = agv_fetch_all(req_id=rid_)
        agvs = data if isinstance(data, list) else data.get("data", [])
        agv = agv_pick_one(agvs, AGV_ID)

        if not agv:
            msg = f"AGV id={AGV_ID} not found"
            log.error(f"{rid_} [AGV] status-summary: {msg}")
            return jsonify({
                "ok": False,
                "agv_busy": True,         # be conservative on errors
                "track_busy": busy_track,
                "error": msg,
                "agv_status_message": msg,
                "agv_raw_status": {}
            }), 500

        # ★ compute both, then OR
        busy_agv = agv_is_busy(agv)
        busy_combined = busy_agv or busy_track

        status_text, task_number = agv_task_status(agv)
        message = f"Status: {status_text}"
        if task_number:
            message += f", Task: {task_number}"
        if busy_track:
            message += " | Track: MOVING"

        return jsonify({
            "ok": True,
            "agv_busy": busy_combined,     # ← front-end uses this
            "track_busy": busy_track,
            "agv_busy_raw": busy_agv,
            "agv_status_message": message,
            "agv_raw_status": agv
        })

    except requests.exceptions.RequestException as e:
        log.error(f"{rid_} [AGV] status-summary: HTTP request failed: {e}")
        return jsonify({
            "ok": False,
            "agv_busy": True,
            "track_busy": busy_track,
            "error": str(e),
            "agv_status_message": "Failed to connect to AGV system."
        }), 502
    except Exception as e:
        log.error(f"{rid_} [AGV] status-summary: Unexpected error: {e}")
        return jsonify({
            "ok": False,
            "agv_busy": True,
            "track_busy": busy_track,
            "error": str(e),
            "agv_status_message": "Server error checking AGV status."
        }), 500

@app.post("/agv/send-task")
def api_agv_send_task():
    rid_ = rid()
    payload = request.get_json(silent=True) or {}
    target = str(payload.get("target", "")).strip()
    if not target:
        return jsonify({"ok": False, "error": "missing target"}), 400
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


# ---- Main /click: AGV (sync) first, then Track (async worker) ----
@app.post("/click")
def click():
    """
    From 3D UI: {x:1..7, y:1..10}
    Flow:
      1) AGV: check status -> send-task (wait HTTP reply) -> stop (no polling)
      2) Track: if not busy, start background thread to perform ABS move
      3) Return immediately with agv_result + track_job_started/busy
    """
    rid_ = rid()
    payload = request.get_json(force=True) or {}
    x = int(payload.get("x", 0))
    y = int(payload.get("y", 0))
    log.info(f"{rid_} [/click] recv x={x}, y={y} from {request.remote_addr}")

    if not (1 <= y <= 10):
        return jsonify({"ok": False, "error": f"invalid y={y} (expected 1..10)"}), 400

    # ---- (1) AGV: check & send once (sync) ----
    agv_result = {"ok": False, "skipped": False, "base": AGV_BASE_URL}
    try:
        data = agv_fetch_all(req_id=rid_)
        agvs = data if isinstance(data, list) else data.get("data", [])
        agv = agv_pick_one(agvs, AGV_ID)
        if not agv:
            msg = f"AGV id={AGV_ID} not found"
            log.error(f"{rid_} [AGV] {msg}")
            agv_result = {"ok": False, "error": msg, "base": AGV_BASE_URL}
        else:
            busy_now = agv_is_busy(agv)
            st0, tn0 = agv_task_status(agv)
            log.info(f"{rid_} [AGV] initial status={st0} taskNumber={tn0} working={agv.get('working')} busy={busy_now}")
            if busy_now:
                agv_result = {"ok": False, "busy": True, "error": f"AGV {AGV_ID} busy (status={st0}, taskNumber={tn0})", "base": AGV_BASE_URL}
            else:
                target_key = str(min(max(x, 1), 7)) # Ensure x is within 1-7 range for target mapping
                target = AGV_TARGETS.get(target_key)
                if not target:
                    msg = f"no target mapped for x={x} (mapped to {target_key})"
                    log.error(f"{rid_} [AGV] {msg}")
                    agv_result = {"ok": False, "error": msg, "base": AGV_BASE_URL}
                else:
                    try:
                        st_resp = send_task_to_agv(target, req_id=rid_)
                        task_number = st_resp.get("taskNumber") if isinstance(st_resp, dict) else None
                        agv_result = {"ok": True, "target": target, "taskNumber": task_number, "base": AGV_BASE_URL}
                        log.info(f"{rid_} [AGV] send-task OK target={target} taskNumber={task_number}")
                    except Exception as e:
                        log.error(f"{rid_} [AGV] send-task failed: {e}")
                        agv_result = {"ok": False, "error": f"send-task failed: {e}", "base": AGV_BASE_URL}
    except Exception as e:
        log.error(f"{rid_} [AGV] fetch data failed: {e}")
        agv_result = {"ok": False, "error": f"fetch agv data failed: {e}", "base": AGV_BASE_URL}

    # ---- (2) Track: start async worker if not busy ----
    track_started = False
    if _SHUTDOWN_EVENT.is_set(): # Don't start new workers if shutting down
        log.info(f"{rid_} [TRACK] application is shutting down, skipping new move.")
    elif track_busy():
        log.info(f"{rid_} [TRACK] busy: skip starting new move")
    else:
        deadline = float(payload.get("read_deadline_s", READ_DEADLINE_DEFAULT))
        idle_gap = float(payload.get("idle_gap_s", IDLE_GAP_DEFAULT))
        t = threading.Thread(target=track_move_worker, args=(y, rid_, deadline, idle_gap), daemon=True)
        t.start()
        track_started = True
        log.info(f"{rid_} [TRACK] worker started for y={y}")

    # ---- (3) Return immediately ----
    return jsonify({
        "ok": agv_result.get("ok", False) and track_started, # 'ok' is true if both AGV task sent AND track job started
        "x": x, "y": y,
        "agv_result": agv_result,
        "track_job_started": track_started,
        "track_busy": track_busy(),
        "serial_open": bool(SER and getattr(SER, "is_open", False))
    })

# ========= Startup / Shutdown hooks =========
atexit.register(serial_close)

def _sig_handler(signum, frame):
    log.info(f"[SIGNAL] received {signum}, initiating graceful shutdown...")
    # Signal all worker threads to stop
    _SHUTDOWN_EVENT.set()
    # Close serial port (also called by atexit)
    serial_close()
    
    # Give a short moment for cleanup, especially if there are other threads.
    # In a real production environment with Gunicorn, Gunicorn would manage worker processes.
    time.sleep(1) 
    
    # Use os._exit() for a forceful exit to ensure termination,
    # as sys.exit() can be caught by Flask's development server.
    log.info(f"[SIGNAL] Forcefully exiting process.")
    os._exit(0)

for _sig in (signal.SIGINT, signal.SIGTERM):
    try:
        signal.signal(_sig, _sig_handler)
    except Exception as e:
        # in some hosted envs signal handling is restricted
        log.warning(f"[SIGNAL] Could not register signal handler for {_sig}: {e}")
        pass

# ========= App entry =========
if __name__ == "__main__":
    try:
        # Initialize serial port when the script starts
        serial_init() 
    except Exception as e:
        log.error(f"[BOOT] Initial serial port setup failed: {e}")
        # Decide here if you want to exit or let the app run without serial
        # For now, we let it run, and serial_get_or_raise will attempt re-init
    
    log.info("[BOOT] Starting Flask application...")
    # Make sure use_reloader is False to avoid multiple processes and cleaner shutdown
    app.run(host="0.0.0.0", port=8000, debug=True, use_reloader=False) 