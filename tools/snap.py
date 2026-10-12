"""게임 플레이 중 F9를 누르면 게임 창 화면을 screenshots/ 에 저장. F12로 종료.

    python -m tools.snap
"""
import os
import time
from datetime import datetime

import yaml

from bot.actions import HotkeyWatcher
from bot.vision import save_image
from bot.window import GameWindow, enable_dpi_awareness, find_windows

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "screenshots")


def main():
    enable_dpi_awareness()
    with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    title, process_name = cfg["window_title"], cfg.get("process_name")

    matches = find_windows(title, process_name)
    if not matches:
        print(f"'{title}' 이(가) 들어간 창을 찾지 못했습니다. config.yaml 의 window_title 을 확인하세요.")
        return
    print("찾은 창:", ", ".join(t for _, t in matches))

    window = GameWindow(title, process_name)
    keys = HotkeyWatcher("F9", "F12")
    os.makedirs(OUT_DIR, exist_ok=True)
    print("F9 = 스크린샷 저장, F12 = 종료")

    while True:
        if keys.pressed("F12"):
            break
        if keys.pressed("F9"):
            if window.locate():
                path = os.path.join(OUT_DIR, f"{datetime.now():%Y%m%d_%H%M%S_%f}.png")
                save_image(path, window.grab())
                print(f"저장: {path} ({window.width}x{window.height})")
            else:
                print("게임 창을 찾지 못했습니다.")
        time.sleep(0.05)


if __name__ == "__main__":
    main()
