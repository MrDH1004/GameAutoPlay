"""마우스/키보드 입력과 단축키 감지."""
import ctypes
import time

_input_backend = None

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# 매크로가 마지막으로 입력을 보낸 시각 (GetTickCount ms). 사용자 입력과 구분하는 데 쓴다.
_self_input_tick = 0
_self_input_monotonic = 0.0


def mark_self_input():
    global _self_input_tick, _self_input_monotonic
    _self_input_tick = kernel32.GetTickCount()
    _self_input_monotonic = time.monotonic()


def seconds_since_self_input():
    """마지막 자동 입력 이후 경과 시간. 입력 전이면 무한대로 취급한다."""
    return time.monotonic() - _self_input_monotonic if _self_input_monotonic else float("inf")


def configure_cua(window, config=None):
    """Enable Cua Driver input without a physical-input fallback."""
    global _input_backend
    from .cua_input import CuaInput
    backend = CuaInput(window, config)
    backend.check()
    _input_backend = backend
    return backend


def clear_input_backend():
    global _input_backend
    backend = _input_backend
    _input_backend = None
    if backend is not None:
        close = getattr(backend, "close", None)
        if close:
            close()


def input_backend_ready():
    return _input_backend is not None


def _cua():
    """물리 입력으로 우회하지 않고 Cua Driver만 반환한다."""
    if _input_backend is None:
        from .cua_input import CuaInputError
        raise CuaInputError("Cua Driver 입력 백엔드가 준비되지 않아 물리 입력 없이 동작을 보류합니다")
    return _input_backend


def click(x, y, jitter=3):
    backend = _cua()
    mark_self_input()
    try:
        return backend.click(x, y)
    finally:
        mark_self_input()


def drag(x1, y1, x2, y2, steps=10):
    """누른 채로 (x1,y1)→(x2,y2)로 끌기 (목록 스크롤용)."""
    backend = _cua()
    mark_self_input()
    try:
        return backend.drag(x1, y1, x2, y2, steps)
    finally:
        mark_self_input()


def hotkey(combo):
    """'ctrl+2' 같은 조합 키. 단일 키면 press와 같다."""
    keys = [k.strip() for k in combo.lower().split("+") if k.strip()]
    if not keys:
        raise ValueError("단축키가 비어 있습니다")
    backend = _cua()
    mark_self_input()
    try:
        return backend.hotkey(combo)
    finally:
        mark_self_input()


def press(key):
    backend = _cua()
    mark_self_input()
    try:
        return backend.press(key)
    finally:
        mark_self_input()


class UserInterrupt(Exception):
    """매크로 동작 도중 사용자가 마우스/키보드를 조작함."""


class _LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def _last_input_tick():
    info = _LastInputInfo(ctypes.sizeof(_LastInputInfo), 0)
    user32.GetLastInputInfo(ctypes.byref(info))
    return info.dwTime


class UserActivity:
    """사용자가 직접 마우스/키보드를 조작했는지 감지.

    Windows의 마지막 입력 시각이 매크로 자신의 마지막 입력 시각보다 grace_ms 이상 뒤면
    사용자 입력으로 본다.
    """

    def __init__(self, grace_ms=400):
        self.grace_ms = grace_ms
        self.last_user_tick = None      # 시작 전 입력은 무시
        self._ignore_until = 0.0

    def ignore_until_now(self, extra_seconds=1.5):
        """지금(+extra_seconds)까지의 입력은 사용자 조작으로 치지 않는다 (재개 키, 창 전환 클릭 등)."""
        self._ignore_until = time.monotonic() + extra_seconds
        mark_self_input()
        self.last_user_tick = None

    def idle_seconds(self):
        """사용자가 마지막으로 조작한 뒤 지난 시간(초). 조작한 적 없으면 None."""
        if time.monotonic() < self._ignore_until:
            mark_self_input()
            return None
        last = _last_input_tick()
        after_self = (last - _self_input_tick) & 0xFFFFFFFF   # tick 순환(49일) 대비
        if self.grace_ms < after_self < 0x80000000:
            self.last_user_tick = last
        if self.last_user_tick is None:
            return None
        return ((kernel32.GetTickCount() - self.last_user_tick) & 0xFFFFFFFF) / 1000.0


def _vk_code(key):
    key = key.upper()
    if key.startswith("F") and key[1:].isdigit():
        return 0x6F + int(key[1:])          # F1 = 0x70
    if len(key) == 1:
        return ord(key)
    raise ValueError(f"지원하지 않는 단축키: {key}")


class HotkeyWatcher:
    """키를 '누른 순간'만 True로 돌려주는 폴링 방식 단축키 감지기."""

    def __init__(self, *keys):
        self._codes = {k: _vk_code(k) for k in keys}
        self._down = {k: False for k in keys}

    def pressed(self, key):
        is_down = bool(user32.GetAsyncKeyState(self._codes[key]) & 0x8000)
        was_down = self._down[key]
        self._down[key] = is_down
        return is_down and not was_down


def wheel(x, y, amount=-3):
    """Cua Driver의 창 대상 스크롤만 사용한다."""
    backend = _cua()
    mark_self_input()
    try:
        return backend.wheel(x, y, amount)
    finally:
        mark_self_input()
