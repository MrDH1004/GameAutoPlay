"""오른쪽 퀘스트 트래커를 스크롤하며 OCR로 읽어 보이지 않는 퀘스트까지 파악하고, 진행할 퀘스트를 고른다."""
import logging
import re
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from bot import actions
from tools.ocr import read_text

log = logging.getLogger(__name__)


@dataclass
class Entry:
    text: str
    kind: str                 # main(본편 [N장]) / accept(서사 임무 수락 가능) / side([외전] 등) / other
    badge: str                # ok(적합) / danger(위험) / ''
    ticks: int                # 맨 위에서 휠을 몇 칸 내려야 보이는지
    x: int                    # 화면(기준 1920x1080) 클릭 좌표
    y: int
    objectives: list = field(default_factory=list)


def _textmask(img):
    """밝은 글자(흰/노랑/초록) 픽셀만 남긴 마스크. 움직이는 배경을 무시하고 목록 모양만 비교하려고 쓴다."""
    b, g, r = (img[..., i].astype(int) for i in range(3))
    white = np.minimum(np.minimum(r, g), b) > 175
    yellow = (r > 200) & (g > 170) & (b < 140)
    return (white | yellow).astype(np.uint8) * 255


def _norm(t):
    return re.sub(r"[\s\W\d_]+", "", t)


def _kind(text):
    if re.search(r"\[\s*\d+\s*장\s*\]|\d\s*장\s*\]", text):
        return "main"
    if "수락" in text and "가능" in text:
        return "accept"
    if re.search(r"외전|일일|주간|첫걸음", text) or re.search(r"^\W{0,3}\[", text.strip()):
        return "side"
    return "other"


