"""게임 창 찾기와 클라이언트 영역 캡처."""
import ctypes
from ctypes import wintypes

import cv2
import mss
import numpy as np

# 템플릿·좌표는 모두 이 기준 해상도에서 만들었다. 창 크기가 달라도 캡처를 이 크기로 정규화해서 쓴다.
REF_W, REF_H = 1920, 1080

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


def enable_dpi_awareness():
    """Windows 배율(125% 등)에서도 좌표가 실제 픽셀과 일치하도록 설정."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def process_name_of(hwnd):
    """창을 소유한 프로세스의 실행 파일 이름 (예: dokkaebi.exe). 실패하면 빈 문자열."""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value.rsplit("\\", 1)[-1]
    finally:
        kernel32.CloseHandle(handle)


def find_windows(title_part, process_name=None):
    """제목에 title_part가 들어간 보이는 창 목록 [(hwnd, title)]. 자기 콘솔 창은 제외.
    process_name을 주면 그 실행 파일이 소유한 창만 남긴다."""
    own_console = kernel32.GetConsoleWindow()
    needle = title_part.lower()
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if hwnd == own_console or not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if needle not in buf.value.lower():
            return True
        if process_name and process_name_of(hwnd).lower() != process_name.lower():
            return True
        found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(callback, 0)
    return found


def foreground_title():
    """지금 맨 앞에 있는 창의 제목 (로그용)."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value[:40]


class GameWindow:
    def __init__(self, title_part, process_name=None):
        self.title_part = title_part
        self.process_name = process_name
        self.hwnd = None
        self.title = ""
        self.left = self.top = self.width = self.height = 0
        self._sct = mss.mss()

    def locate(self):
        """창을 찾고 클라이언트 영역 위치를 갱신. 성공하면 True."""
        if self.hwnd is None or not user32.IsWindow(self.hwnd):
            matches = find_windows(self.title_part, self.process_name)
            if not matches:
                self.hwnd = None
                return False
            self.hwnd, self.title = matches[0]

        rect = wintypes.RECT()
        user32.GetClientRect(self.hwnd, ctypes.byref(rect))
        pt = wintypes.POINT(0, 0)
        user32.ClientToScreen(self.hwnd, ctypes.byref(pt))
        self.left, self.top = pt.x, pt.y
        self.width, self.height = rect.right, rect.bottom
        return self.width > 0 and self.height > 0

    def is_foreground(self):
        return self.hwnd is not None and user32.GetForegroundWindow() == self.hwnd

    def is_minimized(self):
        return self.hwnd is not None and bool(user32.IsIconic(self.hwnd))

    @property
    def frame_w(self):
        """grab()이 돌려주는 이미지의 가로 (항상 기준 해상도)."""
        return REF_W

    @property
    def frame_h(self):
        return REF_H

    @property
    def scale(self):
        """실제 창 크기 / 기준 해상도 (x, y)."""
        return self.width / REF_W, self.height / REF_H

    def grab(self):
        """클라이언트 영역을 BGR numpy 배열로 캡처. 창 크기가 기준 해상도와 다르면 1920x1080으로 리사이즈."""
        shot = self._sct.grab({
            "left": self.left, "top": self.top,
            "width": self.width, "height": self.height,
        })
        frame = np.ascontiguousarray(np.asarray(shot)[:, :, :3])
        if (self.width, self.height) != (REF_W, REF_H):
            interp = cv2.INTER_AREA if self.width > REF_W else cv2.INTER_CUBIC
            frame = cv2.resize(frame, (REF_W, REF_H), interpolation=interp)
        return frame

    def frame_to_screen(self, px, py):
        """캡처 이미지(기준 해상도) 안의 픽셀 좌표 → 화면 절대 좌표."""
        sx, sy = self.scale
        return self.left + int(px * sx), self.top + int(py * sy)

    def ratio_to_screen(self, rx, ry):
        """창 비율 좌표(0~1) → 화면 절대 좌표."""
        return self.frame_to_screen(rx * REF_W, ry * REF_H)
