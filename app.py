# app.py — Flask backend for the 3D (Three.js) front-end clicks → RS-485 ABS move
import os
import time
import threading
from typing import Optional, List, Dict, Set, Tuple

from flask import Flask, render_template, request, jsonify
import serial

# ========= Serial & timing config (沿用你的控制專案，可用環境變數覆寫) =========
RS485_PORT = os.getenv("RS485_PORT", "/dev/ttyUSB0")
RS485_BAUD = int(os.getenv("RS485_BAUD", "115200"))
RS485_BYTESIZE = 8        # 8O1
RS485_PARITY = "O"
RS485_STOPBITS = 1
RS485_TIMEOUT = float(os.getenv("RS485_TIMEOUT", "0.8"))     # per-byte timeout (s)
INTER_CMD_DELAY = float(os.getenv("INTER_CMD_DELAY", "0.03"))
READ_DEADLINE_DEFAULT = float(os.getenv("RS485_READ_DEADLINE", "60.0"))
IDLE_GAP_DEFAULT = float(os.getenv("RS485_IDLE_GAP", "1.0"))

# ========= 固定的運動參數（依你的需求）=========
# 點前端方塊後，送絕對定位 ABS：速度=5000，加/減速=100/100 (單位與你的設備一致；你原檔就是 5000)
CLICK_VEL = 5000
CLICK_ACC_MS = 100
CLICK_DEC_MS = 100

# ========= Y→位置的可配置表 =========
# 先用 10..100 cm；之後可自由調整，程式會自動換算成 0.01mm 單位
CLICK_Y_STOPS_CM = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]  # index: y-1

CR = b"\r"
app = Flask(__name__)

# ========= "moving" 互斥鎖：阻擋重複點擊 =========
_MOVING_LOCK = threading.Lock()

def is_moving() -> bool:
    return _MOVING_LOCK.locked()

# ========= Helpers（沿用你的控制專案的作法）=========
def open_serial() -> serial.Serial:
    byte_size_map = {5: serial.FIVEBITS, 6: serial.SIXBITS, 7: serial.SEVENBITS, 8: serial.EIGHTBITS}
    parity_map = {'N': serial.PARITY_NONE, 'E': serial.PARITY_EVEN, 'O': serial.PARITY_ODD,
                  'M': serial.PARITY_MARK, 'S': serial.PARITY_SPACE}
    stop_map = {1: serial.STOPBITS_ONE, 2: serial.STOPBITS_TWO}
    return serial.Serial(
        port=RS485_PORT,
        baudrate=RS485_BAUD,
        bytesize=byte_size_map.get(RS485_BYTESIZE, serial.EIGHTBITS),
        parity=parity_map.get(RS485_PARITY.upper(), serial.PARITY_ODD),
        stopbits=stop_map.get(RS485_STOPBITS, serial.STOPBITS_ONE),
        timeout=RS485_TIMEOUT,
        write_timeout=RS485_TIMEOUT,
        rtscts=False, dsrdtr=False, xonxoff=False
    )

def make_command(head: str, *args: int) -> bytes:
    head = head.strip().upper()
    parts = [head] + [str(int(v)) for v in args]
    return ",".join(parts).encode("ascii") + CR

def read_messages_until(
    ser: serial.Serial,
    overall_deadline_s: float,
    idle_gap_s: Optional[float] = None,
) -> Optional[List[str]]:
    lines: List[str] = []
    buf = bytearray()
    start = time.monotonic()
    last_rx = start
    got_any = False

    while True:
        b = ser.read(1)
        now = time.monotonic()
        if b:
            got_any = True
            buf.extend(b)
            last_rx = now
            if b == CR:
                line = bytes(buf[:-1]).decode("ascii", errors="replace").strip()
                buf = bytearray()
                if line:
                    lines.append(line)
                    if line in TERMINAL_OK or line in ERROR_CODES:
                        break

        if (now - start) > overall_deadline_s:
            break
        if idle_gap_s is not None and got_any and (now - last_rx) > idle_gap_s:
            break

    if not lines:
        return None
    return lines

def send_and_receive_multi(
    ser: serial.Serial,
    cmd_bytes: bytes,
    overall_deadline_s: float,
    idle_gap_s: Optional[float],
) -> Optional[List[str]]:
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    ser.write(cmd_bytes)
    ser.flush()
    time.sleep(INTER_CMD_DELAY)
    return read_messages_until(ser, overall_deadline_s=overall_deadline_s, idle_gap_s=idle_gap_s)

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