class TrackerScanner:
    def __init__(self, bot, cfg):
        self.bot = bot
        self.cfg = cfg
        self.region = cfg.get("region", [0.78, 0.06, 0.2, 0.40])      # 트래커 영역(비율)
        self.max_pages = cfg.get("max_pages", 8)
        self.step = cfg.get("wheel_step", 3)
        self.entries = []
        self.scanned_at = 0.0

    # ---------- 화면/스크롤 ----------
    def _rect(self):
        w, h = self.bot.window.frame_w, self.bot.window.frame_h
        x0, y0 = int(self.region[0] * w), int(self.region[1] * h)
        return x0, y0, int(self.region[2] * w), int(self.region[3] * h)

    def _crop(self):
        x0, y0, w, h = self._rect()
        return self.bot.window.grab()[y0:y0 + h, x0:x0 + w], x0, y0

    def _wheel(self, amount):
        x0, y0, w, h = self._rect()
        sx, sy = self.bot.window.frame_to_screen(x0 + w * 0.5, y0 + h * 0.5)
        if self.bot.dry_run:
            log.info("[dry-run] 트래커 휠 %d", amount)
            return
        self.bot._check_user()
        actions.wheel(sx, sy, amount)
        time.sleep(0.5)

    @staticmethod
    def _same(a, b):
        """두 화면의 글자 마스크가 거의 같으면 True (목록이 스크롤되지 않았다는 뜻)."""
        if a.shape != b.shape:
            return False
        ma, mb = _textmask(a), _textmask(b)
        total = max(1, int(np.count_nonzero(ma | mb)))
        return int(np.count_nonzero(ma ^ mb)) / total < 0.12

    def _badge(self, crop, y, x_off):
        """줄 오른쪽의 배지 색: 초록=적합, 빨강=위험."""
        h, w = crop.shape[:2]
        band = crop[max(0, int(y) - 10):min(h, int(y) + 16), int(w * 0.74):]
        if band.size == 0:
            return ""
        b, g, r = (band[..., i].astype(int) for i in range(3))
        green = int(np.sum((g > 130) & (r < 120) & (b < 150) & (g - r > 40)))
        red = int(np.sum((r > 200) & (g < 70) & (b < 70)))
        if red > 50:
            return "danger"
        if green > 60:
            return "ok"
        return ""

    # ---------- 스캔 ----------
    @staticmethod
    def _measure_shift(c0, c1):
        """c0 → c1 로 목록이 위로 몇 픽셀 올라갔는지 (글자 마스크 템플릿 매칭). (dy, score)"""
        m0, m1 = _textmask(c0), _textmask(c1)
        y0, hh = int(c0.shape[0] * 0.2), int(c0.shape[0] * 0.4)
        res = cv2.matchTemplate(m1, m0[y0:y0 + hh], cv2.TM_CCORR_NORMED)
        _, score, _, (mx, my) = cv2.minMaxLoc(res)
        return y0 - my, score

    def _scroll_measured(self, notches):
        """휠을 굴리고 실제 이동한 픽셀 수를 돌려준다 (0이면 끝에 닿은 것)."""
        c0, _, _ = self._crop()
        self._wheel(notches)
        time.sleep(0.4)                                  # 부드러운 스크롤 애니메이션이 끝날 때까지
        c1, _, _ = self._crop()
        dy, score = self._measure_shift(c0, c1)
        if score < 0.5:
            return None                                  # 측정 불가
        return dy

    def _to_top(self):
        for _ in range(5):
            dy = self._scroll_measured(120)
            if dy is not None and abs(dy) < 3:
                return

    def scan(self):
        """맨 위 → 아래로 스크롤하며 읽고 다시 맨 위로. Entry 목록을 반환 (화면에 안 보이던 퀘스트 포함)."""
        t0 = time.monotonic()
        self._to_top()
        entries, cum, ticks = [], 0.0, 0
        absy = []
        step = self.cfg.get("page_notches", 36)
        for page in range(self.max_pages):
            crop, ox, oy = self._crop()
            try:
                lines = read_text(crop)
            except Exception as e:                       # noqa: BLE001
                log.warning("OCR 실패: %s", e)
                break
            last = None
            for ln in sorted(lines, key=lambda l: l["y"]):
                text = ln["text"].strip()
                kind = _kind(text)
                ay = ln["y"] + cum                       # 스크롤과 무관한 목록상 위치
                if kind == "other":
                    if last is not None and 0 < ay - last[1] < 80 and                             re.search(r"\d+\s*/\s*\d+|처치|획득|이동|대화|수집", text):
                        last[0].objectives.append(text)
                    continue
                dup = next((e for e, y2 in absy if e.kind == kind and abs(y2 - ay) < 14), None)
                if dup is not None:
                    last = (dup, ay)
                    continue
                e = Entry(text=text, kind=kind, badge=self._badge(crop, ln["y"], ln["x"]), ticks=ticks,
                          x=int(ox + max(ln["x"] + 40, 80)), y=int(oy + ln["y"] + ln["h"] / 2))
                entries.append(e)
                absy.append((e, ay))
                last = (e, ay)
            dy = self._scroll_measured(-step)
            if dy is None or dy < 3:
                break                                    # 더 내려갈 곳이 없음
            cum += dy
            ticks += step
        self._to_top()
        self.entries = entries
        self.scanned_at = time.monotonic()
        log.info("트래커 스캔 완료 (%.1fs, 퀘스트 %d개, 목록 길이 약 %dpx)", self.scanned_at - t0, len(entries), cum)
        for i, e in enumerate(entries):
            log.info("  [%d] %-6s %-6s %s%s", i, e.kind, e.badge or "-", e.text,
                     ("  → " + " / ".join(e.objectives)) if e.objectives else "")
        return entries

    # ---------- 선택/클릭 ----------
    def candidates(self, skip_kinds=()):
        """진행 후보: 위험 배지가 없는 항목, 본편 → 수락 가능 → 기타 순."""
        order = {"main": 0, "accept": 1, "side": 2}
        ok = [e for e in self.entries if e.badge != "danger" and e.kind not in skip_kinds]
        return sorted(ok, key=lambda e: order.get(e.kind, 9))

    def click(self, entry):
        """그 항목이 보이는 위치까지 스크롤한 뒤 클릭."""
        self._to_top()
        if entry.ticks:
            self._wheel(-entry.ticks)
        log.info("트래커 퀘스트 클릭: [%s] %s (%d,%d)", entry.kind, entry.text, entry.x, entry.y)
        self.bot._click_frame("트래커 퀘스트", entry.x, entry.y)
