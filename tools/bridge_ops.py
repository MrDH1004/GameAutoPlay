"""브리지 명령 실행부. 파일이 바뀌면 브리지가 자동으로 다시 불러온다 (관리자 재실행 불필요)."""
import ctypes
import time

import pydirectinput

from bot import actions

user32 = ctypes.windll.user32

ALLOWED_KEYS = {"esc", "enter", "space", "tab", "f", "g", "t", "j", "m", "i", "p", "u", "k", "o", "l",
                "h", "x", "z", "r", "1", "2", "3", "4", "5", "up", "down", "left", "right",
                "f11"}                      # f11 = 매크로 일시정지/재개 단축키 (게임 조작이 아님)


def focus_game(win):
    """게임 창을 맨 앞으로. 포그라운드 잠금을 피하려고 ALT를 한 번 눌렀다 뗀다."""
    if win.hwnd is None:
        return False
    if win.is_foreground():
        return True
    if user32.IsIconic(win.hwnd):
        user32.ShowWindow(win.hwnd, 9)          # SW_RESTORE
    kernel32 = ctypes.windll.kernel32
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None)
    my_thread = kernel32.GetCurrentThreadId()
    for _ in range(3):
        # 현재 앞 창의 입력 스레드에 붙어서 포그라운드 잠금을 우회
        user32.AttachThreadInput(my_thread, fg_thread, True)
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        user32.BringWindowToTop(win.hwnd)
        user32.SetForegroundWindow(win.hwnd)
        user32.SetFocus(win.hwnd)
        user32.AttachThreadInput(my_thread, fg_thread, False)
        time.sleep(0.3)
        if win.is_foreground():
            return True
    return False


def execute(win, cmd):
    action = cmd["action"]
    if action == "screenshot":
        return
    if action == "focus":
        if not focus_game(win):
            raise RuntimeError("게임 창을 앞으로 가져오지 못함")
        return
    if not win.is_foreground() and not focus_game(win):
        raise RuntimeError("게임 창이 맨 앞에 있지 않아 입력을 보내지 않음")

    if action == "togglewindow":                # Alt+Enter: 전체화면 <-> 창 모드
        actions.mark_self_input()
        pydirectinput.keyDown("alt")
        time.sleep(0.1)
        pydirectinput.press("enter")
        time.sleep(0.1)
        pydirectinput.keyUp("alt")
        actions.mark_self_input()
        return
    if action == "click":
        sx, sy = win.frame_to_screen(cmd["x"], cmd["y"])
        actions.click(sx, sy, jitter=0)
    elif action == "key":
        key = cmd["key"].lower()
        if key not in ALLOWED_KEYS:
            raise ValueError(f"허용되지 않은 키: {key}")
        actions.press(key)
    elif action == "drag":
        sx, sy = win.frame_to_screen(cmd["x"], cmd["y"])
        ex, ey = win.frame_to_screen(cmd["x2"], cmd["y2"])
        actions.mark_self_input()
        pydirectinput.moveTo(sx, sy)
        pydirectinput.mouseDown()
        for i in range(1, 11):
            pydirectinput.moveTo(int(sx + (ex - sx) * i / 10), int(sy + (ey - sy) * i / 10))
            time.sleep(0.03)
        pydirectinput.mouseUp()
        actions.mark_self_input()
    elif action == "wheel":
        sx, sy = win.frame_to_screen(cmd["x"], cmd["y"])
        actions.mark_self_input()
        pydirectinput.moveTo(sx, sy)
        user32.mouse_event(0x0800, 0, 0, int(cmd.get("amount", -3)) * 120, 0)
    else:
        raise ValueError(f"알 수 없는 동작: {action}")
