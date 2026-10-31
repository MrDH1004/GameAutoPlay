"""GitHub Releases 기반의 안전한 배포본 자동 업데이트."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen
import zipfile

from app_paths import ROOT


GITHUB_REPOSITORY = "MrDH1004/GameAutoPlay"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
USER_AGENT = "DokkaebiHelper-Updater"
VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    title: str
    notes: str
    page_url: str
    archive_url: str
    checksum_url: str


def version_tuple(value: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.fullmatch(str(value).strip())
    if not match:
        raise ValueError(f"올바르지 않은 버전: {value}")
    return tuple(map(int, match.groups()))


def _read_url(url: str, timeout: float = 15) -> bytes:
    request = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def check_for_update(current_version: str, *, reader=_read_url) -> ReleaseInfo | None:
    """최신 정식 Release가 현재 버전보다 높을 때만 설치 정보를 반환한다."""
    release = json.loads(reader(LATEST_RELEASE_API).decode("utf-8"))
    latest = str(release.get("tag_name", "")).lstrip("v")
    if version_tuple(latest) <= version_tuple(current_version):
        return None
    assets = {asset.get("name"): asset.get("browser_download_url") for asset in release.get("assets", [])}
    archive_name = f"GameAutoPlay-{latest}.zip"
    checksum_name = f"GameAutoPlay-{latest}.sha256.txt"
    if not assets.get(archive_name) or not assets.get(checksum_name):
        raise RuntimeError(f"v{latest} Release에 패치 파일 또는 SHA-256 파일이 없습니다.")
    return ReleaseInfo(
        version=latest,
        title=str(release.get("name") or f"v{latest}"),
        notes=str(release.get("body") or "업데이트 내역이 없습니다."),
        page_url=str(release.get("html_url") or ""),
        archive_url=assets[archive_name],
        checksum_url=assets[checksum_name],
    )


def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    base = destination.resolve()
    for item in archive.infolist():
        target = (destination / item.filename).resolve()
        if target != base and base not in target.parents:
            raise RuntimeError("업데이트 ZIP에 안전하지 않은 경로가 있습니다.")
    archive.extractall(destination)


def stage_update(info: ReleaseInfo, *, reader=_read_url, data_root: str | Path = ROOT) -> Path:
    """업데이트를 내려받아 검증하고 교체용 디렉터리를 반환한다."""
    root = Path(data_root)
    updates = root / "updates"
    updates.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"v{info.version}-", dir=updates))
    archive_path = work / f"GameAutoPlay-{info.version}.zip"
    archive_path.write_bytes(reader(info.archive_url))
    checksum_text = reader(info.checksum_url).decode("utf-8", errors="replace")
    expected = checksum_text.strip().split()[0].lower()
    actual = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or actual != expected:
        shutil.rmtree(work, ignore_errors=True)
        raise RuntimeError("업데이트 파일의 SHA-256 검증에 실패했습니다.")
    extracted = work / "extracted"
    extracted.mkdir()
    try:
        with zipfile.ZipFile(archive_path) as archive:
            _safe_extract(archive, extracted)
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise
    package = extracted / "GameAutoPlay"
    required = (
        package / "GameAutoPlay.exe",
        package / "_internal" / "cua-driver" / "cua-driver.exe",
        package / "verification.json",
    )
    if not all(path.is_file() for path in required):
        shutil.rmtree(work, ignore_errors=True)
        raise RuntimeError("업데이트 패키지에 필수 실행 파일이 없습니다.")
    return package


def create_patch_script(package: str | Path, *, executable: str | Path | None = None,
                        pid: int | None = None, data_root: str | Path = ROOT) -> Path:
    """현재 앱이 종료된 뒤 새 패키지를 덮어쓰고 재실행하는 배치 파일을 만든다."""
    package = Path(package).resolve()
    executable = Path(executable or sys.executable).resolve()
    target = executable.parent
    if not (package / "GameAutoPlay.exe").is_file() or executable.name.lower() != "gameautoplay.exe":
        raise RuntimeError("자동 패치는 빌드된 GameAutoPlay.exe에서만 실행할 수 있습니다.")
    script = Path(data_root) / "apply-update.cmd"
    content = fr'''@echo off
setlocal
set "APP_PID={int(pid or os.getpid())}"
set "SOURCE={package}"
set "TARGET={target}"
for /L %%I in (1,1,120) do (
  tasklist /FI "PID eq %APP_PID%" /NH 2>nul | findstr /R /C:"[ ]%APP_PID%[ ]" >nul
  if errorlevel 1 goto copy_update
  ping 127.0.0.1 -n 2 >nul
)
exit /b 1
:copy_update
robocopy "%SOURCE%" "%TARGET%" /MIR /R:3 /W:1 >nul
if errorlevel 8 exit /b %errorlevel%
start "" "%TARGET%\GameAutoPlay.exe"
for %%D in ("%SOURCE%\..\..") do rd /s /q "%%~fD"
del "%~f0"
'''
    script.write_text(content, encoding="utf-8-sig")
    return script


def launch_patch_script(script: str | Path) -> None:
    subprocess.Popen(
        ["cmd.exe", "/c", str(Path(script).resolve())],
        cwd=str(Path(script).resolve().parent),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0),
        close_fds=True,
    )
