import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest

import updater


def release(version="1.35.0"):
    return {
        "tag_name": f"v{version}", "name": f"도깨비헬퍼 v{version}", "body": "새 기능",
        "html_url": "https://example.test/release",
        "assets": [
            {"name": f"GameAutoPlay-{version}.zip", "browser_download_url": "https://example.test/app.zip"},
            {"name": f"GameAutoPlay-{version}.sha256.txt", "browser_download_url": "https://example.test/app.sha256.txt"},
        ],
    }


def test_check_for_update_returns_newer_release():
    info = updater.check_for_update("1.34.102", reader=lambda _url: json.dumps(release()).encode())
    assert info.version == "1.35.0"
    assert info.archive_url.endswith("app.zip")


def test_check_for_update_ignores_same_or_older_release():
    assert updater.check_for_update("1.35.0", reader=lambda _url: json.dumps(release()).encode()) is None


def package_bytes(version="1.35.0"):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("GameAutoPlay/GameAutoPlay.exe", b"exe")
        archive.writestr("GameAutoPlay/_internal/cua-driver/cua-driver.exe", b"cua")
        archive.writestr("GameAutoPlay/verification.json", json.dumps({"version": version}))
    return output.getvalue()


def test_stage_update_verifies_hash_and_required_files(tmp_path):
    content = package_bytes()
    digest = hashlib.sha256(content).hexdigest()
    info = updater.check_for_update("1.34.102", reader=lambda _url: json.dumps(release()).encode())

    def reader(url):
        return content if url.endswith("app.zip") else f"{digest}  app.zip\n".encode()

    package = updater.stage_update(info, reader=reader, data_root=tmp_path)
    assert (package / "GameAutoPlay.exe").read_bytes() == b"exe"
    assert (package / "_internal/cua-driver/cua-driver.exe").is_file()


def test_stage_update_rejects_bad_hash(tmp_path):
    info = updater.check_for_update("1.34.102", reader=lambda _url: json.dumps(release()).encode())
    with pytest.raises(RuntimeError, match="SHA-256"):
        updater.stage_update(info, reader=lambda url: package_bytes() if url.endswith("app.zip") else b"0" * 64,
                             data_root=tmp_path)


def test_safe_extract_rejects_parent_escape(tmp_path):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("../escape.txt", b"bad")
    with zipfile.ZipFile(io.BytesIO(content.getvalue())) as archive:
        with pytest.raises(RuntimeError, match="안전하지 않은"):
            updater._safe_extract(archive, tmp_path)


def test_patch_script_contains_versioned_source_and_restart(tmp_path):
    package = tmp_path / "stage" / "GameAutoPlay"
    package.mkdir(parents=True)
    (package / "GameAutoPlay.exe").write_bytes(b"new")
    installed = tmp_path / "installed" / "GameAutoPlay.exe"
    installed.parent.mkdir()
    installed.write_bytes(b"old")
    script = updater.create_patch_script(package, executable=installed, pid=123, data_root=tmp_path)
    text = script.read_text(encoding="utf-8-sig")
    assert "APP_PID=123" in text
    assert str(package.resolve()) in text
    assert "robocopy" in text and "GameAutoPlay.exe" in text
