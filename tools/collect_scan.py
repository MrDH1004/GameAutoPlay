"""화첩/유물 수집 스캔·등록·이동 단독 실행 (관리자 권한 필요: 브리지 run 으로 실행). 결과는 logs/collect_scan.log

    tools/collect_scan.py scan       # 화첩 + 유물 스캔 → collect_db.json
    tools/collect_scan.py album [책번호…]   # 화첩만 스캔 (0 인물 1 동물 2 곤충 3 사물 4 도깨비 5 요귀)
    tools/collect_scan.py relics     # 유물 수집만 스캔 (항목마다 저장)
    tools/collect_scan.py fix        # 완성(9/9)인데 몬스터가 비어 있는 그림만 재확인
    tools/collect_scan.py register   # 등록만
    tools/collect_scan.py go         # 수집 몬스터 하나 골라 걸어가기
"""
import ctypes  # noqa: E402
ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)   # 브리지가 띄운 콘솔 창 숨김 (백그라운드 실행)
import logging
import os
import sys
import time

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
                    handlers=[logging.FileHandler(os.path.join(ROOT, "logs", "collect_scan.log"), "w", encoding="utf-8")])
from bot.window import enable_dpi_awareness  # noqa: E402
from bot2.brain import Bot  # noqa: E402
from bot2.collect import CollectTask, save_db  # noqa: E402
from bot2.farm import read_power  # noqa: E402
from tools.bridge_ops import focus_game  # noqa: E402

mode = sys.argv[1] if len(sys.argv) > 1 else "scan"
enable_dpi_awareness()
cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
b = Bot(cfg)
b.window.locate()
focus_game(b.window)
b.activity.ignore_until_now(1e9)
b._check_user = lambda: None
c = b.collect or CollectTask(b, cfg.get("collect") or {})
try:
    if mode == "scan":
        c.scan()
    elif mode == "album":
        books = [int(v) for v in sys.argv[2:]] or None          # 예: album 3 4 5 → 사물화/도깨비화/요귀화만
        new = c.col.scan_album(books=books)
        keep = [e for e in c.db.get("album", []) if books is not None and e["book"] not in books]
        c.db["album"] = sorted(keep + new, key=lambda e: (e["book"], e["idx"]))
        c.db["scanned_at"] = time.time()
        save_db(c.db, c.path)
    elif mode == "relics":
        def _save(items):
            c.db["relics"] = items
            save_db(c.db, c.path)
        c.db["relics"] = c.col.scan_relics(int(cfg.get("collect", {}).get("relic_max_items", 150)), save=_save)
        c.db["scanned_at"] = time.time()
        save_db(c.db, c.path)
    elif mode == "fix":
        n = c.col.rescan_done(c.db, save=lambda: save_db(c.db, c.path))
        logging.info("재확인으로 고친 그림 %d개", n)
    elif mode == "register":
        c.register(time.monotonic())
    elif mode == "go":
        c.col.reset_ui()
        power = read_power(b.window.grab())
        logging.info("전투력 %s → 이동 %s", power, c.go(power, time.monotonic()))
except Exception:  # noqa: BLE001
    logging.exception("실패")
logging.info("끝")
