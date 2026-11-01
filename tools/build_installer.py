"""검증된 배포 폴더를 Inno Setup 설치 파일로 만들고 체크섬을 기록한다."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess

from version import VERSION


ROOT = Path(__file__).resolve().parents[1]


def find_iscc() -> Path:
    candidates = [
        shutil.which("ISCC.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 6" / "ISCC.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise RuntimeError("Inno Setup 6의 ISCC.exe를 찾지 못했습니다.")


def main() -> None:
    package = ROOT / "dist" / VERSION / "GameAutoPlay"
    executable = package / "GameAutoPlay.exe"
    verification = ROOT / "build" / "package-verification.json"
    if not executable.is_file() or not verification.is_file():
        raise RuntimeError("먼저 build_exe.bat와 tools.package_verify를 실행하세요.")
    subprocess.run(
        [str(find_iscc()), f"/DMyAppVersion={VERSION}", str(ROOT / "installer.iss")],
        cwd=ROOT, check=True,
    )
    installer = ROOT / "dist" / VERSION / f"GameAutoPlay-Setup-{VERSION}.exe"
    if not installer.is_file():
        raise RuntimeError("Inno Setup 결과 파일이 생성되지 않았습니다.")
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    checksum = installer.with_suffix(".sha256.txt")
    checksum.write_text(f"{digest}  {installer.name}\n", encoding="utf-8")
    print(f"installer={installer}")
    print(f"sha256={digest}")


if __name__ == "__main__":
    main()
