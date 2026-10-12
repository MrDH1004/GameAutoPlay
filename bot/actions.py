"""마우스/키보드 입력과 단축키 감지."""
import ctypes
import random
import time

import pydirectinput

pydirectinput.PAUSE = 0.05
# 마우스를 화면 왼쪽 위 모서리로 옮기면 pydirectinput이 예외를 내며 멈춘다 (비상 정지).
pydirectinput.FAILSAFE = True

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# 매크로가 마지막으로 입력을 보낸 시각 (GetTickCount ms). 사용자 입력과 구분하는 데 쓴다.
_self_input_tick = 0


def mark_self_input():
    global _self_input_tick
    _self_input_tick = kernel32.GetTickCount()


def click(x, y, jitter=3):
    x += random.randint(-jitter, jitter)
    y += random.randint(-jitter, jitter)
    mark_self_input()
    pydirectinput.moveTo(x, y)
    time.sleep(random.uniform(0.05, 0.12))
    pydirectinput.click()
    mark_self_input()


def drag(x1, y1, x2, y2, steps=10):
    """누른 채로 (x1,y1)→(x2,y2)로 끌기 (목록 스크롤용)."""
    mark_self_input()
    pydirectinput.moveTo(x1, y1)
    pydirectinput.mouseDown()
    for i in range(1, steps + 1):
        pydirectinput.moveTo(int(x1 + (x2 - x1) * i / steps), int(y1 + (y2 - y1) * i / steps))
        time.sleep(0.03)
    pydirectinput.mouseUp()
    mark_self_input()


def press(key):
    mark_self_input()
    pydirectinput.press(key)
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
    """(x, y)로 마우스를 옮기고 휠을 굴린다. amount<0 이면 아래로 (칸 수).
    게임은 120짜리 이벤트를 연달아 보내면 무시하므로, 칸 수만큼의 델타를 이벤트 하나로 보낸다."""
    mark_self_input()
    pydirectinput.moveTo(int(x), int(y))
    time.sleep(0.3)                  # 게임 UI가 마우스 위치(호버)를 인식할 시간
    user32.mouse_event(0x0800, 0, 0, (int(amount) * 120) & 0xFFFFFFFF, 0)       # MOUSEEVENTF_WHEEL
    mark_self_input()
