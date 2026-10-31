from unittest.mock import Mock

import numpy as np
from mss.exception import ScreenShotError

from bot.window import GameWindow


def test_mss_access_denied_promotes_cua_capture_fallback():
    window = object.__new__(GameWindow)
    window.left = window.top = 0
    window.width, window.height = 1920, 1080
    window._sct = Mock()
    window._sct.grab.side_effect = ScreenShotError("BitBlt access denied")
    expected = np.zeros((1080, 1920, 3), dtype=np.uint8)
    window.capture_backend = None
    window.capture_fallback = Mock(return_value=expected)
    window.background_capture = False
    window._capture_fallback_active = False

    assert window.grab() is expected
    assert window.capture_backend == window.capture_fallback
    assert window.background_capture is True
    assert window._capture_fallback_active is True
    window._sct.grab.assert_called_once()

    assert window.grab() is expected
    window._sct.grab.assert_called_once()
    assert window.capture_fallback.call_count == 2
