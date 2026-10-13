"""영기 수련 한 번 단독 실행 (관리자 권한 필요: 브리지 run 으로 실행). 결과는 logs/test_yeonggi.log"""
import ctypes
ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)   # 브리지가 띄운 콘솔 창 숨김
import logging
import os
import sys
import time

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
                    handlers=[logging.FileHandler(os.path.join(ROOT, "logs", "test_yeonggi.log"), "w", encoding="utf-8")])
from bot.window import enable_dpi_awareness  # noqa: E402
from bot2.brain import Bot  # noqa: E402
from tools.bridge_ops import focus_game  # noqa: E402

enable_dpi_awareness()
cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
b = Bot(cfg)
b.window.locate()
focus_game(b.window)
b.activity.ignore_until_now(1e9)
b._check_user = lambda: None
try:
    b.yeonggi.next_check = 0.0
    b.yeonggi.run(time.monotonic())
except Exception:  # noqa: BLE001
    logging.exception("실패")
logging.info("끝")
