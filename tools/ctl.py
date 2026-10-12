"""브리지에 명령을 보내고 결과를 기다리는 클라이언트 (일반 권한에서 실행).

    python -m tools.ctl shot
    python -m tools.ctl click 1870 44 [--wait 1.5]
    python -m tools.ctl key esc
    python -m tools.ctl drag 1000 500 400 500
    python -m tools.ctl wheel 960 540 -3
"""
import argparse
import json
import os
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IN_DIR = os.path.join(ROOT, "bridge", "in")
OUT_DIR = os.path.join(ROOT, "bridge", "out")


def send(cmd, timeout=30):
    if not os.path.exists(os.path.join(OUT_DIR, "_status.json")):
        raise SystemExit("브리지가 실행 중이 아닙니다 (start_bridge.bat)")
    cmd["id"] = time.strftime("%H%M%S_") + uuid.uuid4().hex[:6]
    os.makedirs(IN_DIR, exist_ok=True)
    tmp = os.path.join(IN_DIR, cmd["id"] + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cmd, f)
    os.replace(tmp, tmp[:-4] + ".json")
    out = os.path.join(OUT_DIR, cmd["id"] + ".json")
    end = time.time() + timeout
    while time.time() < end:
        if os.path.exists(out):
            with open(out, encoding="utf-8") as f:
                return json.load(f)
        time.sleep(0.1)
    raise SystemExit("응답 없음 (브리지가 멈췄거나 종료됨)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["shot", "click", "key", "drag", "wheel", "focus", "run", "kill", "togglewindow"])
    p.add_argument("args", nargs="*")
    p.add_argument("--wait", type=float, default=1.0)
    a = p.parse_args()
    v = a.args
    if a.action == "shot":
        cmd = {"action": "screenshot"}
    elif a.action == "click":
        cmd = {"action": "click", "x": int(v[0]), "y": int(v[1])}
    elif a.action == "key":
        cmd = {"action": "key", "key": v[0]}
    elif a.action == "drag":
        cmd = {"action": "drag", "x": int(v[0]), "y": int(v[1]), "x2": int(v[2]), "y2": int(v[3])}
    elif a.action == "wheel":
        cmd = {"action": "wheel", "x": int(v[0]), "y": int(v[1]), "amount": int(v[2])}
    elif a.action == "run":
        cmd = {"action": "run", "args": v or ["main.py"]}
    else:
        cmd = {"action": a.action}
    cmd["wait"] = a.wait
    res = send(cmd)
    print(json.dumps(res, ensure_ascii=False))
    sys.exit(0 if res.get("ok") else 1)


if __name__ == "__main__":
    main()
