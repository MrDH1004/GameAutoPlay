"""임무 화면(J)에서 설정된 우선순위로 진행 가능한 퀘스트의 '자동 진행'을 누른다.

메인 퀘스트 트래커 클릭이 통하지 않을 때(막혔을 때)만 부른다. 규칙:
  * 본편/서사/주간은 선택된 퀘스트의 '자동 진행', 일일은 '적합(초록)' 퀘스트를 '수락'한 뒤 '자동 진행'.
  * '위험(빨강)' 퀘스트, '포기', '즉시 완료'는 템플릿 자체가 없어서 절대 누르지 않는다.
  * 누른 뒤 8초 안에 이동/전투가 시작돼야 성공. 실패한 탭은 한동안 건너뛴다.
"""
import logging
import re
import time
from datetime import date
from difflib import SequenceMatcher

log = logging.getLogger(__name__)

TABS = [("본편", 140), ("서사", 331), ("일일", 520), ("주간", 709)]


class MissionTask:
    def __init__(self, bot, cfg):
        self.bot = bot
        self.cfg = cfg
        self.t = cfg["templates"]
        self.regions = cfg.get("regions", {})
        self.skip_seconds = cfg.get("skip_seconds", 600)    # 실패한 탭을 건너뛰는 시간
        self.story_retry_seconds = float(cfg.get("story_retry_seconds", 15))
        self.tab_blocked_until = {}
        self.next_ok = 0.0
        self.last_tab = None            # 마지막으로 진행을 시작한 탭
        self.last_daily_title = ""      # 수락 직후 트래커에서 다시 선택할 실제 일일 제목
        self.selected_daily_titles = None  # None=기존 자동 선택, 목록이면 헬퍼에서 체크한 일일만 진행
        self._daily_intake_checked = False  # 실행 후 일일 수락 상태 확인은 한 번만, 완료 뒤 다음 선택 항목 때만 재확인
        self._daily_batch_pending = False   # 체크 목록을 일일 탭에서 먼저 일괄 수락
        self._daily_quota_done_day = self._load_daily_quota_done_day()
        configured = cfg.get("priority", ["본편", "서사", "일일", "주간"])
        valid = {name for name, _x in TABS}
        self.priority = [name for name in configured if name in valid]
        self.priority += [name for name, _x in TABS if name not in self.priority]

    @staticmethod
    def _load_daily_quota_done_day():
        """재시작해도 오늘 확인한 5/5 수락 완료 상태를 유지한다."""
        try:
            from .rewards import _load_state
            value = _load_state().get("daily_quota_done_day")
            return date.fromisoformat(value) if value else None
        except (TypeError, ValueError):
            return None

    def _remember_daily_quota_done(self):
        from .rewards import _load_state, _save_state
        today = date.today()
        self._daily_quota_done_day = today
        state = _load_state()
        state["daily_quota_done_day"] = today.isoformat()
        _save_state(state)

    def daily_quota_reached(self):
        """오늘 받은 일일 임무 개수가 5/5인지 반환한다.

        이미 수락된 임무는 이 상태와 관계없이 계속 진행할 수 있다.
        """
        return getattr(self, "_daily_quota_done_day", None) == date.today()

    def ordered_tabs(self, prefer=None, only=False, force_daily=False):
        """설정 우선순위에 따라 탭 좌표를 반환한다. prefer는 즉시 처리할 탭이다."""
        by_name = dict(TABS)
        names = list(self.priority)
        if prefer in by_name:
            names.remove(prefer)
            names.insert(0, prefer)
        if only and prefer in by_name:
            names = [prefer]
        checked = bool(getattr(self, "_daily_intake_checked", False))
        selected = getattr(self, "selected_daily_titles", None)
        if (not force_daily and
                ((selected is not None and checked)
                 or (selected is None and self.daily_quota_reached()))):
            names = [name for name in names if name != "일일"]
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

    def _suitable_rows(self, frame):
        """현재 UI 크기/광원 차이를 허용해 초록색 '적합' 배지를 찾는다."""
        threshold = self.cfg.get("story_badge_threshold",
                                 self.cfg.get("badge_threshold", 0.85))
        return self.bot.matcher.find_all(frame, self.t["ok_badge"],
                                         self.regions.get("list"), threshold)

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

    def accept_selected_suitable_story(self):
        """트래커의 초록 적합 행으로 들어온 서사를 수락하고 자동 진행한다."""
        if not self._wait_title(4.0):
            log.info("적합 서사 행 클릭 뒤 임무 화면이 열리지 않음")
            return False
        try:
            frame = self._grab()
            auto = self._find(frame, "auto")
            if auto:
                log.info("[서사] 이미 수락된 적합 임무 자동 진행")
                self._click_xy("서사 자동 진행", auto.x, auto.y, 1.0)
                return self._progressing()
            accept = self._find(frame, "accept")
            if not accept:
                log.info("[서사] 적합 임무 수락 버튼을 찾지 못함")
                return False
            log.info("[서사] 트래커에서 확인한 적합 임무 자동 수락")
            self._click_xy("적합 서사 수락", accept.x, accept.y, 1.0)
            end = time.monotonic() + 5.0
            while time.monotonic() < end:
                auto = self._find(self._grab(), "auto")
                if auto:
                    self._click_xy("서사 자동 진행", auto.x, auto.y, 1.0)
                    return self._progressing()
                time.sleep(0.4)
            log.info("[서사] 수락 뒤 자동 진행 버튼이 나타나지 않음")
            return False
        finally:
            self._close()

    def _daily_rows(self, frame, include_meditation=False):
        """일일 목록을 OCR로 읽는다. 명상은 목록 조회나 명시 선택 때만 포함한다."""
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
            if "일일" not in compact or ("명상" in compact and not include_meditation):
                continue
            cy = y0 + float(line["y"]) + float(line["h"]) / 2
            rows.append((text, cy))
        return rows

    @staticmethod
    def _daily_title_key(title):
        return re.sub(r"[^0-9A-Za-z가-힣]", "", str(title or ""))

    def _is_selected_daily(self, title):
        """OCR 공백·기호 차이를 허용하면서 사용자가 고른 일일인지 확인한다."""
        selected = getattr(self, "selected_daily_titles", None)
        if selected is None:
            return True
        key = self._daily_title_key(title)
        for wanted in selected:
            wanted_key = self._daily_title_key(wanted)
            if key == wanted_key or (key and wanted_key and
                    SequenceMatcher(None, key, wanted_key).ratio() >= 0.72):
                return True
        return False

    def set_selected_daily_titles(self, titles):
        """헬퍼 체크 목록을 이후 일일 탐색의 허용 목록으로 설정한다."""
        unique = []
        seen = set()
        for title in titles or []:
            title = re.sub(r"\s+", " ", str(title)).strip()
            key = self._daily_title_key(title)
            if title and key not in seen:
                seen.add(key)
                unique.append(title)
        self.selected_daily_titles = unique
        self.last_daily_title = unique[0] if unique else ""
        self._daily_intake_checked = False
        self._daily_batch_pending = bool(unique)

    @classmethod
    def _matching_daily_row(cls, rows, wanted):
        """저장 제목과 OCR 제목이 조금 달라도 같은 일일 행을 찾는다."""
        wanted_key = cls._daily_title_key(wanted)
        candidates = []
        for title, y in rows:
            key = cls._daily_title_key(title)
            ratio = SequenceMatcher(None, key, wanted_key).ratio() if key and wanted_key else 0.0
            if key == wanted_key:
                return title, y
            candidates.append((ratio, title, y))
        if candidates:
            ratio, title, y = max(candidates)
            if ratio >= 0.72:
                return title, y
        return None

    def accept_selected_daily_batch(self):
        """헬퍼에서 체크한 일일을 게임 일일 탭에서 먼저 모두 수락한다.

        이미 수락된 행은 건너뛰며, 5/5 제한에 도달하면 추가 수락을 멈춘다.
        실제 진행 버튼은 누르지 않고 수락 단계만 끝낸 뒤 기존 진행 루프에 맡긴다.
        """
        selected = list(getattr(self, "selected_daily_titles", None) or [])
        result = {"accepted": 0, "already": 0, "missing": 0, "quota_full": False}
        if not selected:
            return result
        self._close()
        self._stop_autohunt()
        try:
            for wanted in selected:
                if not self._reopen_daily():
                    result["missing"] += 1
                    continue
                list_frame = self._grab()
                rows = self._daily_rows(list_frame, include_meditation=True)
                target = self._matching_daily_row(rows, wanted)
                if not target:
                    log.info("  [일일 일괄수락] 목록에서 찾지 못함: %s", wanted)
                    result["missing"] += 1
                    continue
                title, y = target
                self._click_xy(f"일괄수락 후보 선택: {title}",
                               0.36 * self.bot.window.frame_w, y, 0.7)
                frame = self._grab()
                if self._find(frame, "auto"):
                    log.info("  [일일 일괄수락] 기존 수락 확인: %s", title)
                    result["already"] += 1
                    continue
                accept = self._find(frame, "accept")
                badges = self.bot.matcher.find_all(
                    frame, self.t["ok_badge"], self.regions.get("list"),
                    self.cfg.get("badge_threshold", 0.85))
                safe = any(abs(float(b.y) - y) <= 32 for b in badges)
                # 헬퍼에서 사용자가 직접 체크한 행은 적합 배지 템플릿이 순간적으로
                # 흔들려도 수락한다. 제목을 다시 대조했으므로 다른 행을 누르지 않는다.
                explicit_selected = self._is_selected_daily(title)
                if not accept or not (safe or explicit_selected):
                    log.info("  [일일 일괄수락] 수락 버튼 또는 적합 확인 실패: %s", title)
                    result["missing"] += 1
                    continue
                if self._daily_quota_full(frame):
                    self._remember_daily_quota_done()
                    result["quota_full"] = True
                    log.info("  [일일 일괄수락] 받은 임무 5/5 → 추가 수락 종료")
                    break
                self._click_xy(f"일일 일괄 수락: {title}", accept.x, accept.y, 1.0)
                end = time.monotonic() + 5.0
                accepted = False
                while time.monotonic() < end:
                    current = self._grab()
                    if self._daily_limit_popup(current):
                        self._remember_daily_quota_done()
                        result["quota_full"] = True
                        break
                    if self._find(current, "auto"):
                        accepted = True
                        break
                    time.sleep(0.4)
                if result["quota_full"]:
                    break
                if accepted:
                    result["accepted"] += 1
                    log.info("  [일일 일괄수락] 수락 완료: %s", title)
                else:
                    # 수락 뒤 선택이 풀리는 UI도 있으므로 다음 재진입에서 확정한다.
                    result["accepted"] += 1
                    log.info("  [일일 일괄수락] 수락 입력 완료: %s", title)
            return result
        finally:
            self._close()
            self._daily_intake_checked = False
            self._daily_batch_pending = False
            self.last_daily_title = ""

    @staticmethod
    def _daily_row_accepted(frame, y):
        """행 오른쪽 말풍선 아이콘으로 이미 수락한 일일인지 판별한다."""
        import numpy as np

        h, w = frame.shape[:2]
        y0, y1 = max(0, int(y - 28)), min(h, int(y + 28))
        # 말풍선은 행 우측 약 91~94%에 있고, 97% 부근은 선택 체크박스다.
        # 체크박스를 수락 표시로 오인하면 모든 미수락 행을 건너뛰게 된다.
        x0, x1 = int(w * 0.895), int(w * 0.950)
        area = frame[y0:y1, x0:x1]
        if not area.size:
            return False
        lo = area.min(axis=2)
        hi = area.max(axis=2)
        white = (lo >= 175) & ((hi.astype(np.int16) - lo.astype(np.int16)) <= 45)
        return int(np.count_nonzero(white)) >= 90

    def scan_daily_quests(self):
        """게임의 일일 탭을 열어 자동 수행 가능한 퀘스트 제목을 반환한다."""
        self._close()
        self.bot._press("일일 목록 불러오기", "j")
        if not self._wait_title(5.0):
            log.warning("일일 목록 불러오기: 임무 화면이 열리지 않음")
            self._close()
            return []
        try:
            self._click_xy("일일 탭", dict(TABS)["일일"],
                           0.110 * self.bot.window.frame_h, 1.2)
            rows = self._daily_rows(self._grab(), include_meditation=True)
            titles = []
            seen = set()
            for title, _y in rows:
                key = self._daily_title_key(title)
                if key and key not in seen:
                    seen.add(key)
                    titles.append(title)
            log.info("일일 목록 불러오기 완료: %d개", len(titles))
            return titles
        finally:
            self._close()

    def _reopen_daily(self):
        """후보 하나가 진행되지 않았을 때 일일 목록으로 돌아간다."""
        self._close()
        self.bot._press("임무 다시 열기", "j")
        if not self._wait_title(5.0):
            return False
        self._click_xy("일일 탭", dict(TABS)["일일"], 0.110 * self.bot.window.frame_h, 1.2)
        return True

    def _enter_daily_secret_realm(self):
        """비경 참여 일일은 망자의 마을 3단계에만 입장한다."""
        from .startup import is_secret_realm_name, read_map_name
        from tools.ocr import read_text

        w, h = self.bot.window.frame_w, self.bot.window.frame_h
        time.sleep(2.0)
        frame = self._grab()
        # 자동 진행이 비경 선택 화면을 열었는지 제목 OCR로 확인한다.
        from .farm import _crop, _ocr
        title = _ocr(_crop(frame, (20, 10, 330, 75)), 2)
        if "비경" not in re.sub(r"\s+", "", title):
            return False
        def find_text_center(image, *words):
            try:
                lines = read_text(image, scale=1.5)
            except Exception as exc:  # noqa: BLE001
                log.warning("  [일일] 비경 선택 OCR 실패: %s", exc)
                return None
            wanted = [re.sub(r"\s+", "", word) for word in words]
            for line in lines:
                text = re.sub(r"\s+", "", str(line.get("text", "")))
                if all(word in text for word in wanted):
                    return (float(line["x"]) + float(line["w"]) / 2,
                            float(line["y"]) + float(line["h"]) / 2)
            return None

        dungeon = find_text_center(frame, "망자", "마을")
        if dungeon is None:
            log.warning("  [일일] 망자의 마을을 찾지 못해 비경 입장을 보류")
            return False
        self._click_xy("비경 망자의 마을 선택", *dungeon, 1.0)

        stage = find_text_center(self._grab(), "3단계")
        if stage is None:
            log.warning("  [일일] 망자의 마을 3단계를 찾지 못해 비경 입장을 보류")
            return False
        log.info("  [일일] 비경 탐험 → 망자의 마을 3단계 입장")
        self._click_xy("망자의 마을 3단계 선택", *stage, 0.8)
        self._click_xy("비경 입장", 0.92 * w, 0.945 * h, 1.2)
        self._click_xy("비경 입장 확인", 0.557 * w, 0.617 * h, 7.0)
        name = read_map_name(self._grab())
        if is_secret_realm_name(name):
            log.info("  [일일] 비경 입장 확인: %s", name)
            return True
        log.warning("  [일일] 비경 입장 후 지도 확인 실패: %s", name or "읽기 실패")
        return False

    def enter_accepted_daily_secret_realm(self):
        """5/5 뒤 추적 중인 비경 일일도 임무 목록에서 직접 열어 3단계에 입장한다."""
        if not self._reopen_daily():
            return False
        rows = self._daily_rows(self._grab())
        target = next(((title, y) for title, y in rows if "비경" in re.sub(r"\s+", "", title)), None)
        if not target:
            log.warning("  [일일] 일일 목록에서 수락된 비경 탐험 임무를 찾지 못함")
            self._close()
            return False
        title, y = target
        self._click_xy(f"수락된 비경 일일 선택: {title}", 0.36 * self.bot.window.frame_w, y, 0.8)
        auto = self._find(self._grab(), "auto")
        if not auto:
            log.warning("  [일일] 수락된 비경 탐험의 자동 진행 버튼을 찾지 못함")
            self._close()
            return False
        self._click_xy("비경 일일 자동 진행", auto.x, auto.y, 1.0)
        return self._enter_daily_secret_realm()

    def _enter_nakhwa_battlefield(self, reason="일일"):
        """메뉴의 낙화의 전장 일반 채널에 입장하고 안전한 현재 위치에서 사냥한다.

        입장에는 일반 금화 12,500이 사용된다. 영석 등 유료 재화는 사용하지 않는다.
        """
        from .farm import _crop, _ocr
        from .startup import read_map_name

        w, h = self.bot.window.frame_w, self.bot.window.frame_h
        # 이전 입장 시도가 중간 메뉴에서 실패했을 수 있다. 열린 메뉴에서 다시 메뉴
        # 버튼을 누르면 오히려 닫히므로, 매 시도 전에 실제 필드까지 정규화한다.
        for _ in range(3):
            current = self.bot.eyes.look(self._grab(), time.monotonic())
            if current.in_field and current.state != "OVERLAY":
                break
            self.bot._press("낙화 입장 재시도 전 열린 화면 닫기", "esc")
            time.sleep(1.0)
        self._close()
        self._click_xy("메뉴 열기", 0.974 * w, 0.041 * h, 1.0)
        self._click_xy("낙화의 전장 선택", 0.770 * w, 0.773 * h, 1.5)

        title = re.sub(r"\s+", "", _ocr(_crop(self._grab(), (20, 10, 350, 75)), 2))
        if "낙화의전장" not in title:
            log.warning("  [일일] 낙화의 전장 메뉴 진입 확인 실패: %s", title or "읽기 실패")
            return False

        self._click_xy("입장 구역 선택", 0.917 * w, 0.944 * h, 1.0)
        self._click_xy("낙화의 전장 입장 가능 채널", 0.500 * w, 0.452 * h, 0.5)
        self._click_xy("낙화의 전장 입장(일반 금화)", 0.520 * w, 0.683 * h, 8.0)

        name = read_map_name(self._grab())
        if "낙화의전장" not in re.sub(r"\s+", "", name):
            log.warning("  [일일] 낙화의 전장 입장 후 지도 확인 실패: %s", name or "읽기 실패")
            return False

        return self.recover_nakhwa_center(reason)

    def recover_nakhwa_center(self, reason="부활"):
        """낙화 전용 미니맵의 현재 안전 위치에서 점수표·프리셋 B·AUTO를 확인한다."""
        w, h = self.bot.window.frame_w, self.bot.window.frame_h
        boss_cfg = self.bot.cfg.get("nakhwa_boss") or {}
        if not boss_cfg.get("stay_at_current_position", True):
            target = self._nakhwa_move_target(
                boss_cfg, reason, getattr(self.bot, "_source", ""))
            if not self._walk_nakhwa_map_target(target, boss_cfg):
                return False
        else:
            log.info("  [%s] 낙화 전용 미니맵 현재 안전 위치 유지 (중앙 이동 안 함)", reason)

        # 오른쪽 낙화 전용 임무 탭을 펼쳐야 점수표가 표시된다. 최초 진입과
        # 부활 때만 열어 주어 반복 호출이 탭을 다시 닫지 않게 한다.
        opened = bool(getattr(self.bot, "_nakhwa_scoreboard_opened", False))
        if not opened or "부활" in str(reason):
            score_tab = boss_cfg.get("scoreboard_tab", [0.974, 0.142])
            self._click_xy("낙화 전용 임무 점수표 열기",
                           float(score_tab[0]) * w, float(score_tab[1]) * h, 0.8)
            self.bot._nakhwa_scoreboard_opened = True

        # 낙화 몬스터는 강하므로 사용자가 지정한 단일 저격 프리셋 B를 AUTO보다 먼저 적용한다.
        preset_no = max(1, min(3, int(boss_cfg.get("hunt_preset", 2))))
        try:
            from .preset import PresetSwitcher
            switcher = getattr(self.bot, "preset", None)
            if switcher is None:
                switcher = PresetSwitcher(self.bot, {
                    "normal": 1, "boss": preset_no,
                    "boss_seconds": 3, "normal_seconds": 60,
                })
            if not switcher.switch(preset_no):
                log.warning("  [%s] 낙화 프리셋 B 전환 실패 → AUTO 시작 보류", reason)
                return False
            log.info("  [%s] 낙화 단일 저격 프리셋 B 적용 완료", reason)
        except Exception as exc:  # noqa: BLE001
            log.warning("  [%s] 낙화 프리셋 B 전환 오류: %s", reason, exc)
            return False

        # Unity가 백그라운드 T 키를 버리는 경우가 있어 실제 AUTO 버튼을 직접 누르고 재확인한다.
        auto_button = boss_cfg.get("auto_button", [0.887, 0.942])
        for attempt in range(3):
            snap = self.bot.eyes.look(self._grab(), time.monotonic())
            if snap.auto_on:
                log.info("  [%s] 낙화의 전장 현재 위치 점수표 및 AUTO 확인", reason)
                return True
            self._click_xy(f"낙화 AUTO 버튼 {attempt + 1}/3",
                           float(auto_button[0]) * w, float(auto_button[1]) * h, 1.5)
        snap = self.bot.eyes.look(self._grab(), time.monotonic())
        if not snap.auto_on:
            log.warning("  [%s] 낙화의 전장 입장 후 AUTO 활성화 확인 실패", reason)
            return False
        log.info("  [%s] 낙화의 전장 현재 위치 점수표 및 AUTO 확인", reason)
        return True

    def _walk_nakhwa_map_target(self, target, boss_cfg):
        """세계지도를 열어 지정 지점을 고르고 무료 ``걸어가기``로 이동한다."""
        from .collect import _matches, _tpl
        from .evade import MAP_TITLE_BOX, WALK_BTN_BOX

        w, h = self.bot.window.frame_w, self.bot.window.frame_h
        frame = self._grab()
        map_title = _tpl("world_map_title.png")
        if not _matches(frame, map_title, MAP_TITLE_BOX, 0.82):
            direct_target = boss_cfg.get(
                "direct_minimap_target", boss_cfg.get("map_open_target", [0.208, 0.174]))
            self._click_xy("낙화 전용 미니맵 사냥 위치 선택",
                           float(direct_target[0]) * w,
                           float(direct_target[1]) * h, 1.5)
            frame = self._grab()
        if not _matches(frame, map_title, MAP_TITLE_BOX, 0.82):
            # 낙화의 전용 미니맵은 세계지도 창을 띄우지 않고 지도 안 지점을
            # 누르는 즉시 그 위치로 자동 이동한다. 실제 이동 상태가 잡히면
            # 세계지도 제목/걸어가기 버튼이 없어도 정상 이동으로 처리한다.
            moving_seen = False
            for _ in range(6):
                snap = self.bot.eyes.look(self._grab(), time.monotonic())
                moving_seen = bool(snap.walking or snap.state == "MOVING")
                if moving_seen:
                    break
                time.sleep(0.5)
            if moving_seen:
                deadline = time.monotonic() + float(
                    boss_cfg.get("center_move_wait_seconds", 90))
                quiet = 0
                while time.monotonic() < deadline:
                    time.sleep(1.0)
                    snap = self.bot.eyes.look(self._grab(), time.monotonic())
                    moving = bool(snap.walking or snap.state == "MOVING")
                    quiet = quiet + 1 if not moving else 0
                    if quiet >= 3:
                        log.info("  [낙화] 전용 미니맵 직접 이동 완료 확인")
                        return True
                log.warning("  [낙화] 전용 미니맵 직접 이동 완료를 확인하지 못함")
                return False
            # 이미 선택 지점에 도착해 있으면 이동 애니메이션이 생기지 않는다.
            # 낙화 안에서는 이 클릭 자체가 전용 미니맵 목적지 지정이므로 성공으로 본다.
            log.info("  [낙화] 전용 미니맵 사냥 지점 선택 완료 (현재 위치 또는 즉시 도착)")
            return True

        self._click_xy("낙화 지도 녹색 사냥 지점 선택",
                       float(target[0]) * w, float(target[1]) * h, 1.0)
        selected = self._grab()
        walk_tpl = _tpl("world_walk_btn.png")
        hit = _matches(selected, walk_tpl, WALK_BTN_BOX, 0.80)
        if not hit:
            log.warning("  [낙화] 녹색 사냥 지점 선택 후 무료 '걸어가기' 버튼을 찾지 못함")
            self.bot._press("낙화 지도 닫기", "esc")
            return False

        self._click_xy("낙화 녹색 사냥 지점 걸어가기",
                       hit[0][0] + walk_tpl.shape[1] // 2,
                       hit[0][1] + walk_tpl.shape[0] // 2, 1.0)

        deadline = time.monotonic() + float(boss_cfg.get("center_move_wait_seconds", 90))
        moving_seen = False
        quiet = 0
        while time.monotonic() < deadline:
            time.sleep(1.0)
            snap = self.bot.eyes.look(self._grab(), time.monotonic())
            moving = bool(snap.walking or snap.state == "MOVING")
            moving_seen = moving_seen or moving
            quiet = quiet + 1 if not moving else 0
            if moving_seen and quiet >= 2:
                log.info("  [낙화] 지도 녹색 사냥 지점 도착 확인")
                return True
        log.warning("  [낙화] 녹색 사냥 지점 이동 완료를 확인하지 못함")
        return False

    @staticmethod
    def _nakhwa_move_target(boss_cfg, reason, source=""):
        """낙화 입장 후 세계지도에서 사용할 지정 사냥 지점을 반환한다."""
        if "요화선녀" in str(reason) or "요화선녀" in str(source):
            return boss_cfg.get("boss_move_target", [0.504, 0.509])
        return boss_cfg.get("center_move_target", [0.504, 0.509])

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
            score_tab = (self.bot.cfg.get("nakhwa_boss") or {}).get(
                "scoreboard_tab", [0.974, 0.114])
            self._click_xy("낙화 진행도 확인 후 우측 임무 패널 복원",
                           float(score_tab[0]) * self.bot.window.frame_w,
                           float(score_tab[1]) * self.bot.window.frame_h, 0.5)

    @staticmethod
    def _parse_nakhwa_progress(text):
        match = re.search(r"(\d{1,3})\s*/\s*100", str(text or ""))
        return min(100, int(match.group(1))) if match else None

    @staticmethod
    def _nakhwa_row_completed(rows):
        """읽힌 일일이 있는데 낙화 행만 사라졌다면 방금 처치 임무가 완료된 것이다."""
        return bool(rows) and not any("낙화" in re.sub(r"\s+", "", title) for title, _y in rows)

    @staticmethod
    def _parse_daily_quota(text):
        match = re.search(r"(\d+)\s*/\s*(\d+)", str(text or ""))
        return (int(match.group(1)), int(match.group(2))) if match else None

    def _daily_quota_full(self, frame):
        """오늘 받은 일일 임무 개수가 5/5인지 확인한다. 0/5면 새로 수락한다."""
        from .farm import _binarize, _crop, _ocr
        h, w = frame.shape[:2]
        # 오른쪽 '연속 진행' 스위치의 0이 붙어 5/5를 5/50으로 읽지 않도록
        # 완료 개수 문구까지만 자르고 원본·이진화 OCR을 교차 확인한다.
        quota_box = (int(w * 0.25), int(h * 0.88), int(w * 0.62), h)
        crop = _crop(frame, quota_box)
        for image in (crop, _binarize(crop, 150), _binarize(crop, 170), _binarize(crop, 190)):
            quota = self._parse_daily_quota(_ocr(image, 2))
            if quota and quota[1] > 0 and quota[0] >= quota[1]:
                return True
        return False

    def _daily_limit_popup(self, frame):
        """수락 뒤 표시되는 '완료 제한 개수 도달' 안내도 5/5로 처리한다."""
        from .farm import _binarize, _crop, _ocr
        h, w = frame.shape[:2]
        crop = _crop(frame, (int(w * 0.35), int(h * 0.16), int(w * 0.67), int(h * 0.28)))
        for image in (crop, _binarize(crop, 180)):
            text = re.sub(r"\s+", "", _ocr(image, 2))
            if "완료제한개수" in text and ("수락" in text or "등록" in text):
                return True
        return False

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

    def leave_instance_for_common_event(self, event_name):
        """미니맵 좌측 하단 나가기 버튼으로 현재 인스턴스를 종료한다."""
        from .farm import _crop, _ocr

        w, h = self.bot.window.frame_w, self.bot.window.frame_h
        self._click_xy(f"{event_name} 우선: 인스턴스 나가기", 0.025 * w, 0.190 * h, 1.5)
        frame = self._grab()
        dialog = re.sub(r"\s+", "", _ocr(
            _crop(frame, (int(w * 0.34), int(h * 0.27), int(w * 0.67), int(h * 0.73))), 2))
        if "나가기" in dialog or "확인" in dialog:
            self._click_xy(f"{event_name} 우선: 나가기 확인", 0.550 * w, 0.579 * h, 3.0)
        else:
            # 확인창 없이 바로 나가는 인스턴스도 있어 장면 전환만 기다린다.
            time.sleep(3.0)
        log.warning("공통 이벤트 최우선: %s 참가를 위해 인스턴스 나가기 실행", event_name)
        return True

    def leave_daily_secret_realm(self):
        """망자의 마을 일일 체류 시간이 끝나면 미니맵 나가기로 일반 맵에 복귀한다."""
        from .farm import _crop, _ocr

        w, h = self.bot.window.frame_w, self.bot.window.frame_h
        self._click_xy("일일 망자의 마을 60분 완료: 나가기", 0.025 * w, 0.190 * h, 1.5)
        frame = self._grab()
        dialog = re.sub(r"\s+", "", _ocr(
            _crop(frame, (int(w * 0.34), int(h * 0.27), int(w * 0.67), int(h * 0.73))), 2))
        if "나가기" in dialog or "확인" in dialog:
            self._click_xy("일일 망자의 마을 나가기 확인", 0.550 * w, 0.579 * h, 3.0)
        else:
            time.sleep(3.0)
        return True

    def _daily_guide_spot(self, title):
        """퀘스트 제목과 맞는 가장 안전한 길라잡이 사냥터를 반환한다."""
        if not self.cfg.get("daily_guide_fallback", True):
            return None
        from .farm import guide_spots
        compact = re.sub(r"\s+", "", title)
        aliases = (("산신의영역지하동굴", "산신의 영역 지하 동굴"),
                   ("안개기슭", "안개 기슭"), ("황무지마을", "황무지 마을"),
                   ("우물동굴", "우물 동굴"), ("음산한폐광", "음산한 폐광"),
                   ("독기지대", "독기 지대"))
        for key, region in aliases:
            if key not in compact:
                continue
            return next((s for s in guide_spots() if s["name"].startswith(region)), None)
        return None

    @staticmethod
    def _prefer_direct_guide(title):
        """자동 진행보다 검증된 무료 길라잡이 포탈을 먼저 쓸 일일 임무."""
        return "산신의영역지하동굴" in re.sub(r"\s+", "", str(title or ""))

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
        if (getattr(self, "_daily_batch_pending", False)
                and getattr(self, "selected_daily_titles", None)):
            intake = self.accept_selected_daily_batch()
            log.info("  [일일] 시작 전 일괄수락 완료: 신규 %d · 기존 %d · 누락 %d%s",
                     intake["accepted"], intake["already"], intake["missing"],
                     " · 5/5" if intake["quota_full"] else "")
        self._daily_intake_checked = True
        self._close()
        self._stop_autohunt()
        if not self._reopen_daily():
            return False
        list_frame = self._grab()
        quota_exhausted = self._daily_quota_full(list_frame)
        if quota_exhausted:
            self._remember_daily_quota_done()
            log.info("  [일일] 받은 임무 5/5 → 우측 수락 아이콘이 있는 임무만 진행")
        selected_titles = getattr(self, "selected_daily_titles", None)
        include_meditation = bool(selected_titles and any(
            "명상" in self._daily_title_key(title) for title in selected_titles))
        rows = self._daily_rows(list_frame, include_meditation=include_meditation)
        if getattr(self, "selected_daily_titles", None) is not None:
            rows = [(title, y) for title, y in rows if self._is_selected_daily(title)]
        # 우측 아이콘은 목록 폭·상세 패널 상태에 따라 좌표가 달라진다.
        # 수락 여부는 행을 연 뒤의 '자동 진행/수락' 버튼으로만 확정한다.
        if not rows:
            self.last_daily_title = ""
            log.info("  [일일] 선택한 수행 임무가 목록에 없음")
            return False
        log.info("  [일일] 수행 후보: %s", " → ".join(text for text, _y in rows[:5]))
        w = self.bot.window.frame_w
        self.last_daily_title = ""
        for index, (title, y) in enumerate(rows):
            self.last_daily_title = title
            self._click_xy(f"일일 후보 선택: {title}", 0.36 * w, y, 0.8)
            frame = self._grab()
            auto = self._find(frame, "auto")
            if auto:
                log.info("  [일일] 기존 수락 확인: %s", title)
                if "낙화의전장" in re.sub(r"\s+", "", title):
                    log.info("  [일일] '%s' → 메뉴의 낙화의 전장으로 입장", title)
                    if self._enter_nakhwa_battlefield():
                        return True
                    if index + 1 < len(rows) and self._reopen_daily():
                        continue
                    break
                if self._prefer_direct_guide(title):
                    log.info("  [일일] '%s' → 산신의 영역 지하 동굴 무료 포탈 우선", title)
                    self._close()
                    if self._route_daily_by_guide(title):
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
                explicit_selected = bool(
                    selected_titles is not None and self._is_selected_daily(title))
                if accept and (safe or explicit_selected):
                    if quota_exhausted or self._daily_quota_full(frame):
                        log.info("  [일일] 받은 임무 5/5 → 미수락 일일은 다음 초기화 뒤 진행")
                        if index + 1 < len(rows) and self._reopen_daily():
                            continue
                        return False
                    log.info("  [일일] 미수락 확인 → 수락: %s", title)
                    self._click_xy("수락", accept.x, accept.y, 1.2)
                    end = time.monotonic() + 5.0
                    auto = None
                    while time.monotonic() < end:
                        current = self._grab()
                        if self._daily_limit_popup(current):
                            self._remember_daily_quota_done()
                            log.info("  [일일] 완료 제한 안내 감지 → 오늘 일일 임무 확인 종료")
                            return False
                        auto = self._find(current, "auto")
                        if auto:
                            break
                        time.sleep(0.5)
                    if auto:
                        if self._prefer_direct_guide(title):
                            log.info("  [일일] '%s' 수락 완료 → 산신의 영역 지하 동굴 무료 포탈 우선", title)
                            self._close()
                            if self._route_daily_by_guide(title):
                                return True
                            if index + 1 < len(rows) and self._reopen_daily():
                                continue
                            break
                        self._click_xy("자동 진행", auto.x, auto.y, 1.0)
                        if self._progressing():
                            return True
                        if self._route_daily_by_guide(title):
                            return True
                    else:
                        # 게임이 수락 뒤 목록 선택을 풀거나 트래커만 갱신하는 경우가 있다.
                        # 수락 자체를 실패로 처리하면 일일 탭이 차단되고 선택 모드가
                        # 즉시 끝나므로, 다음 짧은 주기에 트래커/임무 목록에서 재확인한다.
                        log.info("  [일일] '%s' 수락 입력 완료 → 자동 진행 버튼은 다음 주기에 재확인", title)
                        return True
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
    def _try_tab(self, name, tab_x, safe_only=False):
        w, h = self.bot.window.frame_w, self.bot.window.frame_h
        self._click_xy(f"{name} 탭", tab_x, 0.110 * h, 1.2)

        if name == "일일":
            return self._try_daily()

        tried_rows = 0
        selected_ok = False
        picked_safe_daily = False
        for _ in range(5):
            frame = self._grab()

            # 서사 전용 모드는 현재 선택된 항목을 그대로 실행하지 않는다. 목록에서
            # 게임이 현재 전투력에 맞다고 표시한 초록색 '적합' 행을 먼저 골라야 한다.
            if safe_only and not selected_ok:
                rows = self._suitable_rows(frame)
                if tried_rows >= len(rows) or tried_rows >= 3:
                    log.info("  [%s] 현재 전투력에 맞는 '적합' 퀘스트 없음", name)
                    return False
                row = rows[tried_rows]
                tried_rows += 1
                self._click_xy(f"{name} 적합 퀘스트 선택", 0.36 * w, row.y, 1.0)
                selected_ok = True
                continue

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
                rows = self._suitable_rows(frame)
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
            if accept and (name == "일일" or safe_only) and selected_ok:
                # 방금 우리가 '적합' 배지 행을 골랐을 때만 수락한다 (기본 선택 행이나 '위험' 행은 수락하지 않는다)
                log.info("  [%s] 적합 퀘스트 수락", name)
                self._click_xy("수락", accept.x, accept.y, 1.0)
                selected_ok = bool(safe_only)
                # 수락 직후엔 버튼이 바뀌는 데 시간이 걸린다: '자동 진행'이 뜰 때까지 기다리고, 다른 행을 또 수락하지 않는다
                end = time.monotonic() + 5.0
                while time.monotonic() < end and not self._find(self._grab(), "auto"):
                    time.sleep(0.5)
                if not self._find(self._grab(), "auto"):
                    log.info("  [%s] 수락 후 '자동 진행' 버튼이 나타나지 않음", name)
                    return False
                continue

            # 선택된 행이 부적합하거나 버튼이 없음 → 다음 '적합' 행을 선택
            rows = self._suitable_rows(frame)
            if tried_rows >= len(rows) or tried_rows >= 3:
                log.info("  [%s] 진행 가능한 '적합' 퀘스트 없음", name)
                return False
            row = rows[tried_rows]
            tried_rows += 1
            self._click_xy(f"{name} 퀘스트 선택", 0.36 * w, row.y, 1.0)
            selected_ok = True
        return False

    # ---------- 본체 ----------
    def run(self, now, prefer=None, only=False, safe_only=False, force_daily=False):
        if (getattr(self.bot, "normal_mode", False)
                or getattr(self.bot, "_normal_mode_request", None) is True):
            log.debug("일반 모드 또는 전환 요청 감지 → 임무 창 열기 차단")
            return False
        """필드에서 호출. 성공(진행 시작)하면 True. 끝나면 임무 화면은 닫혀 있어야 한다."""
        self.next_ok = now + self.cfg.get("cooldown", 90)
        if self.bot.dry_run:
            log.info("[dry-run] 임무 화면 확인 시점 (J 키가 필요해 dry-run에서는 건너뜀)")
            return False
        tabs = self.ordered_tabs(prefer, only, force_daily=force_daily)
        if not tabs:
            log.debug("일일 수락 상태를 이미 확인했거나 남은 수락 가능 횟수가 없어 임무 창 생략")
            return False
        log.info("임무 화면에서 진행할 퀘스트 확인 시작")
        self.bot._press("임무 열기", "j")
        if not self._wait_title(5.0):
            log.warning("임무 화면이 열리지 않음")
            self._close()
            return False

        if (getattr(self.bot, "normal_mode", False)
                or getattr(self.bot, "_normal_mode_request", None) is True):
            log.info("일반 모드 전환 요청 → 열린 임무 창을 닫고 확인 중단")
            self._close()
            return False

        ok = False
        try:
            for name, x in tabs:
                if self.is_blocked(name):
                    log.info("[%s] 탭은 차단 중이라 건너뜀", name)
                    continue
                log.info("[%s] 탭 확인", name)
                if self._try_tab(name, x, safe_only=safe_only):
                    log.info("[%s] 진행 시작", name)
                    ok = True
                    self.last_tab = name
                    break
                # 서사 전용은 순간적인 배지 인식 실패 때문에 장시간 멈추지 않는다.
                retry = self.story_retry_seconds if safe_only else self.skip_seconds
                self.tab_blocked_until[name] = time.monotonic() + retry
                if safe_only:
                    self.next_ok = time.monotonic() + retry
        finally:
            self._close()
        if not ok:
            log.warning("임무 화면에서 진행할 수 있는 퀘스트가 없음")
        return ok