def cm_to_units(cm: float) -> int:
    """cm → 0.01mm units. 1 cm = 10 mm = 1000 *0.01mm"""
    return int(round(cm * 1000.0))

# ========= 訊息分類碼（沿用你的控制專案）=========
MSG_CLASS: Dict[str, Dict[str, str]] = {
    "DONE":         {"type": "success", "text": "Movement completed."},
    "HOME_OK":      {"type": "success", "text": "Homing completed successfully."},
    "RESTART_OK":   {"type": "success", "text": "Controller restarted successfully."},
    "ERROR_HOME":   {"type": "error",   "text": "Homing failed."},
    "ERROR_REPEAT": {"type": "error",   "text": "Repeated/duplicate command ignored."},
    "ERROR_DRIVER": {"type": "error",   "text": "Motor driver fault."},
    "ERROR_INPOS":  {"type": "error",   "text": "Position not reached / in-position error."},
    "NOT_HOME_OK":  {"type": "error",   "text": "Not at HOME (reported as OK)."},
}
ERROR_CODES: Set[str] = {k for k, v in MSG_CLASS.items() if v["type"] == "error"}
TERMINAL_OK: Set[str] = {"DONE", "HOME_OK", "RESTART_OK"}


@app.route("/")
def index():
    return render_template("index.html")

@app.get("/status")
def status():
    """簡單查詢當前是否有移動中的工作。"""
    return jsonify({"busy": is_moving()})

# ========= /click：從前端接 {x,y}，依 y 查表送 ABS =========
@app.post("/click")
def click():
    """
    接收前端 3D 牆點擊：{x:1..7, y:1..10}
      - 若正在移動，回 busy 訊息
      - y 對應 CLICK_Y_STOPS_CM[y-1]（單位：cm）
      - 轉成 0.01mm 單位後送 ABS, pos_units, CLICK_VEL, CLICK_ACC_MS, CLICK_DEC_MS
    """
    payload = request.get_json(force=True) or {}
    x = int(payload.get("x", 0))
    y = int(payload.get("y", 0))

    if not (1 <= y <= 10):
        return jsonify({"ok": False, "error": f"invalid y={y} (expected 1..10)"}), 400

    # busy guard — non-blocking acquire
    if not _MOVING_LOCK.acquire(blocking=False):
        return jsonify({
            "ok": False,
            "busy": True,
            "error": "track is moving, wait to complete then click again."
        }), 409

    try:
        try:
            cm = CLICK_Y_STOPS_CM[y - 1]
        except IndexError:
            return jsonify({"ok": False, "error": f"y={y} not mapped"}), 400

        pos_units = cm_to_units(cm)  # → 0.01mm units
        deadline = float(payload.get("read_deadline_s", READ_DEADLINE_DEFAULT))
        idle_gap = float(payload.get("idle_gap_s", IDLE_GAP_DEFAULT))

        # 開啟序列埠並送 ABS
        try:
            ser = open_serial()
        except serial.SerialException as e:
            return jsonify({"ok": False, "error": f"serial open failed: {e}"}), 500

        try:
            cmd = make_command("ABS", pos_units, CLICK_VEL, CLICK_ACC_MS, CLICK_DEC_MS)
            print(f"[CLICK] x={x}, y={y} → cm={cm} → pos_units={pos_units} | cmd={cmd!r}")
            lines = send_and_receive_multi(ser, cmd, overall_deadline_s=deadline, idle_gap_s=idle_gap)
            if lines is None:
                return jsonify({
                    "ok": False,
                    "cmd": cmd.decode('ascii').rstrip(),
                    "error": f"no reply within {deadline:.1f}s"
                }), 504

            parsed = classify_messages(lines)
            overall_ok = all(p.get("type") != "error" for p in parsed)
            return jsonify({
                "ok": overall_ok,
                "cmd": cmd.decode('ascii').rstrip(),
                "x": x, "y": y, "cm": cm, "pos_units": pos_units,
                "replies": lines, "parsed": parsed,
                "deadline_used_s": deadline, "idle_gap_used_s": idle_gap
            })
        finally:
            ser.close()
    finally:
        # 確保任何路徑都會釋放 busy
        _MOVING_LOCK.release()

#（如需：也可保留你原本 /cmd/abs, /cmd/inc, /cmd/home, /cmd/pos, /cmd/restart 等端點在同一支後端）

if __name__ == "__main__":
    # 你先前 3D 服務跑 8000，就用 8000；若已被前端佔用可改其他埠。
    app.run(host="0.0.0.0", port=8000, debug=True)
