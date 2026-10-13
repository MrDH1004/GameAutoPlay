"""영기 수련 자동 진행.

일정 간격마다: ☰ 메뉴 → 영기 수련 → (진행 중이면 바로 나감) → 비어 있는(0/1) 노드 클릭 → 수련 → 필드로 복귀.
"""
import logging
import time

log = logging.getLogger(__name__)


def parse_remaining(frame, px, py):
    """모래시계 아이콘 (px, py) 주변 글자에서 남은 시간(초)."""
    import re
    try:
        from bot2.farm import _binarize, _ocr
    except ImportError:
        return None
    h, w = frame.shape[:2]
    x0, x1 = max(0, int(px - 0.04 * w)), min(w, int(px + 0.04 * w))
    y0, y1 = max(0, int(py - 0.01 * h)), min(h, int(py + 0.05 * h))
    crop = frame[y0:y1, x0:x1]
    for img, sc in ((crop, 3), (_binarize(crop, 170), 3), (crop, 2)):
        t = _ocr(img, sc).replace(" ", "")
        hh = re.search(r"(\d+)시간", t)
        mm = re.search(r"(\d+)분", t)
        ss = re.search(r"(\d+)초", t)
        if hh or mm or ss:
            return (int(hh.group(1)) * 3600 if hh else 0) + (int(mm.group(1)) * 60 if mm else 0) +                 (int(ss.group(1)) if ss else 0)
    return None


