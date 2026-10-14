"""같은 게임에 제어판과 명령줄 봇이 동시에 입력하지 않도록 실행 잠금을 공유한다."""
import hashlib
from pathlib import Path
import tempfile

from PySide6.QtCore import QLockFile


def acquire_bot_lock(config):
    identity = str(config.get('process_name') or config.get('window_title') or 'dokkaebi').casefold()
    name = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]
    lock = QLockFile(str(Path(tempfile.gettempdir()) / ('dokkaebi-bot-' + name + '.lock')))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        raise RuntimeError('같은 게임의 자동 진행이 이미 실행 중입니다. 기존 제어판이나 명령줄 봇을 먼저 정지하세요.')
    return lock
