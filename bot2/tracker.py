"""오른쪽 퀘스트 트래커를 스크롤하며 OCR로 읽어 보이지 않는 퀘스트까지 파악하고, 진행할 퀘스트를 고른다."""
import logging
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher

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
        self.main_tab = cfg.get("main_tab", [0.975, 0.113])
        self.main_tab_wait = float(cfg.get("main_tab_wait", 0.6))
        self._main_tab_selected = False

    def _main_tab_active(self, frame):
        """Return True when the right-side main quest icon has its gold selected glow."""
        if frame is None or not hasattr(frame, "shape") or len(frame.shape) < 2:
            return False
        h, w = frame.shape[:2]
        main_tab = getattr(self, "main_tab", [0.975, 0.113])
        cx, cy = int(float(main_tab[0]) * w), int(float(main_tab[1]) * h)
        roi = frame[max(0, cy - 35):min(h, cy + 35),
                    max(0, cx - 30):min(w, cx + 30)]
        if roi.size == 0:
            return False
        b, g, r = (roi[..., i].astype(int) for i in range(3))
        gold = (r > 180) & (g > 140) & (b < 130) & ((r - b) > 70)
        return int(np.count_nonzero(gold)) >= 50

    def ensure_main_tab(self, frame=None, force=False):
        """우측 세로 메뉴의 첫 번째 ? 두루마리 아이콘으로 메인 퀘스트 목록을 표시한다."""
        if frame is None:
            live = getattr(self.bot, "live", None)
            frame = getattr(live, "_latest_frame", None)
        if self._main_tab_active(frame):
            self._main_tab_selected = True
            return False
        if getattr(self, "_main_tab_selected", False) and not force and frame is None:
            return False
        main_tab = getattr(self, "main_tab", None)
        if not main_tab or len(main_tab) != 2:
            return False
        w, h = self.bot.window.frame_w, self.bot.window.frame_h
        x, y = int(float(main_tab[0]) * w), int(float(main_tab[1]) * h)
        log.info("트래커 메인 퀘스트 탭 선택 (%d,%d)", x, y)
        self.bot._click_frame("트래커 메인 퀘스트 탭", x, y)
        self._main_tab_selected = True
        wait = float(getattr(self, "main_tab_wait", 0.0))
        if wait > 0:
            time.sleep(wait)
        return True

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
            # 글줄 매칭이 실패했다는 이유만으로 맨 아래로 판단하면 목록 중간에서
            # 스캔이 끝난다. 화면 자체가 같을 때만 끝으로 보고, 달라졌다면 한
            # 페이지 이동한 것으로 추정해 계속 스크롤한다.
            if self._same(c0, c1):
                return 0
            estimated = max(3, int(c0.shape[0] * 0.65))
            log.debug("트래커 스크롤 이동량 측정 실패(score=%.2f) → %dpx 이동으로 계속", score, estimated)
            return estimated
        return dy

    def _to_top(self):
        for _ in range(5):
            dy = self._scroll_measured(120)
            if dy is not None and abs(dy) < 3:
                return

    def scan(self):
        """맨 위 → 아래로 스크롤하며 읽고 다시 맨 위로. Entry 목록을 반환 (화면에 안 보이던 퀘스트 포함)."""
        t0 = time.monotonic()
        self.ensure_main_tab(force=True)
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
        self.ensure_main_tab(force=True)
        self._to_top()
        if entry.ticks:
            self._wheel(-entry.ticks)
        log.info("트래커 퀘스트 클릭: [%s] %s (%d,%d)", entry.kind, entry.text, entry.x, entry.y)
        self.bot._click_frame("트래커 퀘스트", entry.x, entry.y)

    def click_text(self, marker):
        """끝까지 스크롤하며 현재 화면에서 marker를 다시 OCR해 그 줄만 클릭한다.

        사전 스캔 좌표는 원위치 복귀와 재스크롤 과정에서 어긋날 수 있으므로
        일일 전용처럼 오선택이 치명적인 경우에는 저장 좌표를 사용하지 않는다.
        """
        self.ensure_main_tab(force=True)
        wanted = re.sub(r"\s+", "", marker)
        self._to_top()
        step = self.cfg.get("page_notches", 36)
        for _page in range(self.max_pages):
            crop, ox, oy = self._crop()
            try:
                lines = read_text(crop, scale=2)
            except Exception as exc:  # noqa: BLE001
                log.warning("트래커 '%s' OCR 실패: %s", marker, exc)
                lines = []
            for line in lines:
                compact = re.sub(r"\s+", "", str(line.get("text", "")))
                if wanted not in compact:
                    continue
                # 긴 목표 문구 중앙보다 행 아이콘 바로 오른쪽이 실제 선택 영역으로 안정적이다.
                x = int(ox + max(float(line["x"]) + 40, 80))
                y = int(oy + float(line["y"]) + float(line["h"]) / 2)
                log.info("트래커 문구 확인 클릭: %s (%d,%d)", line["text"], x, y)
                self.bot._click_frame(f"트래커 {marker}", x, y)
                return str(line["text"])
            dy = self._scroll_measured(-step)
            if dy is not None and dy < 3:
                break
        log.info("트래커를 끝까지 확인했지만 '%s' 항목을 찾지 못함", marker)
        self._to_top()
        return ""

    def click_suitable_story_accept(self):
        """필드 트래커의 초록 ``서사 임무 수락 가능!`` 행만 클릭한다.

        빨간 ``위험`` 외전과 임무창의 서사 탭은 건드리지 않는다.
        """
        self.ensure_main_tab(force=True)
        crop, ox, oy = self._crop()
        try:
            lines = read_text(crop, scale=2)
        except Exception as exc:  # noqa: BLE001
            log.warning("서사 수락 항목 OCR 실패: %s", exc)
            return False
        for line in lines:
            raw = str(line.get("text", ""))
            compact = re.sub(r"[^0-9A-Za-z가-힣]", "", raw)
            if not ("서사" in compact and "수락" in compact and "가능" in compact):
                continue
            cy = float(line["y"]) + float(line["h"]) / 2
            if self._badge(crop, cy, float(line.get("x", 0))) != "ok":
                log.info("서사 수락 가능 행은 보이지만 초록 '적합'이 아니므로 건너뜀")
                return False
            x = int(ox + max(float(line["x"]) + 90, 100))
            y = int(oy + cy)
            log.info("트래커 초록 '서사 임무 수락 가능' 클릭 (%d,%d)", x, y)
            self.bot._click_frame("적합 서사 임무 수락 진입", x, y)
            time.sleep(1.2)
            return True
        return False

    def click_daily(self, expected_title="", strict=False, allowed_titles=None):
        """OCR이 ``[일일]`` 머리말을 깨뜨려도 수행형 일일 행을 찾아 클릭한다.

        임무 화면에서 막 수락한 제목이 있으면 그 낱말 일치도를 우선 사용한다. 제목을
        모르는 재시작 직후에는 요귀 처치·비경 탐험·낙화 전장 문구만 일일 후보로 본다.
        allowed_titles가 있으면 헬퍼에서 체크한 제목과 일치하는 행만 선택한다.
        """
        self.ensure_main_tab(force=True)
        expected = re.sub(r"[^0-9A-Za-z가-힣]", "", str(expected_title or ""))
        ignored = {"일일", "요귀", "처치", "참여", "하기"}
        tokens = [t for t in re.findall(r"[가-힣]{2,}", str(expected_title or ""))
                  if t not in ignored]
        allowed = []
        for title in allowed_titles or []:
            key = re.sub(r"[^0-9A-Za-z가-힣]", "", str(title or ""))
            title_tokens = [t for t in re.findall(r"[가-힣]{2,}", str(title or ""))
                            if t not in ignored]
            if key:
                allowed.append((str(title), key, title_tokens))
        step = self.cfg.get("page_notches", 36)
        for expand_attempt in range(2):
            self._to_top()
            collapsed_header = None
            saw_daily_row = False
            for page in range(self.max_pages):
                crop, ox, oy = self._crop()
                try:
                    lines = read_text(crop, scale=2)
                except Exception as exc:  # noqa: BLE001
                    log.warning("일일 트래커 OCR 실패: %s", exc)
                    lines = []
                for line in lines:
                    raw = str(line.get("text", ""))
                    compact = re.sub(r"[^0-9A-Za-z가-힣]", "", raw)
                    token_hits = sum(token in compact for token in tokens)
                    expected_match = bool(expected and (token_hits >= min(2, len(tokens))
                                                         or SequenceMatcher(None, expected, compact).ratio() >= 0.58))
                    allowed_match = None
                    for allowed_title, allowed_key, allowed_tokens in allowed:
                        hits = sum(token in compact for token in allowed_tokens)
                        if (hits >= min(2, len(allowed_tokens))
                                or SequenceMatcher(None, allowed_key, compact).ratio() >= 0.58):
                            allowed_match = allowed_title
                            break
                    # '비경탐험 시간이 남아 있어요' 같은 콘텐츠 알림은 퀘스트 행이 아니다.
                    # 머리말 OCR이 깨진 경우에는 실제 수행 목표만 보조 후보로 허용한다.
                    active_daily = bool(re.search(r"요귀.*처치|낙화.*전장.*(?:요귀|처치|참여)", compact))
                    generic_daily_ui = ("일일임무" in compact)
                    explicit_daily_row = ("일일" in compact and not generic_daily_ui and len(compact) >= 6)
                    if page == 0 and generic_daily_ui and collapsed_header is None:
                        collapsed_header = (
                            int(ox + max(float(line["x"]) + 40, 80)),
                            int(oy + float(line["y"]) + float(line["h"]) / 2),
                        )
                    saw_daily_row = saw_daily_row or active_daily or explicit_daily_row
                    if strict and expected and not expected_match:
                        continue
                    if allowed and not allowed_match:
                        continue
                    if not expected_match and not active_daily and not explicit_daily_row:
                        continue
                    x = int(ox + max(float(line["x"]) + 40, 80))
                    y = int(oy + float(line["y"]) + float(line["h"]) / 2)
                    title = str(expected_title or allowed_match or raw)
                    log.info("트래커 일일 행 확인 클릭: %s (%d,%d)", raw, x, y)
                    self.bot._click_frame("트래커 [일일]", x, y)
                    return title
                dy = self._scroll_measured(-step)
                if dy is not None and dy < 3:
                    break
            if expand_attempt == 0 and collapsed_header and not saw_daily_row:
                self._to_top()
                log.info("접힌 일일 임무 그룹 펼치기")
                self.bot._click_frame("트래커 일일 그룹 펼치기", *collapsed_header)
                time.sleep(1.0)
                continue
            break
        log.info("트래커를 끝까지 확인했지만 수행형 일일 항목을 찾지 못함")
        self._to_top()
        return ""
