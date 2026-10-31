"""대시보드에 표시할 보스·이벤트 일정 계산."""
from datetime import datetime, timedelta


WEEKDAY = ("월", "화", "수", "목", "금", "토", "일")


def _clock(value, fallback):
    text = str(value or fallback)
    return text[:5] if len(text) >= 5 else fallback


def _event_period(cfg, now):
    event = ((cfg.get("event_calendar") or {}).get("nakhwa_depth") or {})
    title = event.get("title", "낙화의 전장 · 심처 개방")
    try:
        start = datetime.fromisoformat(str(event["starts_at"]))
        end = datetime.fromisoformat(str(event["ends_at"]))
    except (KeyError, TypeError, ValueError):
        return title, "일정 확인 필요"
    period = f"{start:%m월 %d일 %H:%M} ~ {end:%m월 %d일 %H:%M}"
    state = "진행 중" if start <= now < end else "예정" if now < start else "종료"
    return title, f"{period} · {state}"


def dashboard_schedule(now, cfg, boss_status="", nakhwa_status=""):
    """오늘 기준 표시 문자열을 반환한다. GUI 밖에서도 단위 테스트할 수 있게 유지한다."""
    boss = cfg.get("boss_hunt") or {}
    nakhwa = cfg.get("nakhwa_boss") or {}
    boss_name = "산신 산군대호" if now.weekday() in (0, 2, 4, 6) else "해골장군"
    boss_key = "mountain_tiger_enabled" if now.weekday() in (0, 2, 4, 6) else "skeleton_general_enabled"
    boss_enabled = bool(boss.get("enabled", True) and boss.get(boss_key, True))
    boss_time = _clock(boss.get("starts_at"), "21:00")
    duration = int(float(boss.get("duration_minutes", 5)))
    boss_start = datetime.strptime(boss_time, "%H:%M")
    boss_end = (boss_start + timedelta(minutes=duration)).strftime("%H:%M")
    nakhwa_time = _clock(nakhwa.get("starts_at"), "22:00")
    event_title, event_detail = _event_period(cfg, now)
    boss_tail = (f" · {boss_status}" if boss_status not in ("", "idle", "대기")
                 else " · 자동 참가" if boss_enabled else " · 자동 참가 꺼짐")
    nakhwa_enabled = bool(nakhwa.get("enabled", True))
    nakhwa_tail = (f" · {nakhwa_status}" if nakhwa_status not in ("", "idle", "대기")
                   else " · 자동 입장·사냥" if nakhwa_enabled else " · 자동 참가 꺼짐")
    return (
        f"● 오늘의 월드 보스  {boss_name}\n"
        f"   {WEEKDAY[now.weekday()]}요일 {boss_time}~{boss_end}{boss_tail}\n"
        f"● 낙화의 전장 · 요화선녀\n"
        f"   매일 {nakhwa_time}{nakhwa_tail}\n"
        f"● {event_title}\n"
        f"   {event_detail}"
    )
