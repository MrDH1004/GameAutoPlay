"""검증된 버전별 폴더를 사용자 데이터가 없는 배포 ZIP과 체크섬으로 묶는다."""
import hashlib
import json
from pathlib import Path
import zipfile

import yaml
from app_support import collect_secrets
from version import VERSION

ROOT = Path(__file__).resolve().parents[1]


def main():
    release = ROOT / 'dist' / VERSION
    package = release / 'GameAutoPlay'
    verification = json.loads((ROOT / 'build' / 'package-verification.json').read_text(encoding='utf-8'))
    executable_hash = hashlib.sha256((package / 'GameAutoPlay.exe').read_bytes()).hexdigest()
    if verification['version'] != VERSION or verification['sha256'] != executable_hash:
        raise RuntimeError('현재 EXE와 배포 검증 결과가 일치하지 않습니다')
    if not all(value for value in verification.values() if isinstance(value, bool)):
        raise RuntimeError('배포 검증을 먼저 통과해야 합니다')
    config = yaml.safe_load((package / '_internal' / 'config.yaml').read_text(encoding='utf-8'))
    if collect_secrets(config):
        raise RuntimeError('배포 기본 설정에 비밀값이 포함되어 있습니다')
    files = [path for path in package.rglob('*') if path.is_file()]
    if any(path.name in ('gui_settings.json', 'bot2.log', 'state.json', '.gui.lock') for path in files):
        raise RuntimeError('배포 폴더에 사용자 실행 데이터가 있습니다')
    manifest = {key: value for key, value in verification.items() if key not in ('executable', 'data_directory')}
    filename = f'GameAutoPlay-{VERSION}.zip'
    destination = release / filename
    temporary = destination.with_suffix('.zip.tmp')
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files:
            archive.write(path, Path('GameAutoPlay') / path.relative_to(package))
        archive.writestr('GameAutoPlay/verification.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr('GameAutoPlay/README.txt',
                         f'도깨비헬퍼 v{VERSION}\n\nZIP 전체를 풀고 GameAutoPlay.exe를 실행하세요.\n'
                         '게임을 전체화면으로 실행하고 제어판에서 시작을 누르세요.\n'
                         'F11: 일시정지/재개, F12: 자동 진행 종료.\n'
                         '문제가 생기면 도움말의 환경 점검과 진단 파일 저장을 사용하세요.\n'
                         '설정과 로그는 %LOCALAPPDATA%\\DokkaebiHelper에 보관됩니다.\n'
                         '검증 기록은 verification.json에 있습니다. 이 버전은 외부 출시 전 검증을 진행하는 배포 후보입니다.\n')
    temporary.replace(destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    (release / f'GameAutoPlay-{VERSION}.sha256.txt').write_text(f'{digest}  {filename}\n', encoding='utf-8')
    print(json.dumps({'archive': str(destination), 'sha256': digest, 'size_mb': round(destination.stat().st_size / 1048576, 1)}, indent=2))


if __name__ == '__main__':
    main()
