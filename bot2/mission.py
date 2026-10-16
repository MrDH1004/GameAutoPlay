"""임무 화면(J)에서 설정된 우선순위로 진행 가능한 퀘스트의 '자동 진행'을 누른다.

메인 퀘스트 트래커 클릭이 통하지 않을 때(막혔을 때)만 부른다. 규칙:
  * 본편/서사/주간은 선택된 퀘스트의 '자동 진행', 일일은 '적합(초록)' 퀘스트를 '수락'한 뒤 '자동 진행'.
  * '위험(빨강)' 퀘스트, '포기', '즉시 완료'는 템플릿 자체가 없어서 절대 누르지 않는다.
  * 누른 뒤 8초 안에 이동/전투가 시작돼야 성공. 실패한 탭은 한동안 건너뛴다.
"""
import logging
import re
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
        configured = cfg.get("priority", ["본편", "서사", "일일", "주간"])
        valid = {name for name, _x in TABS}
        self.priority = [name for name in configured if name in valid]
        self.priority += [name for name, _x in TABS if name not in self.priority]

    def ordered_tabs(self, prefer=None, only=False):
        """설정 우선순위에 따라 탭 좌표를 반환한다. prefer는 즉시 처리할 탭이다."""
        by_name = dict(TABS)
        names = list(self.priority)
        if prefer in by_name:
            names.remove(prefer)
            names.insert(0, prefer)
        if only and prefer in by_name:
            names = [prefer]
        return [(name, by_name[name]) for name in names]

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
        """'자동 진행' 클릭 후 실제 이동/대화가 시작되는지.

        임무 화면을 열기 전부터 싸우던 전투는 새 임무 진행으로 세지 않는다.
        """
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            time.sleep(0.5)
            snap = self.bot.eyes.look(self._grab(), time.monotonic())
            if snap.state in ("MOVING", "DIALOG"):
                return True
        return False

    def _daily_rows(self, frame):
        """일일 목록을 OCR로 읽어 명상 외 실제 수행 임무를 위에서부터 반환한다."""
        from tools.ocr import read_text

        h, w = frame.shape[:2]
        x0, y0 = int(w * 0.115), int(h * 0.19)
        crop = frame[y0:int(h * 0.91), x0:int(w * 0.725)]
        rows = []
        try:
            lines = read_text(crop, scale=1.5)
        except Exception as e:  # noqa: BLE001
            log.warning("일일 임무명 OCR 실패: %s", e)
            return rows
        for line in lines:
            text = re.sub(r"\s+", " ", line["text"]).strip()
            compact = re.sub(r"\s+", "", text)
            if "일일" not in compact or "명상" in compact:
                continue
            cy = y0 + float(line["y"]) + float(line["h"]) / 2
            rows.append((text, cy))
        return rows

    def _reopen_daily(self):
        """후보 하나가 진행되지 않았을 때 일일 목록으로 돌아간다."""
        self._close()
        self.bot._press("임무 다시 열기", "j")
        if not self._wait_title(5.0):
            return False
        self._click_xy("일일 탭", dict(TABS)["일일"], 0.110 * self.bot.window.frame_h, 1.2)
        return True

    def _enter_daily_secret_realm(self):
        """비경 참여 일일은 가장 안전한 1단계에 입장한다."""
        from .startup import is_secret_realm_name, read_map_name

        w, h = self.bot.window.frame_w, self.bot.window.frame_h
        time.sleep(2.0)
        frame = self._grab()
        # 자동 진행이 비경 선택 화면을 열었는지 제목 OCR로 확인한다.
        from .farm import _crop, _ocr
        title = _ocr(_crop(frame, (20, 10, 330, 75)), 2)
        if "비경" not in re.sub(r"\s+", "", title):
            return False
        log.info("  [일일] 비경 탐험 → 안전한 1단계 입장")
        self._click_xy("비경 1단계 선택", 0.31 * w, 0.245 * h, 0.8)
        self._click_xy("비경 입장", 0.92 * w, 0.945 * h, 1.2)
        self._click_xy("비경 입장 확인", 0.557 * w, 0.617 * h, 7.0)
        name = read_map_name(self._grab())
        if is_secret_realm_name(name):
            log.info("  [일일] 비경 입장 확인: %s", name)
            return True
        log.warning("  [일일] 비경 입장 후 지도 확인 실패: %s", name or "읽기 실패")
        return False

    def _enter_nakhwa_battlefield(self):
        """메뉴의 낙화의 전장 2채널에 입장하고 내부 사냥 구역으로 이동한다.

        입장에는 일반 금화 12,500이 사용된다. 영석 등 유료 재화는 사용하지 않는다.
        """
        from .farm import _crop, _ocr
        from .startup import read_map_name

        w, h = self.bot.window.frame_w, self.bot.window.frame_h
        self._close()
        self._click_xy("메뉴 열기", 0.974 * w, 0.041 * h, 1.0)
        self._click_xy("낙화의 전장 선택", 0.770 * w, 0.773 * h, 1.5)

        title = re.sub(r"\s+", "", _ocr(_crop(self._grab(), (20, 10, 350, 75)), 2))
        if "낙화의전장" not in title:
            log.warning("  [일일] 낙화의 전장 메뉴 진입 확인 실패: %s", title or "읽기 실패")
            return False

        self._click_xy("입장 구역 선택", 0.917 * w, 0.944 * h, 1.0)
        self._click_xy("낙화의 전장 2채널", 0.500 * w, 0.452 * h, 0.5)
        self._click_xy("낙화의 전장 입장(일반 금화)", 0.520 * w, 0.683 * h, 8.0)

        name = read_map_name(self._grab())
        if "낙화의전장" not in re.sub(r"\s+", "", name):
            log.warning("  [일일] 낙화의 전장 입장 후 지도 확인 실패: %s", name or "읽기 실패")
            return False

        # 오른쪽 입구 안전지대에는 몬스터가 없다. 북쪽의 3점 요귀 군집까지 이동한다.
        for step in range(6):
            self._click_xy(f"낙화 사냥 구역 이동 {step + 1}/6", 0.52 * w, 0.17 * h, 1.4)

        snap = self.bot.eyes.look(self._grab(), time.monotonic())
        if snap.auto_on:
            self.bot._press("낙화 자동 사냥 재시작(끄기)", "t")
            time.sleep(0.8)
        self.bot._press("낙화 자동 사냥 켜기", "t")
        time.sleep(2.0)
        log.info("  [일일] 낙화의 전장 2채널 입장 및 사냥 구역 이동 완료")
        return True

    def read_nakhwa_progress(self):
        """전장 안에서 일일 탭의 처치 수를 읽고 즉시 필드로 돌아간다."""
        from .farm import _crop, _ocr

        self.bot._press("낙화 일일 진행도 확인", "j")
        if not self._wait_title(5.0):
            return None
        try:
            self._click_xy("일일 탭", dict(TABS)["일일"], 0.110 * self.bot.window.frame_h, 1.0)
            frame = self._grab()
            rows = self._daily_rows(frame)
            if self._nakhwa_row_completed(rows):
                log.info("  [일일] 낙화 임무가 일일 목록에서 사라짐 → 100/100 완료")
                return 100
            text = _ocr(_crop(frame, (1390, 500, 1870, 640)), 2)
            progress = self._parse_nakhwa_progress(text)
            if progress is None:
                log.warning("  [일일] 낙화 처치 수 OCR 실패: %s", re.sub(r"\s+", " ", text).strip())
                return None
            log.info("  [일일] 낙화의 전장 요귀 처치: %d/100", progress)
            return progress
        finally:
            self._close()

    @staticmethod
    def _parse_nakhwa_progress(text):
        match = re.search(r"(\d{1,3})\s*/\s*100", str(text or ""))
        return min(100, int(match.group(1))) if match else None

    @staticmethod
    def _nakhwa_row_completed(rows):
        """읽힌 일일이 있는데 낙화 행만 사라졌다면 방금 처치 임무가 완료된 것이다."""
        return bool(rows) and not any("낙화" in re.sub(r"\s+", "", title) for title, _y in rows)

    def leave_nakhwa_battlefield(self):
        """완료된 낙화 일일을 빠져나와 다음 일일을 진행할 수 있게 한다."""
        from .farm import _crop, _ocr
        from .startup import read_map_name

        w, h = self.bot.window.frame_w, self.bot.window.frame_h
        self._click_xy("낙화의 전장 나가기", 0.025 * w, 0.190 * h, 1.5)
        frame = self._grab()
        dialog = re.sub(r"\s+", "", _ocr(_crop(frame, (650, 300, 1270, 780)), 2))
        if "나가기" in dialog or "확인" in dialog:
            self._click_xy("낙화의 전장 나가기 확인", 0.550 * w, 0.579 * h, 7.0)
        else:
            time.sleep(6.0)
        name = read_map_name(self._grab())
        if "낙화의전장" in re.sub(r"\s+", "", name):
            log.warning("  [일일] 낙화의 전장 나가기 확인 실패: %s", name)
            return False
        log.info("  [일일] 낙화의 전장 완료 후 일반 지도 복귀: %s", name or "지도명 읽기 대기")
        return True

    def _daily_guide_spot(self, title):
        """퀘스트 제목과 맞는 가장 안전한 길라잡이 사냥터를 반환한다."""
        if not self.cfg.get("daily_guide_fallback", True):
            return None
        from .farm import guide_spots
        compact = re.sub(r"\s+", "", title)
        aliases = (("안개기슭", "안개 기슭"), ("황무지마을", "황무지 마을"),
                   ("우물동굴", "우물 동굴"), ("음산한폐광", "음산한 폐광"),
                   ("독기지대", "독기 지대"))
        for key, region in aliases:
            if key not in compact:
                continue
            return next((s for s in guide_spots() if s["name"].startswith(region)), None)
        return None

    def _route_daily_by_guide(self, title):
        """자동 진행 경로가 막히면 무료 길라잡이로 해당 일일 지역에 진입한다."""
        spot = self._daily_guide_spot(title)
        farm = getattr(self.bot, "farm", None)
        if not spot or not farm:
            return False
        log.info("  [일일] 자동 이동 없음 → 길라잡이 무료 이동: %s", spot["name"])
        if not farm.guide.travel(spot):
            return False
        snap = self.bot.eyes.look(self._grab(), time.monotonic())
        if not snap.auto_on:
            self.bot._press("일일 사냥 자동 사냥 켜기", "t")
        return True

    def _try_daily(self):
        """명상은 자연 누적시키고, 수락한 실제 일일을 순서대로 시도한다."""
        rows = self._daily_rows(self._grab())
        if not rows:
            log.info("  [일일] 명상 외 수행 임무를 읽지 못함")
            return False
        log.info("  [일일] 수행 후보: %s", " → ".join(text for text, _y in rows[:5]))
        w = self.bot.window.frame_w
        for index, (title, y) in enumerate(rows):
            self._click_xy(f"일일 후보 선택: {title}", 0.36 * w, y, 0.8)
            frame = self._grab()
            auto = self._find(frame, "auto")
            if auto:
                if "낙화의전장" in re.sub(r"\s+", "", title):
                    log.info("  [일일] '%s' → 메뉴의 낙화의 전장으로 입장", title)
                    if self._enter_nakhwa_battlefield():
                        return True
                    if index + 1 < len(rows) and self._reopen_daily():
                        continue
                    break
                log.info("  [일일] '%s' 자동 진행", title)
                self._click_xy("자동 진행", auto.x, auto.y, 1.0)
                if "비경" in title and self._enter_daily_secret_realm():
                    return True
                if self._on_screen():
                    self._close()
                if self._progressing():
                    return True
                if self._route_daily_by_guide(title):
                    return True
                log.info("  [일일] '%s' 진행 없음 → 다음 후보", title)
            else:
                accept = self._find(frame, "accept")
                badges = self.bot.matcher.find_all(frame, self.t["ok_badge"], self.regions.get("list"),
                                                    self.cfg.get("badge_threshold", 0.85))
                safe = any(abs(float(b.y) - y) <= 32 for b in badges)
                if accept and safe:
                    log.info("  [일일] 안전 적합 임무 수락: %s", title)
                    self._click_xy("수락", accept.x, accept.y, 1.2)
                    end = time.monotonic() + 5.0
                    auto = None
                    while time.monotonic() < end:
                        auto = self._find(self._grab(), "auto")
                        if auto:
                            break
                        time.sleep(0.5)
                    if auto:
                        self._click_xy("자동 진행", auto.x, auto.y, 1.0)
                        if self._progressing():
                            return True
                        if self._route_daily_by_guide(title):
                            return True
                    else:
                        log.info("  [일일] '%s' 수락 입력 후 자동 진행 버튼이 나타나지 않음", title)
                else:
                    log.info("  [일일] 미수락 또는 안전 확인 불가: %s", title)
            if index + 1 < len(rows) and not self._reopen_daily():
                break
        return False

    def _stop_autohunt(self):
        """일일 자동 진행이 기존 자리 전투에 붙잡히지 않도록 자동 사냥을 끈다."""
        snap = self.bot.eyes.look(self._grab(), time.monotonic())
        if not snap.auto_on:
            return
        log.info("일일 임무 이동 전 자동 사냥 끄기")
        self.bot._press("자동 사냥 끄기", "t")
        end = time.monotonic() + 4.0
        while time.monotonic() < end:
            time.sleep(0.5)
            if not self.bot.eyes.look(self._grab(), time.monotonic()).auto_on:
                return

    # ---------- 탭 하나 처리 ----------
    def _try_tab(self, name, tab_x):
        w, h = self.bot.window.frame_w, self.bot.window.frame_h
        self._click_xy(f"{name} 탭", tab_x, 0.110 * h, 1.2)

        if name == "일일":
            return self._try_daily()

        tried_rows = 0
        selected_ok = False
        picked_safe_daily = False
        for _ in range(5):
            frame = self._grab()

            # 일일 탭을 열면 이미 수락한 첫 임무가 선택되어 있다. 이 임무의
            # 자동 진행을 먼저 사용해야 낙화의 전장/비경 같은 진행 중 일일을
            # 끝낼 수 있다. 적합 배지 탐색은 수락 임무가 없을 때의 보충 동작이다.
            auto = self._find(frame, "auto")
            if auto:
                log.info("  [%s] 이미 수락한 임무 자동 진행 클릭", name)
                self._click_xy("자동 진행", auto.x, auto.y, 1.5)
                if self._on_screen():
                    self._close()
                if self._progressing():
                    return True
                log.info("  [%s] 자동 진행했지만 이동·대화가 시작되지 않음", name)
                return False

            # 수락된 임무가 없으면 녹색 '적합' 전투 임무를 고른다.
            if name == "일일" and not picked_safe_daily:
                rows = self.bot.matcher.find_all(frame, self.t["ok_badge"], self.regions.get("list"),
                                                 self.cfg.get("badge_threshold", 0.85))
                if rows:
                    preferred = min(int(self.cfg.get("daily_ok_index", 1)), len(rows) - 1)
                    row = rows[preferred]
                    tried_rows = preferred + 1
                    picked_safe_daily = True
                    self._click_xy(f"{name} 적합 퀘스트 선택", 0.36 * w, row.y, 1.0)
                    selected_ok = True
                    continue
                picked_safe_daily = True

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
    def run(self, now, prefer=None, only=False):
        """필드에서 호출. 성공(진행 시작)하면 True. 끝나면 임무 화면은 닫혀 있어야 한다."""
        self.next_ok = now + self.cfg.get("cooldown", 90)
        if self.bot.dry_run:
            log.info("[dry-run] 임무 화면 확인 시점 (J 키가 필요해 dry-run에서는 건너뜀)")
            return False
        tabs = self.ordered_tabs(prefer, only)
        if tabs and tabs[0][0] == "일일":
            self._stop_autohunt()
        log.info("임무 화면에서 진행할 퀘스트 확인 시작")
        self.bot._press("임무 열기", "j")
        if not self._wait_title(5.0):
            log.warning("임무 화면이 열리지 않음")
            self._close()
            return False

        ok = False
        try:
            for name, x in tabs:
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
