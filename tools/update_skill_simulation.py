"""깨비지지 도술 105종과 유저 덱을 갱신하고 기본 계수 CPU 조합 탐색을 실행한다."""
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skill_simulator import RESULT_FILE, owned_skill_names, refresh_and_simulate  # noqa: E402


def exact_kkaebigg_validation(catalog):
    """사이트 원본 JS 계산기를 별도 실행해 로컬 근사 모델을 교차검증한다."""
    owned = owned_skill_names()
    ids = [str(skill["id"]) for skill in catalog["skills"]
           if not skill.get("ultimate") and skill["name"] in owned]
    script = os.path.join(ROOT, "tools", "kkaebigg_exact_validate.mjs")
    completed = subprocess.run(
        ["node", script, ",".join(ids)], cwd=ROOT, capture_output=True,
        text=True, encoding="utf-8", timeout=300, check=True)
    payload = json.loads(completed.stdout)
    by_id = {int(skill["id"]): skill["name"] for skill in catalog["skills"]}
    for row in payload.get("rows", []):
        row["skills"] = [by_id[index] for index in row.pop("core") + row.pop("ext")]
        row["cast_order"] = [by_id[index] for index in row["cast_order"] if index in by_id]
        row["ultimate"] = by_id.get(row["ultimate"])
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--beam", type=int, default=16000)
    args = parser.parse_args()
    lock_path = os.path.join(ROOT, "logs", "skill-learning.lock")
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if time.time() - os.path.getmtime(lock_path) < 7200:
            print("도술 학습이 이미 실행 중입니다.")
            raise SystemExit(0)
        os.unlink(lock_path)
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        # 이 학습 프로세스와 자식 워커가 사용할 수 있는 CPU를 논리 코어 절반으로 제한한다.
        if os.name == "nt":
            import ctypes
            total = max(1, os.cpu_count() or 1)
            allowed = max(1, total // 2)
            ctypes.windll.kernel32.SetProcessAffinityMask(
                ctypes.windll.kernel32.GetCurrentProcess(), (1 << allowed) - 1)
        catalog, results = refresh_and_simulate(max(1000, args.beam))
        try:
            results["kkaebigg_exact"] = exact_kkaebigg_validation(catalog)
            RESULT_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
            results["kkaebigg_exact"] = {"error": str(exc), "rows": []}
            RESULT_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        os.close(lock_fd)
        try:
            os.unlink(lock_path)
        except FileNotFoundError:
            pass
    print(json.dumps({
        "skills": len(catalog["skills"]), "builds": len(catalog["user_builds"]),
        "owned": results["owned_count"], "results": results,
    }, ensure_ascii=False, indent=2))
