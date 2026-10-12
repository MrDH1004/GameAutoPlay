"""트래커를 스크롤하며 퀘스트를 읽어 출력한다. (관리자 권한 → bridge run 으로 실행)  logs/scan_tracker.log
    -m tools.scan_tracker            스캔만
    -m tools.scan_tracker click      스캔 후 첫 번째 진행 후보를 클릭
"""
import logging
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from bot.window import enable_dpi_awareness  # noqa: E402
from bot2.brain import Bot  # noqa: E402
from bot2.tracker import TrackerScanner  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
                    handlers=[logging.FileHandler(os.path.join(ROOT, "logs", "scan_tracker.log"), "w", "utf-8")])
enable_dpi_awareness()
cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
bot = Bot(cfg)
bot.user_resume = 0
bot.window.locate()
bot.matcher.set_degrade(bot.window.width / 1920)
sc = TrackerScanner(bot, (cfg.get("quest_tracker") or {}).get("scan") or {})
sc.scan()
cands = sc.candidates()
logging.info("진행 후보 순서: %s", [f"{e.kind}:{e.text}" for e in cands])
if "click" in sys.argv[1:] and cands:
    sc.click(cands[0])
logging.info("끝")
