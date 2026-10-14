"""주기적으로 공짜 보상을 받는다: 우편함 '모두 받기', 업적 탭별 '모두 받기', 길라잡이 사냥터 미션 보상.

모두 '받기' 버튼만 누른다 (구매/강화/소모 버튼은 누르지 않는다). 받을 게 없으면 버튼을 눌러도 아무 일도 없다.
좌표는 1920x1080 기준 (Driver가 실제 화면에 맞춘다).
"""
import json
import logging
import os
import re
from collections import Counter

import cv2
import numpy as np

from .farm import ROOT, GuideNav, StopRequested, _binarize, _crop, _ocr

log = logging.getLogger(__name__)

MAIL_ICON = (1716, 42)
MAIL_TABS = [(111, 119), (273, 119), (435, 119), (596, 119)]     # 서신 / 시스템 / 문파 / 개인
MAIL_CLAIM = (1760, 1024)
MENU_ICON = (1870, 44)
MENU_ACHV = (1674, 570)
ACHV_TABS = [(120, 448), (120, 522), (120, 596), (120, 669), (120, 743)]   # 성장 / 전투 / 도전 / 생활 / 탐험
ACHV_CLAIM = (1768, 1001)
BACK = (48, 44)
POPUP_DISMISS = (960, 600)          # '보상받기' 연출 창은 화면을 누르면 닫힌다


def _tpl(name):
    return cv2.imdecode(np.fromfile(os.path.join(ROOT, "templates", name), dtype=np.uint8), cv2.IMREAD_COLOR)


def _seen(img, tpl, box, thr=0.8):
    area = _crop(img, box)
    if area.shape[0] < tpl.shape[0] or area.shape[1] < tpl.shape[1]:
        return False
    _, mx, _, _ = cv2.minMaxLoc(cv2.matchTemplate(area, tpl, cv2.TM_CCOEFF_NORMED))
    return mx > thr


STATE_FILE = os.path.join(ROOT, "logs", "state.json")


def read_skill_count(frame):
    """업적 '성장' 탭의 '도술을 N개 습득해 보세요' 진행도에서 지금 배운 도술 수. 못 찾으면 None."""
    from tools.ocr import read_text
    try:
        lines = read_text(_crop(frame, (380, 160, 1400, 930)), scale=1.5)
    except Exception:  # noqa: BLE001
        return None
    row = next((l for l in lines if re.search(r"도술을\s*\d+\s*개", l["text"])), None)
    if not row:
        return None
    y = int(160 + row["y"] + 36)                     # 설명 줄 바로 아래 진행 막대의 'n/m'
    crop = _crop(frame, (830, y - 16, 960, y + 14))
    votes = Counter()
    for img, sc in ((crop, 2), (crop, 3), (_binarize(crop, 170), 3), (_binarize(crop, 200), 3)):
        m = re.search(r"(\d+)\s*[/!]\s*(\d+)", _ocr(img, sc))
        if m:
            votes[int(m.group(1))] += 1
    return votes.most_common(1)[0][0] if votes else None


def _load_state():
    from storage import load_json
    data, warning = load_json(STATE_FILE)
    if warning:
        log.warning("상태 복구: %s", warning)
    return data


def _save_state(st):
    from storage import save_json
    save_json(STATE_FILE, st)


class RewardTask:
    def __init__(self, drv, cfg):
        self.drv = drv
        self.cfg = cfg or {}
        self.interval = float(self.cfg.get("interval_minutes", 60)) * 60
        self.next_at = 0.0
        self.guide = GuideNav(drv)
        self.t_mail = _tpl("mail_title.png")
        self.t_achv = _tpl("achv_title.png")
        self.count = 0
        self.on_new_skill = None        # 콜백(이전 수, 지금 수)

    def due(self, now):
        return now >= self.next_at

    def _wait(self, tpl, tries=6):
        for _ in range(tries):
            self.drv.sleep(0.6)
            if _seen(self.drv.shot(), tpl, (0, 0, 400, 90)):
                return True
        return False

    def mail(self):
        self.drv.click(*MAIL_ICON)
        if not self._wait(self.t_mail):
            log.warning("보상: 우편함이 열리지 않음")
            return False
        for xy in MAIL_TABS:
            self.drv.click(*xy)
            self.drv.sleep(0.8)
            self.drv.click(*MAIL_CLAIM)
            self.drv.sleep(1.5)
            self.drv.click(*POPUP_DISMISS)
            self.drv.sleep(0.6)
        self.drv.click(*BACK)
        self.drv.sleep(1.0)
        return True

    def achievements(self):
        self.drv.click(*MENU_ICON)
        self.drv.sleep(1.2)
        self.drv.click(*MENU_ACHV)
        if not self._wait(self.t_achv):
            log.warning("보상: 업적 화면이 열리지 않음")
            if not _seen(self.drv.shot(), self.t_achv, (0, 0, 400, 90)):
                self.drv.key("esc")                 # 메뉴만 열린 상태면 닫는다
            return False
        for i, xy in enumerate(ACHV_TABS):
            self.drv.click(*xy)
            self.drv.sleep(0.9)
            self.drv.click(*ACHV_CLAIM)
            self.drv.sleep(1.5)
            self.drv.click(*POPUP_DISMISS)
            self.drv.sleep(0.6)
            if i == 0:
                self._check_skills()
        self.drv.click(*BACK)
        self.drv.sleep(1.0)
        return True

    def _check_skills(self):
        """새로 배운 도술이 있으면 알린다 (업적 '도술 익히기' 진행도 비교)."""
        n = read_skill_count(self.drv.shot())
        if n is None:
            return
        st = _load_state()
        before = st.get("skills")
        if before is not None and n > before:
            log.warning("새 도술 %d개 습득 (%d → %d) → 스킬 추천/프리셋 재검토 권장", n - before, before, n)
            if self.on_new_skill:
                self.on_new_skill(before, n)
        st["skills"] = n
        _save_state(st)

    def run(self, now, power=None):
        """한 번 돌린다. 끝나면 필드로 돌아와 있어야 한다."""
        self.next_at = now + self.interval
        steps = [n for k, n in (("mail", "우편함"), ("achievements", "업적"), ("guide", "길라잡이")) if self.cfg.get(k, k != "mail")]
        log.info("보상 받기 시작 (%s)", " → ".join(steps))
        done = []
        try:
            if self.cfg.get("mail", False) and self.mail():
                done.append("우편함")
            if self.cfg.get("achievements", True) and self.achievements():
                done.append("업적")
            if self.cfg.get("guide", True) and power:
                n = self.guide.claim_rewards(power)
                done.append(f"길라잡이 {n}개")
        except StopRequested:
            return
        self.count += 1
        log.info("보상 받기 끝: %s", ", ".join(done) or "없음")
