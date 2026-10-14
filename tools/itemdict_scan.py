"""아이템 사전 '수집 재료' / '화첩 조각' 획득처 수집 (읽기 전용). 결과는 item_dict.json, 로그는 logs/itemdict_scan.log

    사전 창(메뉴 → 아이템 사전)을 열어 둔 상태에서:
    python tools/itemdict_scan.py                  # 수집 재료 → 화첩 조각 전부
    python tools/itemdict_scan.py "수집 재료"        # 한 카테고리만
    python tools/itemdict_scan.py --list           # 지금 보이는 목록 줄만 읽어 보기 (시험용)
    python tools/itemdict_scan.py --finalize       # 저장된 item_dict.json의 이름 철자·전투력만 정리

브리지(start_bridge.bat)가 켜져 있어야 한다. 입력은 브리지가 넣으므로 일반 권한으로 실행한다.
스캔하는 동안 게임 화면을 건드리지 말 것 (봇은 꺼 둘 것).
"""
import logging
import os
import sys
import time

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
                    handlers=[logging.FileHandler(os.path.join(ROOT, "logs", "itemdict_scan.log"), "a", encoding="utf-8"),
                              logging.StreamHandler()])
from tools import ctl  # noqa: E402
from bot2.itemdict import ItemDictScanner, CATEGORY_TAB, finalize, save  # noqa: E402


class CtlDriver:
    def shot(self):
        return cv2.imread(ctl.send({"action": "screenshot"})["screenshot"])

    def click(self, x, y):
        ctl.send({"action": "click", "x": int(x), "y": int(y)})

    def drag(self, x1, y1, x2, y2):
        ctl.send({"action": "drag", "x": int(x1), "y": int(y1), "x2": int(x2), "y2": int(y2)})

    def key(self, k):
        ctl.send({"action": "key", "key": k})

    def sleep(self, s):
        time.sleep(s)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--finalize" in sys.argv:                     # 화면 없이 저장된 item_dict.json만 정리
        from bot2.itemdict import load
        db = load()
        logging.info("정리: 이름 통일 %d · 전투력 채움 %d", *finalize(db))
        save(db)
        return
    sc = ItemDictScanner(CtlDriver())
    if not sc.dict_open(sc.drv.shot()):
        logging.error("아이템 사전 창이 열려 있지 않음 (메뉴 → 아이템 사전)")
        return
    if "--list" in sys.argv:
        sc.close_panel()
        print(sc.list_rows(sc.drv.shot()))
        return
    for cat in (args or list(CATEGORY_TAB)):
        n = sc.scan_category(cat)
        logging.info("%s: 새로 읽은 아이템 %d개 (누적 %d)", cat, n, len(sc.db["items"]))
    logging.info("정리: 이름 통일 %d · 전투력 채움 %d", *finalize(sc.db))
    save(sc.db)
    logging.info("끝")


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        logging.exception("실패")
