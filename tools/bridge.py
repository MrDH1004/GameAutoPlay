"""관리자 권한으로 실행되는 제어 브리지.

게임이 관리자 권한으로 실행되기 때문에, 일반 권한 프로세스(Claude 등)는 게임에 입력을 보낼 수 없다.
이 브리지가 bridge/in/*.json 명령을 읽어 대신 실행하고, 결과 화면을 bridge/out/ 에 저장한다.
실행부(tools/bridge_ops.py)는 파일이 바뀔 때마다 다시 불러온다.

    start_bridge.bat   (관리자 권한으로 자동 재실행)
    F12 = 브리지 종료

명령 형식 (좌표는 게임 창 기준 픽셀, 1920x1080):
    {"id": "...", "action": "click", "x": 100, "y": 200, "wait": 1.0}
    {"id": "...", "action": "key", "key": "esc", "wait": 1.0}
    {"id": "...", "action": "drag", "x": 100, "y": 200, "x2": 500, "y2": 200}
    {"id": "...", "action": "wheel", "x": 100, "y": 200, "amount": -3}
    {"id": "...", "action": "focus"}
    {"id": "...", "action": "screenshot"}
    {"id": "...", "action": "run", "args": ["main.py"]}     # 매크로를 관리자 권한으로 실행
    {"id": "...", "action": "kill"}                         # 브리지가 띄운 매크로 종료
"""
import ctypes
import glob
import importlib
import json
import os
import subprocess
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import yaml  # noqa: E402

from bot import actions  # noqa: E402
from bot.vision import save_image  # noqa: E402
from bot.window import GameWindow, enable_dpi_awareness  # noqa: E402
from tools import bridge_ops  # noqa: E402

IN_DIR = os.path.join(ROOT, "bridge", "in")
OUT_DIR = os.path.join(ROOT, "bridge", "out")
OPS_FILE = os.path.join(ROOT, "tools", "bridge_ops.py")


def stop_other_instances():
    """같은 폴더의 매크로(main.py)나 이전 브리지가 돌고 있으면 종료."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { ($_.CommandLine -like '*main.py*' -or $_.CommandLine -like '*tools.bridge*') "
          "-and $_.ProcessId -ne " + str(os.getpid()) + " } | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    if out.stdout.strip():
        print("실행 중이던 매크로/브리지 종료:", out.stdout.split())


def main():
    global bridge_ops
    if sys.stdout is None:          # pythonw(콘솔 없음)로 실행되면 로그 파일로
        os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
        sys.stdout = sys.stderr = open(os.path.join(ROOT, "logs", "bridge.log"), "a",
                                       encoding="utf-8", buffering=1)
    enable_dpi_awareness()
    stop_other_instances()
    os.makedirs(IN_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    win = GameWindow(cfg["window_title"], cfg.get("process_name"))
    keys = actions.HotkeyWatcher("F12")
    admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    print(f"브리지 시작 (관리자 권한: {admin}). F12 = 종료")
    with open(os.path.join(OUT_DIR, "_status.json"), "w", encoding="utf-8") as f:
        json.dump({"running": True, "admin": admin, "pid": os.getpid()}, f)

    # 콘솔 창이 게임을 가리지 않도록 최소화하고 게임을 앞으로
    ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 6)
    if win.locate():
        bridge_ops.focus_game(win)

    ops_mtime = os.path.getmtime(OPS_FILE)
    macro = None
    while True:
        if keys.pressed("F12"):
            print("종료")
            break
        for path in sorted(glob.glob(os.path.join(IN_DIR, "*.json"))):
            try:
                with open(path, encoding="utf-8") as f:
                    cmd = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue            # 아직 쓰는 중
            os.remove(path)

            if os.path.getmtime(OPS_FILE) != ops_mtime:
                ops_mtime = os.path.getmtime(OPS_FILE)
                bridge_ops = importlib.reload(bridge_ops)
                print("bridge_ops 다시 불러옴")

            result = {"id": cmd.get("id"), "ok": True}
            try:
                action = cmd["action"]
                if action == "run":
                    if macro and macro.poll() is None:
                        macro.kill()
                    macro = subprocess.Popen([sys.executable] + cmd.get("args", ["main.py"]), cwd=ROOT,
                                             creationflags=subprocess.CREATE_NEW_CONSOLE)
                    result["pid"] = macro.pid
                elif action == "kill":
                    if macro and macro.poll() is None:
                        macro.kill()
                        result["killed"] = macro.pid
                else:
                    if not win.locate():
                        raise RuntimeError("게임 창을 찾지 못함")
                    bridge_ops.execute(win, cmd)
                    time.sleep(cmd.get("wait", 0 if action == "screenshot" else 1.0))
                    shot = os.path.join(OUT_DIR, f"{cmd['id']}.png")
                    save_image(shot, win.grab())
                    result["screenshot"] = shot
            except Exception as e:  # noqa: BLE001
                result.update(ok=False, error=f"{type(e).__name__}: {e}")
                traceback.print_exc()
            print(cmd.get("action"), cmd.get("x", cmd.get("key", "")), cmd.get("y", ""), "→",
                  "OK" if result["ok"] else result["error"])
            tmp = os.path.join(OUT_DIR, f"{cmd['id']}.json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False)
            os.replace(tmp, tmp[:-4])
        time.sleep(0.05)

    os.remove(os.path.join(OUT_DIR, "_status.json"))


if __name__ == "__main__":
    main()
