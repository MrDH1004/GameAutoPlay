"""제어판 '동작 흐름' 화면에 표시할 모드별 UML형 흐름도."""
from html import escape


FLOW_PAGES = {
    "overview": {
        "title": "전체 상태 흐름",
        "subtitle": "모드가 달라도 화면 판정과 안전 처리는 같은 순서로 적용됩니다.",
        "blocks": [
            ("start", "화면 관찰", "게임 창 · HP · 전투력 · 지도 · 이동 · 전투 · AUTO 상태를 반복 확인"),
            ("decision", "사용자 조작 감지?", "예 → 마지막 입력부터 2분 대기 · 아니오 → 다음 판단"),
            ("decision", "긴급 상태?", "사망 → 무료 복귀 / 저체력 → 회복·피신 / 대화 → 넘기기 / 메뉴 고착 → ESC 복구"),
            ("branches", "활성 모드 선택", [
                ("일일 전용", "[일일]만 수행", "daily"),
                ("서사 전용", "적합 서사만 수행", "story"),
                ("일반 모드", "현재 위치 유지", "normal"),
                ("앵벌 모드", "추천 사냥터 이동", "farm"),
                ("기본 퀘스트", "트래커·임무 진행", "quest"),
            ]),
            ("action", "공통 작업 병행", "영기 수련 · 문파 도움 · 물약 · 20시 필드 보스 · 22시 요화선녀 · 알림"),
            ("loop", "다시 화면 관찰", "각 행동 뒤 실제 화면을 재확인하고 다음 상태를 결정"),
        ],
    },
    "daily": {
        "title": "일일 전용 모드",
        "subtitle": "본편·서사·주간을 제외하고 수락한 일일 임무를 끝낸 뒤 일반 맵 AUTO로 복귀합니다.",
        "blocks": [
            ("start", "[일일] 탐색", "임무의 일일 탭과 우측 트래커를 끝까지 스크롤하여 '[일일]' 문구가 있는 항목만 선택"),
            ("decision", "일일 선택 한도 5/5?", "예 → 임무창을 다시 열지 않고 이미 수락된 [일일]을 트래커에서 진행 · 아니오 → 적합 일일 수락"),
            ("branches", "임무 종류 판단", [
                ("낙화의 전장", "채널 입장 → 중앙 이동 → AUTO → 100마리 확인 → 퇴장", "warn"),
                ("비경 탐험", "망자의 마을 3단계 → AUTO → 시간 종료까지 유지", "accent"),
                ("산신 지하동굴", "길라잡이 무료 포탈 → 지하 동굴 1 → AUTO", "ok"),
                ("일반 처치", "자동 진행 → 막히면 해당 지역 무료 길라잡이", "text"),
            ]),
            ("decision", "일반 맵으로 복귀?", "AUTO 사냥을 먼저 복구하고 30초 뒤 남은 일일 임무를 다시 확인"),
            ("loop", "진행할 일일 없음", "현재 위치 AUTO 사냥 + 영기·필드 보스·회복 등 공통 작업 유지"),
        ],
    },
    "story": {
        "title": "서사 전용 모드",
        "subtitle": "본편·일일·주간을 제외하고 현재 전투력에 맞는 서사 임무만 진행합니다.",
        "blocks": [
            ("start", "서사 탭 열기", "임무 화면에서 서사 탭만 확인"),
            ("decision", "적합 배지 있음?", "게임이 현재 전투력에 적합하다고 표시한 행만 선택"),
            ("action", "수락·자동 진행", "적합 행을 수락하고 자동 이동·대화·전투 진행"),
            ("decision", "적합 서사 없음?", "위험 임무는 건드리지 않고 다음 확인 주기까지 대기"),
            ("loop", "다음 서사 확인", "완료 후 서사 탭에서 다음 적합 임무 반복"),
        ],
    },
    "normal": {
        "title": "일반 모드",
        "subtitle": "퀘스트·임무창·추천 사냥터 이동 없이 사용자가 둔 현재 위치를 기준으로 사냥합니다.",
        "blocks": [
            ("start", "현재 지역 저장", "안전할 때 현재 지도와 복귀 기준 위치를 기억"),
            ("action", "현재 위치 AUTO", "임무창을 열지 않고 AUTO가 꺼지면 다시 켬"),
            ("decision", "HP 20% 이하?", "예 → AUTO 중지 후 밝은 도로 픽셀을 분석해 먼 길로 피신 · 분석 실패 시 안전 경로 사용"),
            ("decision", "HP 80% 이상 회복?", "예 → 저장한 원래 위치·지역으로 복귀 · 복귀 실패 시 현재 안전 위치 사용"),
            ("loop", "AUTO 재개", "복귀 완료를 확인하고 현재 위치 사냥 흐름 반복"),
        ],
    },
    "farm": {
        "title": "앵벌 모드",
        "subtitle": "퀘스트를 멈추고 전투력에 맞는 검증된 사냥터를 자동 선택합니다.",
        "blocks": [
            ("start", "전투력·제외 목록 확인", "현재 전투력, 허용 비율, 최근 사망으로 제외된 사냥터를 계산"),
            ("decision", "안전 후보 있음?", "가장 높은 안전 후보 선택 · 없으면 이동하지 않고 경고"),
            ("action", "무료 이동", "길라잡이 추천 사냥터의 권장 전투력을 확인한 뒤 무료 바로가기만 사용"),
            ("action", "도착 확인 → AUTO", "이동 종료와 필드 화면을 확인한 뒤 자동사냥 시작"),
            ("decision", "사망·정체·재선택 시각?", "문제 사냥터를 임시 제외하고 다음 안전 후보로 재평가"),
            ("loop", "앵벌 유지", "20시 필드 보스가 끝나면 기존 앵벌 사냥터로 복귀"),
        ],
    },
    "quest": {
        "title": "기본 퀘스트 모드",
        "subtitle": "트래커와 임무창을 이용해 진행 가능한 퀘스트를 선택하고 막히면 안전 사냥으로 전환합니다.",
        "blocks": [
            ("start", "퀘스트 탐색", "우측 트래커와 임무 탭에서 설정된 우선순위·전투력 조건을 확인"),
            ("action", "자동 진행", "퀘스트 클릭 → 이동 → NPC 대화·컷신 넘기기 → 전투 → 완료 확인"),
            ("decision", "사망?", "무료 복귀 → 같은 퀘스트 1회 재시도 → 다시 사망하면 해당 퀘스트 임시 차단"),
            ("decision", "진행 가능한 퀘스트 없음?", "전투력에 맞는 앵벌 사냥터에서 일정 시간 성장 후 다시 시도"),
            ("loop", "다음 퀘스트", "완료·실패·차단 결과를 반영해 다음 후보 선택"),
        ],
    },
    "common": {
        "title": "공통 안전·이벤트 흐름",
        "subtitle": "모든 모드에 공통으로 적용되며, 긴급 안전 판단이 일반 진행보다 우선합니다.",
        "blocks": [
            ("branches", "최우선 안전 판단", [
                ("사용자 조작", "2분 동안 모든 자동 입력 양보", "accent"),
                ("사망", "유료 즉시 부활 금지 · 무료 나가기/거점 복귀", "bad"),
                ("저체력", "잡던 몹 마무리·회복 또는 안전 지역 피신", "warn"),
                ("고위험 몬스터", "무료 길라잡이 회피 · 실패 시 일시정지", "ok"),
            ]),
            ("action", "전투 콘텐츠 잠금", "비경·낙화 안에서는 AUTO만 유지 · 공통 이벤트 시간이면 나가기 후 이벤트 최우선 참가"),
            ("action", "주기 작업", "영기 수련 · 문파 도움 · 부족 물약 구매 · 알림을 안전한 필드 시점에 실행"),
            ("decision", "20:58~21:05 필드 보스?", "월수금일 산신 산군대호 · 화목토 해골장군 → 대상과 2,000엽전을 확인한 경우만 유료 이동 → 전투 후 복귀"),
            ("decision", "21:58~22:12 요화선녀?", "메뉴 → 낙화의 전장 일반 채널 → 미니맵 중앙 이동 → AUTO · 사망 시 거점 부활 후 중앙 복귀"),
            ("loop", "화면 재검증", "입력 결과를 추측하지 않고 실제 화면 상태가 바뀌었는지 확인"),
        ],
    },
}


