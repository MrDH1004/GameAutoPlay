"""임무 화면(J)에서 본편 → 서사 → 일일 → 주간 순으로 진행 가능한 퀘스트의 '자동 진행'을 누른다.

메인 퀘스트 트래커 클릭이 통하지 않을 때(막혔을 때)만 부른다. 규칙:
  * 본편/서사/주간은 선택된 퀘스트의 '자동 진행', 일일은 '적합(초록)' 퀘스트를 '수락'한 뒤 '자동 진행'.
  * '위험(빨강)' 퀘스트, '포기', '즉시 완료'는 템플릿 자체가 없어서 절대 누르지 않는다.
  * 누른 뒤 8초 안에 이동/전투가 시작돼야 성공. 실패한 탭은 한동안 건너뛴다.
"""
import logging
import time

log = logging.getLogger(__name__)

TABS = [("본편", 140), ("서사", 331), ("일일", 520), ("주간", 709)]


class MissionTask:
    def __init__(self, bot, cfg):
        self.bot = bot
        self.cfg = cfg
        self.t = cfg["templates"]
        self.regions = cfg.get("regions", {})
        self.skip_seconds = cfg.get("skip_seconds", 600)    # 실패한 탭을 건너뛰는 시간
        self.tab_blocked_until = {}
        self.next_ok = 0.0
        self.last_tab = None            # 마지막으로 진행을 시작한 탭

    def block(self, name, seconds):
        """이 탭의 퀘스트를 seconds초 동안 진행하지 않는다 (예: 진행하다 사망한 퀘스트)."""
        self.tab_blocked_until[name] = time.monotonic() + seconds

    def is_blocked(self, name):
        return time.monotonic() < self.tab_blocked_until.get(name, 0)

    # ---------- 도우미 ----------
    def _grab(self):
        return self.bot.window.grab()

    def _find(self, frame, key, region_key=None, threshold=None):
        return self.bot.matcher.find(frame, self.t[key], self.regions.get(region_key or key), threshold)

    def _on_screen(self):
        return bool(self._find(self._grab(), "title"))

    def _wait_title(self, timeout=4.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self._on_screen():
                return True
            time.sleep(0.3)
        return False

    def _click_xy(self, label, x, y, pause=1.0):
        self.bot._click_frame(label, x, y)
        time.sleep(pause)

    def _close(self):
        """임무 화면에서 필드로 복귀 (ESC)."""
        for _ in range(3):
            if not self._on_screen():
                return
            self.bot._press("임무 닫기", "esc")
            time.sleep(1.2)

    def _progressing(self, timeout=8.0):
        """'자동 진행' 클릭 후 이동/전투/대화가 시작되는지."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            time.sleep(0.5)
            snap = self.bot.eyes.look(self._grab(), time.monotonic())
            if snap.state in ("MOVING", "COMBAT", "DIALOG"):
                return True
        return False

    # ---------- 탭 하나 처리 ----------
    def _try_tab(self, name, tab_x):
        w, h = self.bot.window.frame_w, self.bot.window.frame_h
        self._click_xy(f"{name} 탭", tab_x, 0.110 * h, 1.2)

        tried_rows = 0
        selected_ok = False
        for _ in range(5):
            frame = self._grab()
            auto = self._find(frame, "auto")
            if auto:
                log.info("  [%s] 자동 진행 클릭", name)
                self._click_xy("자동 진행", auto.x, auto.y, 1.5)
                if self._on_screen():                 # 화면이 안 닫힘 → 팝업/불가
                    self._close()
                if self._progressing():
                    return True
                log.info("  [%s] 자동 진행했지만 진행 없음", name)
                return False

            accept = self._find(frame, "accept")
            if accept and name == "일일" and selected_ok:
                # 방금 우리가 '적합' 배지 행을 골랐을 때만 수락한다 (기본 선택 행이나 '위험' 행은 수락하지 않는다)
                log.info("  [%s] 적합 퀘스트 수락", name)
                self._click_xy("수락", accept.x, accept.y, 1.0)
                selected_ok = False
                # 수락 직후엔 버튼이 바뀌는 데 시간이 걸린다: '자동 진행'이 뜰 때까지 기다리고, 다른 행을 또 수락하지 않는다
                end = time.monotonic() + 5.0
                while time.monotonic() < end and not self._find(self._grab(), "auto"):
                    time.sleep(0.5)
                if not self._find(self._grab(), "auto"):
                    log.info("  [%s] 수락 후 '자동 진행' 버튼이 나타나지 않음", name)
                    return False
                continue

            # 선택된 행이 부적합하거나 버튼이 없음 → 다음 '적합' 행을 선택
            rows = self.bot.matcher.find_all(frame, self.t["ok_badge"], self.regions.get("list"),
                                             self.cfg.get("badge_threshold", 0.85))
            if tried_rows >= len(rows) or tried_rows >= 3:
                log.info("  [%s] 진행 가능한 '적합' 퀘스트 없음", name)
                return False
            row = rows[tried_rows]
            tried_rows += 1
            self._click_xy(f"{name} 퀘스트 선택", 0.36 * w, row.y, 1.0)
            selected_ok = True
        return False

    # ---------- 본체 ----------
    def run(self, now):
        """필드에서 호출. 성공(진행 시작)하면 True. 끝나면 임무 화면은 닫혀 있어야 한다."""
        self.next_ok = now + self.cfg.get("cooldown", 90)
        if self.bot.dry_run:
            log.info("[dry-run] 임무 화면 확인 시점 (J 키가 필요해 dry-run에서는 건너뜀)")
            return False
        log.info("임무 화면에서 진행할 퀘스트 확인 시작")
        self.bot._press("임무 열기", "j")
        if not self._wait_title(5.0):
            log.warning("임무 화면이 열리지 않음")
            self._close()
            return False

        ok = False
        try:
            for name, x in TABS:
                if self.is_blocked(name):
                    log.info("[%s] 탭은 차단 중이라 건너뜀", name)
                    continue
                log.info("[%s] 탭 확인", name)
                if self._try_tab(name, x):
                    log.info("[%s] 진행 시작", name)
                    ok = True
                    self.last_tab = name
                    break
                self.tab_blocked_until[name] = time.monotonic() + self.skip_seconds
        finally:
            self._close()
        if not ok:
            log.warning("임무 화면에서 진행할 수 있는 퀘스트가 없음")
        return ok
