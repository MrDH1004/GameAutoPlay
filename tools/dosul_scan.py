"""도술 화면(전체 탭)의 학습한 도술을 위에서부터 차례로 눌러 왼쪽 설명창을 OCR로 읽는다.

    python -m tools.dosul_scan [out.json]

브리지(start_bridge.bat)가 켜져 있어야 하고, 도술 화면이 열려 있어야 한다. 잠긴(미습득) 도술은 건너뛴다.
"""
import json
import os
import subprocess
import sys
import time

import cv2
import numpy as np


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_W, BASE_H = 1360, 768
LIST_X = (1035, 1268)           # 목록 카드 가로 범위 (창 1360x768 기준)
LIST_Y = (122, 760)
LEFT = (0, 110, 270, 500)       # 왼쪽 설명창 (x0, y0, x1, y1)


def ctl(*args):
    out = subprocess.run([sys.executable, "-m", "tools.ctl", *map(str, args)], cwd=ROOT,
                         capture_output=True, text=True).stdout
    return out


def shot():
    out = ctl("shot")
    path = json.loads(out[out.index("{"):])["screenshot"]
    time.sleep(0.2)
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def card_rows(img):
    """목록에서 카드 중심 y 좌표들과 (잠금 여부)."""
    col = img[LIST_Y[0]:LIST_Y[1], 1040:1046].mean(axis=(1, 2))
    bright = col > (col.min() + col.max()) / 2 - 8
    rows, start = [], None
    for i, b in enumerate(list(bright) + [False]):
        if b and start is None:
            start = i
        elif not b and start is not None:
            if i - start > 50:
                rows.append((start + i) // 2 + LIST_Y[0])
            start = None
    out = []
    for y in rows:
        txt = img[y - 22:y - 4, 1100:1200]
        locked = txt.max() < 215
        out.append((y, locked))
    return out


def title_sig(crop):
    g = cv2.cvtColor(crop[5:40, 55:265], cv2.COLOR_BGR2GRAY)
    return g.astype(np.float32)


PITCH = 72.55
FIRST = 163.0                  # 맨 위로 스크롤했을 때 첫 카드 중심 y


def scroll_shift(prev, cur, sx, sy):
    """prev → cur 목록이 위로 밀린 픽셀 수 (아이콘 열을 템플릿 매칭)."""
    best = None
    for y0 in (540, 600, 660):
        yy = round(y0 * sy)
        hh = max(20, round(60 * sy))
        x0, x1 = round(1040 * sx), round(1100 * sx)
        ly0, ly1 = round(LIST_Y[0] * sy), round(LIST_Y[1] * sy)
        tpl = prev[yy:yy + hh, x0:x1]
        res = cv2.matchTemplate(cur[ly0:ly1, x0:x1], tpl, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        if best is None or mx > best[0]:
            best = (mx, yy - (loc[1] + ly0))
    return best[1] if best[0] > 0.6 else None


def main():
    output = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(ROOT, "screenshots", "dosul_scan.json")
    out_dir = os.path.join(ROOT, "screenshots", "dosul")
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        os.remove(os.path.join(out_dir, f))
    img = shot()
    h, w = img.shape[:2]
    sx, sy = w / BASE_W, h / BASE_H
    for _ in range(10):                          # 맨 위로
        ctl("drag", round(1150 * sx), round(250 * sy), round(1150 * sx), round(700 * sy))
    time.sleep(0.5)
    sigs, crops, done = [], [], set()
    G = 0.0                                      # 지금까지 스크롤한 양
    img = shot()
    for page in range(16):
        for k in range(120):
            y = (FIRST + PITCH * k) * sy - G
            if y < (LIST_Y[0] + 20) * sy or y > (LIST_Y[1] - 35) * sy or k in done:
                continue
            done.add(k)
            x0, x1 = round(1100 * sx), round(1200 * sx)
            locked = img[round(y - 22 * sy):round(y - 4 * sy), x0:x1].max() < 170
            if locked:
                continue
            ctl("click", round(1150 * sx), round(y))
            time.sleep(0.7)
            fresh = shot()
            lx0, ly0, lx1, ly1 = (round(LEFT[0] * sx), round(LEFT[1] * sy),
                                   round(LEFT[2] * sx), round(LEFT[3] * sy))
            crop = fresh[ly0:ly1, lx0:lx1]
            normalized = cv2.resize(crop, (LEFT[2] - LEFT[0], LEFT[3] - LEFT[1]))
            sig = title_sig(normalized)
            if any(np.abs(sig - s0).mean() < 6 for s0 in sigs):
                continue
            sigs.append(sig)
            crops.append(crop)
            cv2.imencode(".png", crop)[1].tofile(os.path.join(out_dir, f"{len(crops):02d}.png"))
            print(len(crops), "k=", k, flush=True)
        ctl("drag", round(1150 * sx), round(650 * sy), round(1150 * sx), round(330 * sy))
        time.sleep(0.8)
        new = shot()
        d = scroll_shift(img, new, sx, sy)
        if d is None or d <= 0:
            print("스크롤 이동량을 못 찾음, 중단")
            break
        G += d
        img = new
    # 4열 시트로 이어 붙인다
    for n, start in enumerate(range(0, len(crops), 8)):
        group = crops[start:start + 8]
        rows = []
        for r in range(0, len(group), 4):
            row = group[r:r + 4]
            row += [np.zeros_like(crops[0])] * (4 - len(row))
            rows.append(np.hstack(row))
        cv2.imencode(".png", np.vstack(rows))[1].tofile(os.path.join(out_dir, f"sheet{n + 1}.png"))
    from tools.ocr import read_text
    records = []
    for index, crop in enumerate(crops, 1):
        lines = read_text(crop, scale=1.5)
        text = " | ".join(line["text"] for line in lines if line.get("text"))
        records.append({"index": index, "image": f"dosul/{index:02d}.png", "text": text, "lines": lines})
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print("완료:", len(crops), "개")
    print("OCR:", output)


if __name__ == "__main__":
    main()
