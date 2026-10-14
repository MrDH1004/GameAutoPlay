"""JSON 원자 저장과 마지막 정상 파일 복구. 임시 파일은 같은 디렉터리에 만든다."""
import json
import os
from pathlib import Path
import tempfile
import threading

_lock = threading.RLock()


def _read(path):
    with open(path, encoding='utf-8-sig') as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError('JSON 최상위 값은 객체여야 합니다')
    return data


def load_json(path):
    """(설정, 복구 안내). 원본이 없으면 새 설치, 손상되면 .bak을 읽는다."""
    path = Path(path)
    try:
        return _read(path), ''
    except FileNotFoundError:
        if not path.with_suffix(path.suffix + '.bak').exists():
            return {}, ''
    except (OSError, ValueError):
        pass
    try:
        return _read(path.with_suffix(path.suffix + '.bak')), '설정 파일을 읽지 못해 마지막 정상 백업을 복구했습니다.'
    except (OSError, ValueError):
        return {}, '설정과 백업을 읽지 못했습니다. 기본 설정을 사용합니다. 시작 전에 설정을 확인하세요.'


def _replace(path, payload):
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', prefix=path.name + '.', suffix='.tmp', dir=path.parent,
                                         delete=False) as stream:
            temp_path = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def save_json(path, data, backup=True):
    """저장이 실패하면 기존 파일을 보존한다. 손상된 원본은 정상 .bak을 덮어쓰지 않는다."""
    if not isinstance(data, dict):
        raise ValueError('JSON 최상위 값은 객체여야 합니다')
    payload = (json.dumps(data, ensure_ascii=False, indent=1, allow_nan=False) + '\n').encode('utf-8')
    path = Path(path)
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        if backup and path.exists():
            try:
                previous = _read(path)
            except (OSError, ValueError):
                previous = None
            if previous is not None:
                _replace(path.with_suffix(path.suffix + '.bak'),
                         (json.dumps(previous, ensure_ascii=False, indent=1) + '\n').encode('utf-8'))
        _replace(path, payload)
