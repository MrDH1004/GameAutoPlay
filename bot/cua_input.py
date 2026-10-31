"""Cua Driver based, non-interfering input backend for the game window."""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import threading
import time
from ctypes import wintypes
from pathlib import Path

from app_paths import RESOURCE_ROOT, ROOT


class CuaInputError(RuntimeError):
    pass


def find_executable(configured: str | None = None) -> str:
    # 배포본은 Cua 실행 파일 세트를 _internal/cua-driver에 함께 넣는다.
    # 설치된 전역 버전보다 빌드와 함께 검증된 내장 버전을 우선 사용한다.
    bundled = Path(RESOURCE_ROOT) / "cua-driver" / "cua-driver.exe"
    candidates = [configured, str(bundled), shutil.which("cua-driver")]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(str(Path(local) / "Programs" / "Cua" / "cua-driver" / "bin" / "cua-driver.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate))
    raise CuaInputError("Cua Driver 실행 파일을 찾지 못했습니다")


class CuaInput:
    """Routes helper input to one exact HWND through Cua background delivery."""

    def __init__(self, window, config=None):
        config = config or {}
        self.window = window
        self.executable = find_executable(config.get("executable"))
        self.session = str(config.get("session", "dokkaebi-helper"))
        self.timeout = float(config.get("timeout", 12))
        self.cursor_motion_ms = max(1, int(config.get("cursor_motion_ms", 1)))
        self.capture_enabled = bool(config.get("capture_enabled", False))
        self._lock = threading.RLock()
        self._capture_dir = Path(ROOT) / "runs" / "cua-helper"
        self._capture_dir.mkdir(parents=True, exist_ok=True)
        # 강제 재시작 교체 순간 구·신 헬퍼가 같은 파일을 덮어쓰지 않도록 PID별로 분리한다.
        self._capture_file = self._capture_dir / f"latest-{os.getpid()}.png"
        self._closed = False

    @staticmethod
    def _failure_detail(result):
        """Return the actionable Cua failure without its large observation."""
        if not isinstance(result, dict):
            return "알 수 없는 오류"
        failed_step = result.get("failed_step")
        steps = result.get("steps") or []
        step = None
        if isinstance(failed_step, int) and 0 <= failed_step < len(steps):
            step = steps[failed_step]
        elif steps:
            step = next((item for item in steps if item.get("ok") is False), None)
        if isinstance(step, dict):
            for key in ("message", "error", "detail", "reason"):
                value = step.get(key)
                if value:
                    return f"step {failed_step}: {value}"
        for key in ("message", "error", "detail", "reason"):
            value = result.get(key)
            if value:
                return str(value)
        compact = {
            "executed": result.get("executed"),
            "failed_step": failed_step,
            "step": step,
        }
        return json.dumps(compact, ensure_ascii=False, default=str)[:700]

    def _call(self, tool, payload):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        for attempt in range(2):
            try:
                proc = subprocess.run(
                    [self.executable, tool], input=json.dumps(payload, ensure_ascii=False),
                    text=True, encoding="utf-8", errors="replace", capture_output=True,
                    timeout=self.timeout, creationflags=flags, check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise CuaInputError(f"Cua Driver {tool} 호출 실패: {exc}") from exc
            raw_detail = (proc.stderr or proc.stdout).strip()
            try:
                result = json.loads(proc.stdout)
            except (json.JSONDecodeError, TypeError):
                result = None
            if not proc.returncode:
                break
            detail = self._failure_detail(result) if result is not None else raw_detail
            if attempt == 0 and tool != "start_session" and "session has ended" in detail.lower():
                self._start_session()
                continue
            raise CuaInputError(f"Cua Driver {tool} 실패: {detail[:700]}")
        if result is None:
            raise CuaInputError(f"Cua Driver {tool} 응답 해석 실패")
        if result.get("isError") or result.get("failed_step") is not None:
            raise CuaInputError(f"Cua Driver {tool} 입력 실패: {self._failure_detail(result)}")
        for step in result.get("steps", []):
            if step.get("ok") is False:
                raise CuaInputError(f"Cua Driver 입력 실패: {json.dumps(step, ensure_ascii=False)[:500]}")
        return result

    def _start_session(self):
        """Create or revive the helper's named Cua lifecycle session."""
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.run(
                [self.executable, "start_session"],
                input=json.dumps({"session": self.session}), text=True,
                encoding="utf-8", errors="replace", capture_output=True,
                timeout=self.timeout, creationflags=flags, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CuaInputError(f"Cua Driver 세션 시작 실패: {exc}") from exc
        if proc.returncode:
            raise CuaInputError(f"Cua Driver 세션 시작 실패: {(proc.stderr or proc.stdout).strip()[:500]}")
        # 기본 Cua 커서는 화면을 가로질러 활공한다. 게임 자동화는 지연 없이
        # 바로 클릭해야 하므로 1ms 직선 이동과 모든 시각 효과 끄기를 적용한다.
        motion = {
            "session": self.session,
            "style": "classic",
            "timing": "fixed",
            "glide_duration_ms": self.cursor_motion_ms,
            "dwell_after_click_ms": 0,
            "idle_hide_ms": 1,
            "effects": {name: "off" for name in ("trail", "glow", "magnet", "ripple", "squish")},
        }
        try:
            configured = subprocess.run(
                [self.executable, "set_agent_cursor_motion"],
                input=json.dumps(motion, ensure_ascii=False), text=True,
                encoding="utf-8", errors="replace", capture_output=True,
                timeout=self.timeout, creationflags=flags, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CuaInputError(f"Cua Driver 즉시 커서 설정 실패: {exc}") from exc
        if configured.returncode:
            raise CuaInputError(
                f"Cua Driver 즉시 커서 설정 실패: {(configured.stderr or configured.stdout).strip()[:500]}")
        return proc.stdout

    def check(self):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.run(
                [self.executable, "status"], text=True, encoding="utf-8",
                errors="replace", capture_output=True, timeout=self.timeout,
                creationflags=flags, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CuaInputError(f"Cua Driver 상태 확인 실패: {exc}") from exc
        if proc.returncode or "daemon is running" not in proc.stdout.lower():
            raise CuaInputError(f"Cua Driver 서비스가 실행 중이 아닙니다: {(proc.stderr or proc.stdout).strip()}")
        self._start_session()
        # 화면 캡처 방식과 무관하게 실제 입력 시에는 Cua가 대상 창을
        # 활성화한 뒤 전달할 수 있음을 입력 안전장치에 알린다.
        self.window.cua_input_enabled = True
        # 기본 MSS 캡처가 관리자 게임 창에서 WinError 5를 내면 즉시 Cua
        # 캡처로 전환할 수 있도록 항상 안전한 대체 경로를 등록한다.
        self.window.capture_fallback = self.capture
        if self.capture_enabled:
            self.window.capture_backend = self.capture
            self.window.background_capture = True
        else:
            # 평상시 화면 확인은 mss 캡처로 충분하다. Cua는 실제 입력 직전의
            # 검증 캡처와 클릭·드래그·키 전달에만 사용해 커서 표시와 호출 부하를 없앤다.
            self.window.capture_backend = None
            self.window.background_capture = False
        return proc.stdout

    def close(self):
        """헬퍼가 만든 Cua 세션·커서·임시 캡처를 정리한다.

        Cua 데몬은 다른 앱도 공유할 수 있으므로 이 객체가 소유하지 않은 전역
        서비스는 중지하지 않는다.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                subprocess.run(
                    [self.executable, "end_session"],
                    input=json.dumps({"session": self.session}, ensure_ascii=False),
                    text=True, encoding="utf-8", errors="replace", capture_output=True,
                    timeout=self.timeout, creationflags=flags, check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            try:
                self._capture_file.unlink(missing_ok=True)
            except OSError:
                pass
            self.window.capture_backend = None
            self.window.capture_fallback = None
            self.window.background_capture = False
            self.window.cua_input_enabled = False

    def capture(self):
        """가려진 게임 창도 Cua 창 캡처로 읽어 1920×1080 BGR 프레임을 반환한다."""
        import cv2

        with self._lock:
            pid, hwnd = self._target()
            observed = self._observe(pid, hwnd)
            frame = cv2.imread(str(self._capture_file), cv2.IMREAD_COLOR)
            if frame is None:
                raise CuaInputError("Cua Driver 게임 창 캡처 파일을 읽지 못했습니다")
            bounds = observed.get("window_bounds") or {}
            x = max(0, int(self.window.left - int(bounds.get("x", self.window.left))))
            y = max(0, int(self.window.top - int(bounds.get("y", self.window.top))))
            if frame.shape[1] >= x + self.window.width and frame.shape[0] >= y + self.window.height:
                frame = frame[y:y + self.window.height, x:x + self.window.width]
            if frame.shape[1::-1] != (1920, 1080):
                interp = cv2.INTER_AREA if frame.shape[1] > 1920 else cv2.INTER_CUBIC
                frame = cv2.resize(frame, (1920, 1080), interpolation=interp)
            return frame

    def _target(self):
        if not self.window.locate() or self.window.is_minimized():
            raise CuaInputError("게임 창을 찾지 못했거나 최소화되어 있습니다")
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(self.window.hwnd, ctypes.byref(pid))
        if not pid.value:
            raise CuaInputError("게임 창 PID를 확인하지 못했습니다")
        return int(pid.value), int(self.window.hwnd)

    def _observe(self, pid, hwnd):
        return self._call("get_window_state", {
            "pid": pid, "window_id": hwnd, "session": self.session,
            "include_accessibility_tree": False, "max_image_dimension": 0,
            "screenshot_out_file": str(self._capture_file),
        })

    def _local(self, screen_x, screen_y, observed):
        # Unity receives background PostMessage coordinates in client space.
        # Cua's screenshot also contains the non-client title bar, so using
        # window_bounds shifts every click down by that title-bar height.
        return int(screen_x - self.window.left), int(screen_y - self.window.top)

    @staticmethod
    def _screenshot_local(screen_x, screen_y, observed):
        bounds = observed.get("window_bounds") or {}
        return int(screen_x - bounds["x"]), int(screen_y - bounds["y"])

    @staticmethod
    def _prepare_foreground(hwnd):
        """Cua 전면 입력 전에 게임 HWND를 실제 전면 창으로 만든다."""
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = int(hwnd)
        if int(user32.GetForegroundWindow() or 0) == hwnd:
            return True
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        current_thread = int(kernel32.GetCurrentThreadId())
        foreground = int(user32.GetForegroundWindow() or 0)
        foreground_thread = int(user32.GetWindowThreadProcessId(foreground, None)) if foreground else 0
        attached = bool(foreground_thread and foreground_thread != current_thread)
        try:
            if attached:
                user32.AttachThreadInput(current_thread, foreground_thread, True)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(current_thread, foreground_thread, False)
        time.sleep(0.08)
        return int(user32.GetForegroundWindow() or 0) == hwnd

    @staticmethod
    def _cursor_position():
        point = wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return None
        return int(point.x), int(point.y)

    @staticmethod
    def _restore_cursor(position):
        if position is not None:
            ctypes.windll.user32.SetCursorPos(int(position[0]), int(position[1]))

    def _steps(self, pid, hwnd, steps, delivery_mode="background"):
        # 키 입력처럼 명시적으로 foreground를 요청한 경우에만 대상 창을 활성화한다.
        if delivery_mode == "foreground":
            self._prepare_foreground(hwnd)
        return self._call("run_steps", {
            "pid": pid, "window_id": hwnd, "session": self.session,
            "delivery_mode": delivery_mode, "foreground_fallback": False,
            "steps": steps,
            "observe": {"include_accessibility_tree": False,
                        "include_screenshot": True, "max_image_dimension": 320},
        })

    def click(self, x, y):
        with self._lock:
            pid, hwnd = self._target()
            observed = self._observe(pid, hwnd)
            lx, ly = self._local(x, y, observed)
            # Unity는 PostMessage 백그라운드 클릭을 표시만 하고 실제 UI 입력으로
            # 처리하지 않는다. Cua 전면 입력으로 확실히 전달하되 사용자의 포인터는
            # 클릭 직후 원래 화면 좌표로 즉시 복원한다.
            original = self._cursor_position()
            try:
                self._steps(pid, hwnd, [{"tool": "click", "args": {
                    "x": lx, "y": ly, "delivery_mode": "foreground",
                }}], "foreground")
            finally:
                self._restore_cursor(original)

    def drag(self, x1, y1, x2, y2, steps=10):
        with self._lock:
            pid, hwnd = self._target()
            observed = self._observe(pid, hwnd)
            a = self._local(x1, y1, observed)
            b = self._local(x2, y2, observed)
            self._steps(pid, hwnd, [{"tool": "drag", "args": {
                "from_x": a[0], "from_y": a[1], "to_x": b[0], "to_y": b[1],
                "steps": max(1, min(200, int(steps))),
                "duration_ms": max(80, min(10000, int(steps) * 30)),
                "delivery_mode": "background",
            }}], "background")

    def press(self, key, modifiers=None):
        aliases = {"esc": "escape", "enter": "return", "pgup": "pageup", "pgdn": "pagedown"}
        key = aliases.get(str(key).lower(), str(key).lower())
        with self._lock:
            pid, hwnd = self._target()
            self._observe(pid, hwnd)
            # UnityWndClass는 PostMessage 기반 백그라운드 키를 무시한다.
            # Cua 전면 전달은 물리 키 입력과 같은 경로를 사용하며 마우스 위치는 건드리지 않는다.
            args = {"key": key, "delivery_mode": "foreground"}
            if modifiers:
                args["modifiers"] = [str(x).lower() for x in modifiers]
            self._steps(pid, hwnd, [{"tool": "press_key", "args": args}], "foreground")

    def hotkey(self, combo):
        keys = [key.strip().lower() for key in combo.split("+") if key.strip()]
        if not keys:
            raise ValueError("단축키가 비어 있습니다")
        self.press(keys[-1], keys[:-1])

    def wheel(self, x, y, amount=-3):
        if not amount:
            return
        with self._lock:
            pid, hwnd = self._target()
            observed = self._observe(pid, hwnd)
            lx, ly = self._local(x, y, observed)
            # 휠 대신 같은 창의 백그라운드 드래그를 사용한다.
            distance = max(45, min(260, abs(int(amount)) * 7))
            height = int(observed.get("screenshot_height") or 0)
            if amount > 0:  # wheel up: drag list contents downward
                start_y = max(35, ly - distance // 2)
                end_y = min(max(35, height - 20), start_y + distance)
            else:           # wheel down: drag list contents upward
                start_y = min(max(35, height - 20), ly + distance // 2)
                end_y = max(35, start_y - distance)
            self._steps(pid, hwnd, [{"tool": "drag", "args": {
                "from_x": lx, "from_y": start_y, "to_x": lx, "to_y": end_y,
                "steps": 16, "duration_ms": 320,
                "delivery_mode": "background",
            }}], "background")