FLOW_CHOICES = [
    ("전체 종합", "overview"),
    ("일일 전용", "daily"),
    ("서사 전용", "story"),
    ("일반 모드", "normal"),
    ("앵벌 모드", "farm"),
    ("기본 퀘스트", "quest"),
    ("공통 안전·이벤트", "common"),
]


def runtime_status(bot):
    """실행 중인 Bot 객체를 UI용 읽기 전용 상태로 요약한다."""
    if bot is None:
        return {"running": False, "mode_key": "overview", "mode": "정지",
                "state": "대기", "place": "—", "hp": None, "auto": None,
                "decision": "시작 후 실제 판단 단계가 여기에 표시됩니다.", "active": ()}
    live = getattr(getattr(bot, "live", None), "data", {}) or {}
    base = ("daily" if getattr(bot, "daily_mode", False) else
            "story" if getattr(bot, "story_mode", False) else
            "normal" if getattr(bot, "normal_mode", False) else
            "farm" if getattr(bot, "farm_mode", False) or getattr(bot, "manual_farm", False) else "quest")
    state = getattr(bot, "_last_state", None) or "IDLE"
    rest = getattr(bot, "_rest", None)
    secret = bool(getattr(bot, "_secret_realm", False))
    mode_key, active = base, []

    if getattr(bot, "paused", False):
        mode_key, active, decision = "common", ["최우선 안전 판단"], "일시정지: 자동 입력을 보내지 않습니다."
    elif getattr(bot, "waiting_user", False):
        mode_key, active, decision = "common", ["최우선 안전 판단"], "사용자 조작 감지: 마지막 입력부터 2분간 양보합니다."
    elif state == "DEAD":
        mode_key, active, decision = "common", ["최우선 안전 판단"], "사망 감지: 유료 부활 없이 무료 복귀만 찾습니다."
    elif rest or getattr(bot, "_evade", None) or getattr(bot, "_evade_pending", False):
        mode_key, active = "common", ["최우선 안전 판단"]
        decision = "저체력 안전 처리: " + ({"finish": "현재 몬스터 마무리", "off": "AUTO 끄기",
                    "rest": "회복 대기", "on": "AUTO 재개"}.get(rest, "안전 지역 피신"))
    elif getattr(bot, "_nakhwa_recenter_pending", None):
        mode_key, active = "common", ["21:58~22:12 요화선녀?"]
        decision = "낙화 거점 부활 완료를 확인하고 중앙 재진입·AUTO 복구를 진행합니다."
    elif secret:
        active = ["낙화의 전장" if "낙화" in str(getattr(bot, "_map_name", "")) else "비경 탐험"]
        decision = "전투 콘텐츠 내부: 부가 이동을 잠그고 AUTO만 유지합니다."
    elif base == "daily":
        if getattr(bot, "_daily_return_hunt_pending", False):
            active, decision = ["일반 맵으로 복귀?"], "일반 맵 복귀 확인: 임무보다 AUTO 복구를 우선합니다."
        elif getattr(bot, "_want_hunt", False):
            active, decision = ["진행할 일일 없음"], "진행할 일일이 없어 현재 위치 AUTO를 유지합니다."
        elif state == "OVERLAY":
            active, decision = ["[일일] 탐색"], "일일 임무 화면을 읽고 수행 후보를 고르는 중입니다."
        else:
            active, decision = ["임무 종류 판단"], "[일일] 종류와 전용 이동 경로를 확인합니다."
    elif base == "story":
        if state == "OVERLAY":
            active, decision = ["적합 배지 있음?"], "서사 탭에서 현재 전투력에 맞는 적합 임무를 찾습니다."
        elif state in ("MOVING", "COMBAT", "DIALOG"):
            active, decision = ["수락·자동 진행"], "선택한 적합 서사 임무를 자동 진행합니다."
        else:
            active, decision = ["다음 서사 확인"], "다음 적합 서사 임무를 확인합니다."
    elif base == "normal":
        if getattr(bot, "_want_hunt", False):
            active, decision = ["현재 위치 AUTO"], "현재 위치에서 AUTO 활성화를 확인하는 중입니다."
        elif state == "MOVING":
            active, decision = ["HP 80% 이상 회복?"], "원래 사냥 위치로 복귀 중입니다."
        else:
            active, decision = ["현재 위치 AUTO"], "퀘스트를 열지 않고 현재 위치 사냥을 유지합니다."
    elif base == "farm":
        farm = getattr(bot, "farm", None)
        if farm and getattr(farm, "need_travel", False):
            active, decision = ["무료 이동"], "전투력에 맞는 안전 사냥터를 선택·이동하는 중입니다."
        elif state == "MOVING":
            active, decision = ["도착 확인 → AUTO"], "추천 사냥터로 이동 중입니다."
        else:
            active, decision = ["앵벌 유지"], getattr(farm, "status", "") or "도착한 사냥터에서 AUTO를 유지합니다."
    else:
        if state in ("MOVING", "COMBAT", "DIALOG", "OVERLAY"):
            active, decision = ["자동 진행"], "퀘스트 이동·대화·전투 결과를 확인하는 중입니다."
        else:
            active, decision = ["퀘스트 탐색"], "다음 진행 가능한 퀘스트를 찾습니다."

    labels = {"daily": "일일 전용", "story": "서사 전용", "normal": "일반", "farm": "앵벌", "quest": "기본 퀘스트", "common": "공통 안전"}
    states = {"IDLE": "대기", "MOVING": "이동", "COMBAT": "전투", "DIALOG": "대화", "OVERLAY": "메뉴·로딩", "DEAD": "사망"}
    return {"running": True, "mode_key": mode_key, "mode": labels.get(mode_key, mode_key),
            "state": states.get(state, state), "place": live.get("map") or getattr(bot, "_map_name", "") or "확인 중",
            "hp": live.get("hp"), "auto": live.get("auto_on"), "decision": decision, "active": tuple(active)}


