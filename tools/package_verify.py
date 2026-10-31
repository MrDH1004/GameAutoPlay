"""배포 EXE 실행·중복 실행 방지·자가 점검·업데이트 보존을 격리 데이터로 검증한다."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from storage import save_json
from version import VERSION

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe', default=str(ROOT / 'dist' / VERSION / 'GameAutoPlay' / 'GameAutoPlay.exe'))
    args = parser.parse_args()
    executable = Path(args.exe).resolve()
    (ROOT / 'build').mkdir(exist_ok=True)
    data = Path(tempfile.mkdtemp(prefix='package-smoke-', dir=ROOT / 'build'))
    env = {**os.environ, 'DOKKAEBI_DATA_DIR': str(data), 'QT_QPA_PLATFORM': 'offscreen',
           'DOKKAEBI_SMOKE_HOLD_MS': '5000'}
    env.pop('DOKKAEBI_TEST', None)
    report_path = data / 'logs' / 'smoke-report.json'
    first = subprocess.Popen([str(executable), '--smoke-test'], env=env)
    try:
        deadline = time.monotonic() + 30
        while not report_path.exists() and first.poll() is None and time.monotonic() < deadline:
            time.sleep(.1)
        if not report_path.exists():
            raise RuntimeError('배포 EXE가 GUI 검증 결과를 만들지 못했습니다')
        duplicate = subprocess.run([str(executable), '--smoke-test'], env=env, timeout=30)
        first_result = first.wait(timeout=30)
    finally:
        if first.poll() is None:
            first.terminate()
            first.wait(timeout=10)
    first_report = json.loads(report_path.read_text(encoding='utf-8'))
    bundled_cua = executable.parent / '_internal' / 'cua-driver' / 'cua-driver.exe'
    bundled_cua_helpers = all(
        (bundled_cua.parent / name).is_file()
        for name in ('cua-driver.exe', 'cua-driver-uia.exe', 'cua-cursor-theme.exe')
    )
    cua_version = subprocess.run(
        [str(bundled_cua), '--version'], capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=False,
    ) if bundled_cua_helpers else None
    settings_path = data / 'gui_settings.json'
    settings = json.loads(settings_path.read_text(encoding='utf-8'))
    settings['farm/margin'] = 1.3
    settings['_future_extension'] = 'preserve-me'
    save_json(settings_path, settings)
    config = data / 'config.yaml'
    config.write_text(config.read_text(encoding='utf-8') + '\nrelease_validation_marker: preserve-me\n', encoding='utf-8')
    digit = data / 'templates' / 'digits' / 'power_0.png'
    learned = digit.read_bytes() + b'preserve-learned-data'
    digit.write_bytes(learned)
    save_json(data / '.resources.json', {'version': '0.0.0'})
    env['DOKKAEBI_SMOKE_HOLD_MS'] = '500'
    second = subprocess.run([str(executable), '--smoke-test'], env=env, timeout=30)
    after = json.loads(settings_path.read_text(encoding='utf-8'))
    self_check_path = data / 'logs' / 'self-check.json'
    self_check = subprocess.run([str(executable), '--self-check', '--report', str(self_check_path)], env=env, timeout=30)
    result = {'version': VERSION, 'executable': str(executable), 'data_directory': str(data),
              'sha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
              'first_launch': first_result == 0, 'duplicate_blocked': duplicate.returncode == 3,
              'bundled_cua': bool(cua_version and cua_version.returncode == 0 and 'cua-driver' in cua_version.stdout.lower()),
              'pages': first_report['pages'], 'settings_roundtrip': first_report['settings_roundtrip'],
              'second_launch': second.returncode == 0, 'custom_settings_preserved': after.get('farm/margin') == 1.3,
              'unknown_settings_preserved': after.get('_future_extension') == 'preserve-me',
              'config_preserved': 'release_validation_marker: preserve-me' in config.read_text(encoding='utf-8'),
              'learned_digit_preserved': digit.read_bytes() == learned,
              'self_check': self_check.returncode == 0}
    save_json(ROOT / 'build' / 'package-verification.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not all(v for k, v in result.items() if isinstance(v, bool)):
        raise RuntimeError('배포 검증 실패: 결과 파일을 확인하세요')


if __name__ == '__main__':
    main()
