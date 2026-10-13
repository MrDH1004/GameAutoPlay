"""하단 물약 칸(Ctrl+1/Ctrl+2) 숫자 읽기 점검 (브리지 run 으로 실행). 결과는 logs/potion_slots.log"""
import ctypes  # noqa: E402
ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)   # 브리지가 띄운 콘솔 창 숨김 (백그라운드 실행)
import logging
import os
import sys

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
                    handlers=[logging.FileHandler(os.path.join(ROOT, "logs", "potion_slots.log"), "w", encoding="utf-8")])
from bot.window import GameWindow, enable_dpi_awareness  # noqa: E402
from bot2.farm import _crop  # noqa: E402
from bot2.shop import POTION_COUNT_BOXES, _read_count  # noqa: E402

enable_dpi_awareness()
w = GameWindow("도깨비의세계", "dokkaebi.exe")
w.locate()
f = w.grab()
cv2.imwrite(os.path.join(ROOT, "logs", "potion_slots.png"), f)
logging.info("화면 %s, Ctrl+1=%s", f.shape,
             *[_read_count(_crop(f, b)) for b in POTION_COUNT_BOXES])
