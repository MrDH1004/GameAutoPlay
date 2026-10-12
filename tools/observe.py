"""게임 화면을 일정 간격으로 캡처해 한 장의 미리보기(contact sheet)로 만든다. (일반 권한으로 동작)

    python -m tools.observe 20 2 obs_name      # 20초 동안 2초 간격
결과: samples/obs/<name>/*.png, samples/obs/<name>_sheet.png
"""
import os
import sys
import time
from datetime import datetime

import cv2
import numpy as np
import yaml

from bot.vision import save_image
from bot.window import GameWindow, enable_dpi_awareness

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    seconds, step, name = float(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
    enable_dpi_awareness()
    with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    win = GameWindow(cfg["window_title"], cfg.get("process_name"))
    out = os.path.join(ROOT, "samples", "obs", name)
    os.makedirs(out, exist_ok=True)

    tiles = []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if win.locate():
            frame = win.grab()
            stamp = datetime.now().strftime("%H%M%S")
            save_image(os.path.join(out, f"{stamp}.png"), frame)
            t = cv2.resize(frame, (640, 360))
            cv2.putText(t, stamp, (8, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            tiles.append(t)
        time.sleep(step)

    while len(tiles) % 3:
        tiles.append(np.zeros_like(tiles[0]))
    sheet = np.vstack([np.hstack(tiles[i:i + 3]) for i in range(0, len(tiles), 3)])
    path = os.path.join(ROOT, "samples", "obs", f"{name}_sheet.png")
    save_image(path, sheet)
    print(path, len(tiles))


if __name__ == "__main__":
    main()