def render_flow(key, colors, active_titles=()):
    """QTextBrowser에서 표시 가능한 표 기반 UML형 흐름도를 만든다."""
    page = FLOW_PAGES.get(key, FLOW_PAGES["overview"])
    color_map = {
        "start": colors["ok"], "action": colors["accent"],
        "decision": colors["warn"], "loop": colors["sub"],
        "accent": colors["accent"], "warn": colors["warn"],
        "ok": colors["ok"], "bad": colors["bad"], "text": colors["text"],
        "daily": colors["warn"], "story": colors["accent"], "normal": colors["accent"],
        "farm": colors["ok"], "quest": colors["text"],
    }

    active_titles = set(active_titles or ())

    def node(kind, title, body):
        color = color_map[kind]
        label = {"start": "시작", "action": "동작", "decision": "◆ 판단", "loop": "↻ 반복"}[kind]
        active = title in active_titles
        marker = f"<span style='color:{colors['ok']};font-weight:700'>● 현재 실행</span><br>" if active else ""
        border = colors["ok"] if active else color
        width = 4 if active else 2
        return (f"<table width='76%' align='center' cellpadding='10' cellspacing='0' "
                f"style='background:{colors['panel2']};border:{width}px solid {border}'>"
                f"<tr><td>{marker}<span style='color:{color};font-weight:700'>{label}</span> &nbsp;"
                f"<b>{escape(title)}</b><br><span style='color:{colors['sub']}'>{escape(body)}</span>"
                "</td></tr></table>")

    html = (f"<body style='color:{colors['text']};background:{colors['bg']}'>"
            f"<h1>{escape(page['title'])}</h1>"
            f"<p style='color:{colors['sub']}'>{escape(page['subtitle'])}</p>"
            f"<p><span style='color:{colors['warn']}'>◆ 판단</span> &nbsp;"
            f"<span style='color:{colors['accent']}'>▣ 동작</span> &nbsp;"
            f"<span style='color:{colors['ok']}'>● 시작</span> &nbsp;"
            f"<span style='color:{colors['sub']}'>↻ 반복</span></p>")
    for kind, title, body in page["blocks"]:
        if kind == "branches":
            cells = ""
            width = max(20, 100 // len(body))
            for branch_title, branch_body, branch_color in body:
                color = color_map[branch_color]
                active = branch_title in active_titles
                marker = f"<span style='color:{colors['ok']};font-weight:700'>● 현재</span><br>" if active else ""
                border = colors["ok"] if active else color
                border_width = 4 if active else 2
                cells += (f"<td width='{width}%' valign='top' style='background:{colors['panel2']};"
                          f"border:{border_width}px solid {border};padding:9px'>{marker}<b style='color:{color}'>"
                          f"{escape(branch_title)}</b><br><span style='color:{colors['sub']}'>"
                          f"{escape(branch_body)}</span></td>")
            html += (f"<p align='center' style='color:{colors['warn']}'><b>◆ {escape(title)}</b></p>"
                     f"<table width='100%' cellpadding='7' cellspacing='8'><tr>{cells}</tr></table>")
        else:
            html += node(kind, title, body)
        html += f"<p align='center' style='color:{colors['sub']};font-size:20px'>↓</p>"
    html += (f"<table width='100%' cellpadding='9' style='background:{colors['panel2']}'>"
             f"<tr><td><b style='color:{colors['accent']}'>우선순위</b> &nbsp; 사용자 조작·사망·저체력 "
             "→ 전투 콘텐츠 → 선택 모드 → 공통 주기 작업 → 화면 재확인</td></tr></table></body>")
    return html
