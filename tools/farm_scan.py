"""세계지도의 '지역 정보' 목록을 훑어 사냥터별 몬스터 전투력을 farm_spots.json에 저장한다.

    python -m tools.farm_scan "영험한 청산" 962 585
        (지역 이름, 타바로국 지도에서 그 지역 배너의 화면 좌표)

브리지(start_bridge.bat)가 켜져 있고 게임이 1920x1080 필드 화면이어야 한다. 카드 이미지는 templates/farm/에 저장된다.
이미 있는 지역은 덮어쓴다.
"""
import json
import os
import subprocess
import sys
import time

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from bot2 import farm  # noqa: E402


class BridgeDriver(farm.Driver):
    def _ctl(self, *args):
        for _ in range(3):
            p = subprocess.run([sys.executable, "-m", "tools.ctl", *map(str, args)], cwd=ROOT,
                               capture_output=True, text=True)
            if p.stdout.strip().startswith("{") or "{" in p.stdout:
                return p.stdout
            time.sleep(0.5)
        raise RuntimeError("ctl 실패: " + p.stderr[-200:])

    def shot(self):
        out = self._ctl("shot")
        path = json.loads(out[out.index("{"):])["screenshot"]
        time.sleep(0.25)
        return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)

    def click(self, x, y):
        self._ctl("click", int(x), int(y))

    def drag(self, x1, y1, x2, y2):
        self._ctl("drag", int(x1), int(y1), int(x2), int(y2))

    def key(self, k):
        self._ctl("key", k)


def same_img(a, b, frac=0.012):
    if a.shape != b.shape:
        return False
    return (np.abs(a.astype(int) - b.astype(int)).max(axis=2) > 60).mean() < frac


def main():
    region_name, bx, by = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    drv = BridgeDriver()
    nav = farm.MapNav(drv)
    db = farm.load_spots()
    db["regions"][region_name] = {"banner": [bx, by]}
    db["spots"] = [s for s in db["spots"] if s["region"] != region_name]
    os.makedirs(farm.CARD_DIR, exist_ok=True)
    key = f"r{len(db['regions'])}"
    for i, n in enumerate(db["regions"]):
        if n == region_name:
            key = f"r{i + 1}"

    if not nav.open_region(db["regions"][region_name]):
        raise SystemExit("지역 정보 목록을 열지 못함")
    nav.scroll_top()
    seen = []                                     # 처리한 카드 이름 띠 이미지
    img = drv.shot()

    def already(img, y):
        """같은 카드를 이미 처리했는가 (스크롤로 위치가 달라져도 이미지 매칭으로 찾는다)."""
        y0, y1 = max(farm.LIST_Y[0], y - 45), min(farm.LIST_Y[1], y + 45)
        area = img[y0:y1, 170:395]
        for t in seen:
            if area.shape[0] >= t.shape[0] and area.shape[1] >= t.shape[1]:
                _, mx, _, _ = cv2.minMaxLoc(cv2.matchTemplate(area, t, cv2.TM_CCOEFF_NORMED))
                if mx > 0.9:
                    return True
        return False

    for page in range(30):
        rows = farm.find_card_rows(img)
        print(f"페이지 {page}: 카드 {len(rows)}장", flush=True)
        for y in rows:
            if y - 33 < farm.LIST_Y[0] or y + 33 > farm.LIST_Y[1]:      # 반쯤 잘린 카드는 다음 페이지에서
                continue
            if already(img, y):
                continue
            crop = farm.card_crop(img, y)
            seen.append(crop)
            drv.click(farm.LIST_CLICK_X, y)
            drv.sleep(1.3)
            shot = drv.shot()
            vals = farm.read_monster_powers(shot)
            red = farm.red_ratio(shot)
            title = farm._ocr(farm._crop(shot, farm.PANEL_TITLE), 3).strip()
            n = len(db["spots"]) + 1
            card = f"{key}_{len([s for s in db['spots'] if s['region'] == region_name]) + 1:02d}.png"
            cv2.imencode(".png", crop)[1].tofile(os.path.join(farm.CARD_DIR, card))
            spot = {"id": f"{key}_{n:03d}", "region": region_name, "name": title, "card": card,
                    "powers": vals, "power": farm.consensus(vals), "red": round(red, 3)}
            db["spots"].append(spot)
            print("  ", spot["id"], title, vals, "red" if red > 0.01 else "", flush=True)
            farm.save_spots(db)
        drv.drag(farm.LIST_CLICK_X, 900, farm.LIST_CLICK_X, 450)
        drv.sleep(0.8)
        new = drv.shot()
        d = farm.scroll_shift(img, new)
        if d is None or d < 5:
            break
        img = new
    nav.close_map()
    print("완료:", len([s for s in db["spots"] if s["region"] == region_name]), "곳")


if __name__ == "__main__":
    main()
