"""입력을 보내지 않는 환경 점검과 비밀값을 제외한 로컬 진단 파일."""
import ctypes
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
import json
import platform
from pathlib import Path
import re
import sys
import zipfile

from bot2.selfcheck import missing_templates, validate_config
from version import VERSION


def redact(value, secrets=()):
    if isinstance(value, dict):
        return {k: '[제외]' if any(part in k.lower() for part in ('token', 'webhook', 'chat_id', 'password', 'secret'))
                else redact(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, secrets) for v in value]
    if not isinstance(value, str):
        return value
    for secret in sorted((str(s) for s in secrets if s), key=len, reverse=True):
        value = value.replace(secret, '[제외]')
    value = re.sub(r'https://api\.telegram\.org/bot[^/\s]+', 'https://api.telegram.org/bot[제외]', value)
    value = re.sub(r'https://(?:\w+\.)?discord(?:app)?\.com/api/webhooks/[^\s]+', '[웹훅 제외]', value)
    value = re.sub(r'\b\d{5,}:[A-Za-z0-9_-]{15,}\b', '[토큰 제외]', value)
    return value


def collect_secrets(data):
    secrets = []
    if isinstance(data, dict):
        for key, val in data.items():
            if isinstance(val, (str, int)) and any(part in key.lower() for part in ('token', 'webhook', 'chat_id', 'password', 'secret')):
                if val:
                    secrets.append(str(val))
            else:
                secrets.extend(collect_secrets(val))
    return secrets


def environment_report(cfg, root, include_game=True):
    checks = []

    def add(name, status, detail):
        checks.append({'name': name, 'status': status, 'detail': detail})

    add('운영체제', 'ok' if sys.platform == 'win32' else 'error', platform.system() + ' ' + platform.release())
    try:
        admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except AttributeError:
        admin = False
    add('입력 권한', 'ok' if admin else 'warning', '관리자 권한' if admin else '관리자 실행 또는 관리자 브리지가 필요합니다')
    problems = validate_config(cfg)
    add('설정', 'error' if problems else 'ok', '\n'.join(problems) or '필수 항목과 수치 범위 정상')
    missing = missing_templates(cfg)
    add('화면 인식 파일', 'error' if missing else 'ok', ', '.join(missing) or '필수 템플릿 정상')
    from tools.ocr import PS1
    add('Windows OCR', 'ok' if Path(PS1).is_file() else 'error', 'OCR 스크립트 있음 · 언어 인식은 실제 화면으로 확인' if Path(PS1).is_file() else 'OCR 스크립트 없음')
    if include_game:
        try:
            from bot.window import find_windows
            windows = find_windows(cfg.get('window_title', ''), cfg.get('process_name'))
            add('게임 창', 'ok' if windows else 'warning', '게임 창 감지' if windows else '게임을 실행한 뒤 시작하세요')
        except Exception as error:
            add('게임 창', 'warning', type(error).__name__ + ': 창 상태를 읽지 못했습니다')
    dependencies = {}
    for package in ('PySide6', 'mss', 'numpy', 'opencv-python', 'PyYAML', 'pydirectinput'):
        try:
            dependencies[package] = version(package)
        except PackageNotFoundError:
            dependencies[package] = '배포판 내장' if getattr(sys, 'frozen', False) else '미설치'
    return {'app_version': VERSION, 'at': datetime.now().isoformat(timespec='seconds'),
            'python': platform.python_version(), 'packaged': bool(getattr(sys, 'frozen', False)),
            'checks': checks, 'dependencies': dependencies}


def export_support(path, report, settings, lines, config=None):
    """설정·점검·최근 로그만 저장한다. 화면·계정 데이터·프로필 원본은 포함하지 않는다."""
    secrets = collect_secrets(settings) + collect_secrets(config or {})
    log_text = '\n'.join(str(line) for line in list(lines)[-200:])
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in (('environment.json', report), ('settings.json', settings)):
            archive.writestr(name, json.dumps(redact(data, secrets), ensure_ascii=False, indent=2))
        archive.writestr('recent.log', redact(log_text, secrets))
        archive.writestr('README.txt', '도깨비헬퍼 로컬 진단 자료입니다. 외부로 자동 전송하지 않습니다.\n')
