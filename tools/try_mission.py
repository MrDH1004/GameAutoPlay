"""임무 화면 자동 진행만 한 번 실행해 본다 (관리자 권한 필요, bridge run으로 실행).  logs/try_mission.log 에 기록."""
import logging
import os
import sys
import time

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from bot2.brain import Bot  # noqa: E402
from bot.window import enable_dpi_awareness  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
                    handlers=[logging.FileHandler(os.path.join(ROOT, "logs", "try_mission.log"), "w", "utf-8")])
enable_dpi_awareness()
cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
bot = Bot(cfg)
bot.user_resume = 0                 # 테스트 중에는 사용자 조작 감지를 끈다
for n in sys.argv[1:]:                # 인자로 준 탭은 건너뜀 (탭별 테스트용)
    bot.mission.tab_blocked_until[n] = time.monotonic() + 3600
bot.window.locate()
bot.matcher.set_degrade(bot.window.width / 1920)
logging.info("결과: %s", bot.mission.run(time.monotonic()))
