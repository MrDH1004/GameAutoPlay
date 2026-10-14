"""개발 데이터는 저장소, 배포판 데이터는 사용자별 영구 디렉터리에 보관한다."""
import os
from pathlib import Path
import shutil
import sys

RESOURCE_ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
ROOT = str(Path(os.environ.get('DOKKAEBI_DATA_DIR') or Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'DokkaebiHelper')) if getattr(sys, 'frozen', False) else str(RESOURCE_ROOT)


def initialize_data(resource_root=None, data_root=None, version=None):
    """배포판 업데이트 시 설정·사냥터 DB·학습한 숫자는 유지하고 정적 리소스만 갱신한다."""
    from storage import load_json, save_json
    from version import VERSION
    source, target = Path(resource_root or RESOURCE_ROOT), Path(data_root or ROOT)
    if source.resolve() == target.resolve():
        return
    target.mkdir(parents=True, exist_ok=True)
    for name in ('config.yaml', 'farm_spots.json', 'item_dict.json'):
        if (source / name).is_file() and not (target / name).exists():
            shutil.copy2(source / name, target / name)
    marker, _ = load_json(target / '.resources.json')
    release = version or VERSION
    if marker.get('version') == release:
        return
    for directory in ('templates', 'assets'):
        origin = source / directory
        if not origin.exists():
            continue
        for item in origin.rglob('*'):
            if not item.is_file():
                continue
            relative = item.relative_to(source)
            destination = target / relative
            if relative.parts[:2] == ('templates', 'digits') and destination.exists():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)
    save_json(target / '.resources.json', {'version': release})
