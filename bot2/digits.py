"""스스로 배우는 숫자 템플릿 (전투력 읽기 가속/보조).

OCR(PowerShell, 1~2초)이 확실하게 읽은 전투력 화면에서 숫자 글자를 잘라 0~9 템플릿을 모은다.
0~9가 다 모이면 OCR 없이 템플릿만으로 즉시 읽는다 (가끔 OCR로 교차 확인).
템플릿: templates/digits/power_<숫자>.png (여러 번 배우면 평균)
"""
import logging
import os

import cv2
import numpy as np

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, "templates", "digits")
SIZE = (12, 18)          # (w, h)


def _binary(crop, thr=150):
    return (crop.max(axis=2) > thr).astype(np.uint8)


def segment_number(crop):
    """오른쪽 끝 숫자 묶음의 글자 이미지들 (쉼표 제외, 왼쪽→오른쪽). 실패하면 []."""
    b = _binary(crop)
    n, _, stats, _ = cv2.connectedComponentsWithStats(b, 8)
    comps = [stats[i] for i in range(1, n) if stats[i][cv2.CC_STAT_AREA] >= 4]
    if not comps:
        return []
    comps.sort(key=lambda s: s[cv2.CC_STAT_LEFT])
    h_max = max(s[cv2.CC_STAT_HEIGHT] for s in comps)
    # 오른쪽부터 간격이 크게 벌어지기 전까지가 숫자 묶음
    group = [comps[-1]]
    for s in reversed(comps[:-1]):
        right = s[cv2.CC_STAT_LEFT] + s[cv2.CC_STAT_WIDTH]
        if group[0][cv2.CC_STAT_LEFT] - right > max(4, h_max * 0.45):
            break
        group.insert(0, s)
    digits = [s for s in group if s[cv2.CC_STAT_HEIGHT] >= h_max * 0.6]     # 쉼표는 키가 작다
    out = []
    for s in digits:
        x, y, w, h = s[0], s[1], s[2], s[3]
        g = b[y:y + h, x:x + w].astype(np.float32)
        out.append(cv2.resize(g, SIZE, interpolation=cv2.INTER_AREA))
    return out


class DigitBook:
    def __init__(self, name="power"):
        self.name = name
        self.tpl = {}
        self.count = {}
        os.makedirs(DIR, exist_ok=True)
        for d in range(10):
            p = os.path.join(DIR, f"{name}_{d}.png")
            if os.path.exists(p):
                img = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
                if img is not None:
                    self.tpl[d] = img.astype(np.float32) / 255.0
                    self.count[d] = 5

    @property
    def complete(self):
        return len(self.tpl) == 10

    def learn(self, crop, value):
        glyphs = segment_number(crop)
        text = str(value)
        if len(glyphs) != len(text):
            return False
        for ch, g in zip(text, glyphs):
            d = int(ch)
            n = self.count.get(d, 0)
            self.tpl[d] = g if d not in self.tpl else (self.tpl[d] * n + g) / (n + 1)
            self.count[d] = min(n + 1, 20)
            cv2.imencode(".png", (self.tpl[d] * 255).astype(np.uint8))[1].tofile(
                os.path.join(DIR, f"{self.name}_{d}.png"))
        return True

    def read(self, crop, max_dist=0.18):
        """템플릿으로 읽은 정수. 모르는 글자가 있거나 애매하면 None."""
        if not self.complete:
            return None
        glyphs = segment_number(crop)
        if not glyphs:
            return None
        out = ""
        for g in glyphs:
            dists = {d: float(np.mean(np.abs(g - t))) for d, t in self.tpl.items()}
            d, best = min(dists.items(), key=lambda kv: kv[1])
            if best > max_dist:
                return None
            out += str(d)
        return int(out) if out else None