class YeonggiTask:
    def __init__(self, bot, cfg):
        self.bot = bot
        self.cfg = cfg
        self.interval = cfg.get("interval", 180)
        self.next_check = 0.0       # 시작 직후 한 번 확인
        self.t = cfg["templates"]
        self.regions = cfg.get("regions", {})

    # ---------- 도우미 ----------
    def _find(self, frame, key, threshold=None):
        return self.bot.matcher.find(frame, self.t[key], self.regions.get(key), threshold)

    def _wait_for(self, key, timeout=5.0):
        """템플릿이 나타날 때까지 기다렸다가 (frame, match) 반환. 실패하면 (frame, None)."""
        end = time.monotonic() + timeout
        frame = None
        while time.monotonic() < end:
            frame = self.bot.window.grab()
            match = self._find(frame, key)
            if match:
                return frame, match
            time.sleep(0.3)
        return frame, None

    def _click(self, label, match, pause=1.0):
        self.bot._click_frame(label, match.x, match.y)
        time.sleep(pause)

    def _wait_popup(self, timeout=4.0):
        """노드를 누른 뒤 팝업이 뜰 때까지 기다린다: '수련' 버튼이나 '수련 불가'가 보이면 (frame, train, impossible).
        끝까지 아무것도 안 보이면 마지막 frame과 (None, None)."""
        end = time.monotonic() + timeout
        frame = None
        while time.monotonic() < end:
            frame = self.bot.window.grab()
            train = self._find(frame, "train")
            impossible = self._find(frame, "impossible")
            if train or impossible:
                return frame, train, impossible
            time.sleep(0.3)
        return frame, None, None

    def _close_popup(self, frame):
        """팝업(수련 정보/불가 안내)을 닫는다. 닫혔는지 확인하고 남아 있으면 한 번 더."""
        for _ in range(3):
            match = self._find(frame, "close")
            if match:
                self._click("팝업 닫기", match, 0.8)
            else:
                self.bot._press("팝업 닫기", "esc")
                time.sleep(0.8)
            frame = self.bot.window.grab()
            if not self._find(frame, "close") and not self._find(frame, "train"):
                return

    def _leave(self):
        """영기 수련 화면에서 필드로 복귀."""
        frame = self.bot.window.grab()
        if self.bot._in_field(frame):
            return                      # 이미 필드 (게임이 화면을 닫은 경우 등)
        back = self._find(frame, "back")
        if back:
            self._click("영기 수련 나가기", back, 1.5)
        else:
            self.bot._press("영기 수련 나가기", "esc")
            time.sleep(1.5)
        if not self.bot._in_field(self.bot.window.grab()):
            self.bot._press("메뉴 닫기", "esc")
            time.sleep(1.0)

    # ---------- 본체 ----------
    def due(self, now):
        return now >= self.next_check

    def run(self, now):
        """필드 화면에서 호출된다. 끝나면 필드로 돌아온 상태여야 한다."""
        self.next_check = now + self.interval
        if self.bot.dry_run:
            log.info("[dry-run] 영기 수련 확인 시점 (메뉴를 열어야 해서 dry-run에서는 건너뜀)")
            return

        log.info("영기 수련 확인 시작")
        try:
            frame = self._open()
            if frame is not None:
                self._start_next(frame)
        except Exception:
            # 사용자 조작으로 중단되면 화면은 사용자에게 맡기고 바로 빠진다
            self.next_check = time.monotonic() + self.cfg.get("retry_delay", 30)
            raise
        if frame is None:
            self.next_check = time.monotonic() + self.cfg.get("retry_delay", 30)
            log.info("  %d초 뒤 다시 시도", self.cfg.get("retry_delay", 30))
        self._leave()

    def _open(self):
        """☰ → 영기 수련 화면을 연다. 성공하면 그 화면의 frame, 실패하면 None."""
        frame = self.bot.window.grab()
        menu = self._find(frame, "menu_button")
        if not menu:
            log.warning("☰ 메뉴 버튼을 찾지 못함")
            return None
        self._click("☰ 메뉴", menu, 0.3)

        frame, entry = self._wait_for("menu_entry", 5.0)       # 메뉴가 늦게 뜨는 경우를 위해 여유
        if not entry:
            log.warning("메뉴에서 '영기 수련'을 찾지 못함 (전투 등으로 메뉴가 닫혔을 수 있음)")
            return None
        self._click("영기 수련", entry, 0.3)

        frame, title = self._wait_for("title", 5.0)
        if not title:
            log.warning("영기 수련 화면이 열리지 않음 (전투 등으로 닫혔을 수 있음)")
            return None
        time.sleep(0.5)                 # 화면 전환 애니메이션
        return self.bot.window.grab()

    def _remaining(self, frame, prog):
        """진행 표시(모래시계) 아래의 남은 시간('9분 57초', '1시간 20분')을 초로. 못 읽으면 None."""
        return parse_remaining(frame, prog.x, prog.y)

    def _start_next(self, frame):
        prog = self._find(frame, "progress")
        if prog:
            left = self._remaining(frame, prog)
            if left:
                wait = max(self.interval, min(left + 15, 6 * 3600))
                self.next_check = time.monotonic() + wait
                log.info("영기 수련 진행 중 (남은 시간 %d분) → %d분 뒤 다시 확인", left // 60, wait // 60)
            else:
                log.info("영기 수련 진행 중 → %d초 뒤 다시 확인", self.interval)
            return

        # 노드 우상단에 빨간 점이 있는 노드만 수련할 수 있다 (점이 없으면 조건 미달/개방 대기)
        badges = self.bot.matcher.find_all(frame, self.t["node_ready"], self.regions.get("node_ready"),
                                           self.cfg.get("ready_threshold", 0.8))
        nodes = [type("N", (), {"x": b.x - 140, "y": b.y + 38})() for b in badges]   # 점 → 노드 몸통 좌표
        if not nodes:
            log.info("수련 가능한(빨간 점) 노드가 화면에 없음")
            return
        log.info("수련 가능한 노드 %d개 발견", len(nodes))

        for node in nodes[: self.cfg.get("max_tries", 6)]:
            self._click(f"노드 ({node.x},{node.y})", node, 0.6)
            frame, train, impossible = self._wait_popup(4.0)

            if impossible and not train:
                log.info("  수련 불가 노드 → 닫고 다음 노드")
                self._close_popup(frame)
                continue

            if not train:
                log.info("  수련 버튼 없음(팝업이 4초 안에 안 뜸) → 닫고 다음 노드")
                self.bot._save_screen(frame, "yeonggi_nobtn")
                self._close_popup(frame)
                continue

            self._click("수련", train, 1.5)
            frame = self.bot.window.grab()
            if self._find(frame, "impossible"):
                log.info("  수련 불가 (재료 부족 등) → 닫고 다음 노드")
                self._close_popup(frame)
                continue
            self._close_popup(frame)

            frame = self.bot.window.grab()
            if self._find(frame, "progress"):
                log.info("영기 수련 시작 성공")
            else:
                log.warning("수련을 눌렀지만 진행 표시가 보이지 않음. 화면 확인 필요")
                self.bot._save_screen(frame, "yeonggi")
            return

        log.warning("시도한 노드 모두 수련 불가")
