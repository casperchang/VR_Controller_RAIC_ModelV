from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.post("/click")
def click():
    data = request.get_json(force=True) or {}
    x = data.get("x")
    y = data.get("y")
    # ⚙️ 這裡之後接你的硬體控制程式
    print(f"[CLICK] X={x}, Y={y}  (from {request.remote_addr})")
    return jsonify(ok=True, x=x, y=y)

if __name__ == "__main__":
    # 開發階段方便測試，用 0.0.0.0 對外開放
    app.run(host="0.0.0.0", port=8000, debug=True)
