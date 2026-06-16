"""
회의실 예약 대시보드 (Streamlit)

실행:
    cd "구글 드라이브 프로젝트"
    streamlit run dashboard.py
"""
import html
from datetime import datetime, timedelta

import streamlit as st
import streamlit.components.v1 as components

from config import ROOM_RESOURCES, TEAM_ORDER, TEAM_MAP, WEEKDAY_KO, TZ
from calendar_api import (
    authenticate,
    has_oauth_config,
    get_auth_url,
    exchange_code_for_credentials,
    build_service_from_creds_json,
    get_user_email,
    fetch_team_events,
    fetch_my_bookings,
    fetch_room_busy_times,
    available_rooms_at,
    classify_events,
    classify_events_per_person,
    build_email_to_name,
    book_meeting,
    cancel_booking,
    build_sms,
    get_week_range,
)

# ─────────────────────────────────────────
# 페이지 설정
# ─────────────────────────────────────────
st.set_page_config(
    page_title="회의실 예약 대시보드",
    page_icon="📆",
    layout="wide",
)
st.title("📆 회의실 예약 대시보드")

# ─────────────────────────────────────────
# 인증 — per-user OAuth (Cloud) / 공유 토큰 (로컬)
# ─────────────────────────────────────────
REDIRECT_URI = "https://dcampcalerderslave-ppqgkxai9nddumkpn6dzjg.streamlit.app/"

if has_oauth_config():
    # ── Per-user OAuth 플로우 ──
    # 1) OAuth 콜백 처리 (?code=xxx)
    if "user_creds" not in st.session_state:
        code = st.query_params.get("code")
        if code:
            try:
                creds = exchange_code_for_credentials(code, REDIRECT_URI)
                svc = build_service_from_creds_json(creds.to_json())[0]
                email = get_user_email(svc)
                st.session_state.user_creds = creds.to_json()
                st.session_state.user_email = email
                st.query_params.clear()
                st.rerun()
            except Exception as e:
                st.error(f"로그인 실패: {e}")
                st.query_params.clear()

    # 2) 로그인 안 됐으면 로그인 화면
    if "user_creds" not in st.session_state:
        st.markdown("---")
        st.markdown(
            "<h2 style='text-align:center;margin-top:2rem;'>회의실 예약 대시보드</h2>"
            "<p style='text-align:center;color:#6b7280;'>"
            "@dcamp.kr Google 계정으로 로그인하면 회의실 예약이 가능합니다.</p>",
            unsafe_allow_html=True,
        )
        col1, col2, col3 = st.columns([2, 1, 2])
        auth_url = get_auth_url(REDIRECT_URI)
        col2.link_button("Google 계정으로 로그인", auth_url, type="primary", use_container_width=True)
        st.caption("로그인 후 새 탭에서 대시보드가 열립니다. 이 탭은 닫아도 됩니다.")
        st.markdown("---")
        st.stop()

    # 3) 로그인 된 상태 — 서비스 빌드 (만료 시 자동 갱신)
    try:
        service, updated_json = build_service_from_creds_json(st.session_state.user_creds)
        st.session_state.user_creds = updated_json
    except Exception:
        for k in ["user_creds", "user_email"]:
            st.session_state.pop(k, None)
        st.rerun()

    user_email = st.session_state.get("user_email", "")
    user_cols = st.columns([5, 1])
    user_cols[0].caption(f"**{user_email}** 로그인됨")
    if user_cols[1].button("로그아웃", use_container_width=True):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()

else:
    # ── 로컬 / 공유 토큰 fallback ──
    if "service" not in st.session_state:
        with st.spinner("Google 인증 중..."):
            st.session_state.service = authenticate()
    service = st.session_state.service

# ─────────────────────────────────────────
# 주차 선택
# ─────────────────────────────────────────
week_label = st.radio(
    "주차 선택",
    ["이번주", "다음주", "다다음주"],
    horizontal=True,
    index=0,
)
week_offset = {"이번주": 0, "다음주": 1, "다다음주": 2}[week_label]

time_min, time_max = get_week_range(week_offset)

caption_row = st.columns([5, 1])
caption_row[0].caption(
    f"조회 기간: **{time_min.strftime('%Y-%m-%d')} ~ {time_max.strftime('%Y-%m-%d')}**"
)
refresh = caption_row[1].button("새로고침", use_container_width=True)

# ─────────────────────────────────────────
# 캐시 관리
# ─────────────────────────────────────────
resources_version = hash(tuple(ROOM_RESOURCES.items()))
cache_key = f"cache_{week_offset}_{resources_version}"
if refresh and cache_key in st.session_state:
    del st.session_state[cache_key]

if cache_key not in st.session_state:
    with st.spinner("일정 조회 중..."):
        events, errors = fetch_team_events(service, week_offset)
        email_to_name = build_email_to_name(service)
        by_date_team = classify_events(events, ROOM_RESOURCES, email_to_name)
        by_date_person = classify_events_per_person(events)
        my_bookings = fetch_my_bookings(service, week_offset)
        room_busy = fetch_room_busy_times(service, week_offset, ROOM_RESOURCES)
        st.session_state[cache_key] = {
            "by_date_team":   by_date_team,
            "by_date_person": by_date_person,
            "email_to_name":  email_to_name,
            "my_bookings":    my_bookings,
            "room_busy":      room_busy,
            "errors":         errors,
        }

cache = st.session_state[cache_key]
by_date_team   = cache["by_date_team"]
by_date_person = cache["by_date_person"]
email_to_name  = cache["email_to_name"]
my_bookings    = cache["my_bookings"]
room_busy      = cache["room_busy"]
errors         = cache["errors"]


def _cancel_mirror(orig_id: str, mid: str, mroom: str, span: tuple | None) -> dict:
    """미러 하나 취소 + my_bookings/room_busy in-place 패치.

    span: (start, end) — room_busy에서 제거할 시간대. 고아 미러는 None.
    """
    result = cancel_booking(service, orig_id, week_offset, mirror_event_ids={mid})
    deleted = set(result.get("deleted_mirror_ids", []))
    if result["status"] == "not_found":
        deleted = {mid}
    if deleted:
        if span and mroom in room_busy:
            room_busy[mroom] = [s for s in room_busy[mroom] if s != span]
        remaining = [m for m in my_bookings.get(orig_id, []) if m["id"] not in deleted]
        if remaining:
            my_bookings[orig_id] = remaining
        else:
            my_bookings.pop(orig_id, None)
    return result


def _mirror_time_info(mirror: dict, ev_start, ev_end):
    """미러의 시작/끝 datetime + 원본 시간과 불일치 여부.

    Returns: (m_start, m_end, time_mismatch) — 파싱 실패 시 (None, None, False).
    """
    raw_s = mirror.get("start", {}).get("dateTime")
    raw_e = mirror.get("end", {}).get("dateTime")
    if not (raw_s and raw_e):
        return None, None, False
    try:
        ms = datetime.fromisoformat(raw_s).astimezone(TZ)
        me = datetime.fromisoformat(raw_e).astimezone(TZ)
        return ms, me, (ms != ev_start or me != ev_end)
    except Exception:
        return None, None, False


def _safe_esc(text: str) -> str:
    """HTML + 마크다운 특수문자 모두 이스케이프 (st.markdown 안에서 안전하게 표시)"""
    s = html.escape(text, quote=True)
    for ch, entity in (("~", "&#126;"), ("*", "&#42;"), ("_", "&#95;"), ("`", "&#96;")):
        s = s.replace(ch, entity)
    return s


# ─────────────────────────────────────────
# 팀 현황 스트립 CSS + 렌더러
# ─────────────────────────────────────────
st.markdown(
    """
    <style>
    .team-strip {
        display: flex;
        overflow-x: auto;
        gap: 8px;
        padding: 8px 2px 12px;
        width: 100%;
        scrollbar-width: thin;
    }
    .tm-card {
        min-width: 160px;
        max-width: 180px;
        flex: 0 0 auto;
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 8px;
        padding: 8px 10px;
        background: rgba(255,255,255,0.04);
    }
    .tm-name {
        font-weight: 700;
        font-size: 13px;
        line-height: 1.2;
    }
    .tm-team {
        font-size: 10px;
        color: #9ca3af;
        margin-bottom: 6px;
    }
    .tm-events { display: flex; flex-direction: column; gap: 4px; }
    .tm-ev {
        font-size: 11px;
        font-weight: 500;
        padding: 4px 8px;
        background: rgba(99,102,241,0.15);
        color: #818cf8;
        border-radius: 4px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    .tm-ev-ooo {
        background: rgba(239,68,68,0.2);
        color: #f87171;
        font-weight: 600;
    }
    .tm-ev-loc {
        background: rgba(59,130,246,0.2);
        color: #60a5fa;
        font-weight: 600;
    }
    .tm-ev-focus {
        background: rgba(139,92,246,0.2);
        color: #a78bfa;
    }
    .tm-free { font-size: 11px; color: #4b5563; font-style: italic; }

    /* 커스텀 툴팁 — hover(데스크톱) + tap/focus(모바일) */
    .ev-tip {
        position: relative;
        cursor: help;
        border-bottom: 1px dotted #9ca3af;
    }
    .ev-tip .ev-tip-body {
        display: none;
        position: absolute;
        top: 100%;
        left: 0;
        margin-top: 4px;
        background: #1f2937;
        color: #f3f4f6;
        padding: 10px 14px;
        border-radius: 8px;
        font-size: 12px;
        line-height: 1.5;
        white-space: normal;
        min-width: 260px;
        max-width: 400px;
        z-index: 9999;
        box-shadow: 0 4px 12px rgba(0,0,0,0.25);
        pointer-events: none;
    }
    .ev-tip:hover .ev-tip-body,
    .ev-tip:focus .ev-tip-body {
        display: block;
    }
    .ev-tip:focus { outline: none; }
    .ev-tip-label { font-weight: 600; color: #93c5fd; }

    /* 예약 취소 popover 안의 ✕ 버튼 — 박스 제거, 텍스트만 */
    [data-baseweb="popover"] .stButton > button {
        border: none !important;
        background: transparent !important;
        box-shadow: none !important;
        padding: 2px 6px !important;
        min-height: auto !important;
        font-size: 16px !important;
        color: #9ca3af !important;
    }
    [data-baseweb="popover"] .stButton > button:hover {
        color: #ef4444 !important;
        background: transparent !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# 근무지/휴가/집중시간 같은 "상태" 이벤트 + "종일" 이벤트만 스트립에 표시.
# 종일 기준을 넣는 이유: 사람들이 workingLocation 대신 종일 일정으로 "선릉" "재택" 같이
# 직접 적어놓는 경우가 많아서.
_STATUS_EV_CLASS = {
    "workingLocation": "tm-ev tm-ev-loc",
    "outOfOffice":     "tm-ev tm-ev-ooo",
    "focusTime":       "tm-ev tm-ev-focus",
}


def _is_all_day(event: dict) -> bool:
    start = event.get("start", {}) or {}
    return "date" in start and "dateTime" not in start


def render_team_strip(date_str: str) -> None:
    day_map = by_date_person.get(date_str, {})
    current_user = (st.session_state.get("user_email") or "").strip().lower()
    cards: list[str] = []
    for email, team in TEAM_MAP.items():
        if current_user and email.strip().lower() == current_user:
            continue
        name = email_to_name.get(email, email.split("@")[0])
        evs = day_map.get(email, [])

        event_html: list[str] = []
        for ev in evs:
            etype = ev.get("eventType", "default")
            is_status_type = etype in _STATUS_EV_CLASS
            if not (is_status_type or _is_all_day(ev)):
                continue
            cls = _STATUS_EV_CLASS.get(etype, "tm-ev")
            title = (ev.get("summary") or "").strip() or etype
            esc = html.escape(title)
            event_html.append(f'<div class="{cls}" title="{esc}">{esc}</div>')
        if not event_html:
            event_html.append('<div class="tm-free">—</div>')

        cards.append(
            f'<div class="tm-card">'
            f'<div class="tm-name">{html.escape(name)}</div>'
            f'<div class="tm-team">{html.escape(team)}</div>'
            f'<div class="tm-events">{"".join(event_html)}</div>'
            f'</div>'
        )
    st.markdown(
        f'<div class="team-strip">{"".join(cards)}</div>',
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────
# 오류 표시
# ─────────────────────────────────────────
if errors:
    with st.expander(f"⚠️ 조회 오류 {len(errors)}건"):
        for e in errors:
            st.text(e)

# ─────────────────────────────────────────
# 고아 예약 — 원본 회의가 사라진 미러 (취소 누락 방지)
# ─────────────────────────────────────────
_all_week_event_ids: set[str] = set()
for _date_evs in by_date_team.values():
    for _team_evs in _date_evs.values():
        for _ev in _team_evs:
            _all_week_event_ids.add(_ev["id"])

_orphan_mirrors: list[tuple[str, dict]] = []
for _orig_id, _mirrors in my_bookings.items():
    if _orig_id in _all_week_event_ids:
        continue
    for _m in _mirrors:
        _orphan_mirrors.append((_orig_id, _m))

if _orphan_mirrors:
    with st.expander(
        f"⚠️ 원본 회의가 없는 예약 {len(_orphan_mirrors)}건 — 삭제됐거나 다른 주차로 이동한 회의",
        expanded=True,
    ):
        for _orig_id, _m in _orphan_mirrors:
            _mroom = (
                _m.get("extendedProperties", {})
                .get("private", {})
                .get("room_name", "?")
            )
            _mtitle = (
                _m.get("extendedProperties", {})
                .get("private", {})
                .get("original_title", _m.get("summary", "(제목 없음)"))
            )
            _ms_raw = _m.get("start", {}).get("dateTime")
            _me_raw = _m.get("end", {}).get("dateTime")
            try:
                _ms = datetime.fromisoformat(_ms_raw).astimezone(TZ)
                _me = datetime.fromisoformat(_me_raw).astimezone(TZ)
                _time_str = (
                    f"{_ms.strftime('%m/%d %H:%M')}~{_me.strftime('%H:%M')}"
                )
            except Exception:
                _time_str = "(시간 정보 없음)"
            _mid = _m["id"]
            oc = st.columns([3, 2, 4, 1])
            oc[0].markdown(f"🏢 **{_mroom}**")
            oc[1].write(_time_str)
            oc[2].write(_mtitle)
            if oc[3].button("✕ 취소", key=f"orphan_cancel_{_mid}", type="secondary"):
                with st.spinner("취소 중..."):
                    _res = _cancel_mirror(_orig_id, _mid, _mroom, span=None)
                if _res["status"] in ("ok", "not_found"):
                    st.success("취소됨")
                else:
                    st.error(_res["message"])
                st.rerun()

# ─────────────────────────────────────────
# 메인 테이블 (요일별 탭)
# ─────────────────────────────────────────
WEEKDAY_TAB_LABELS = ["월", "화", "수", "목", "금"]
DAY_OPTIONS = WEEKDAY_TAB_LABELS + ["📋 문자 생성"]

active_day = st.radio(
    "요일",
    DAY_OPTIONS,
    horizontal=True,
    key="active_day_tab",
    label_visibility="collapsed",
)

COL_WIDTHS = [1.4, 1.3, 2.8, 1.2, 2.0, 1.8, 1.6]

monday, _ = get_week_range(week_offset)

# 선택된 요일만 렌더 — (st.tabs는 첫 rerun 때 월요일로 리셋되는 이슈가 있어 radio로 대체)
if active_day in WEEKDAY_TAB_LABELS:
    _selected_idx = WEEKDAY_TAB_LABELS.index(active_day)
    _tab_iter = [(_selected_idx, (st.container(), active_day))]
else:
    _tab_iter = []

for weekday_idx, (tab, day_label) in _tab_iter:
    date = monday + timedelta(days=weekday_idx)
    date_str = date.strftime("%Y-%m-%d")

    with tab:
        # 팀 현황 카드 스트립 (가로 스크롤)
        render_team_strip(date_str)

        team_events_for_date = by_date_team.get(date_str, {})

        # 팀 간 중복 제거
        all_evs: list = []
        seen: set = set()
        for team in TEAM_ORDER:
            for ev in team_events_for_date.get(team, []):
                if ev["id"] in seen:
                    continue
                seen.add(ev["id"])
                all_evs.append(ev)

        if not all_evs:
            st.info(f"{date.month}/{date.day} ({day_label}) 일정이 없습니다.")
            continue

        all_evs.sort(key=lambda x: x["start"])

        # 미배정 이벤트와 해당 multiselect 키 준비
        unassigned: list[tuple[int, dict, str]] = []
        for idx, ev in enumerate(all_evs):
            if ev["rooms"]:
                continue
            if ev["id"] in my_bookings:
                continue
            sel_key = f"sel_all_{date_str}_{idx}_{ev['id']}"
            unassigned.append((idx, ev, sel_key))

        # 아직 예약 전이지만 다른 이벤트 드롭다운에서 이미 선택된 방들 — 겹치는 시간대의 드롭다운에서 제외
        tentative_bookings: list[tuple] = []
        for _, t_ev, t_key in unassigned:
            for t_room in st.session_state.get(t_key, []):
                tentative_bookings.append((t_ev["start"], t_ev["end"], t_room, t_key))

        selected_rooms_count = sum(
            len(st.session_state.get(k, [])) for _, _, k in unassigned
        )
        selected_evs_count = sum(
            1 for _, _, k in unassigned
            if len(st.session_state.get(k, [])) > 0
        )

        # 요일 헤더 + 일괄 예약 버튼
        head_cols = st.columns([5, 2])
        head_cols[0].subheader(f"{date.month}/{date.day} ({day_label})")

        batch_clicked = False
        if unassigned:
            btn_label = (
                f"🚀 일괄 예약 "
                f"({selected_evs_count}/{len(unassigned)}건 · 회의실 {selected_rooms_count}개)"
            )
            batch_clicked = head_cols[1].button(
                btn_label,
                key=f"batch_{date_str}",
                type="primary",
                disabled=(selected_rooms_count == 0),
                use_container_width=True,
            )

        # 이전 배치 결과 표시 (있으면)
        result_key = f"batch_result_{date_str}"
        if result_key in st.session_state:
            results = st.session_state[result_key]
            ok = [r for r in results if r["status"] == "ok"]
            dup = [r for r in results if r["status"] == "duplicate"]
            fails = [r for r in results if r["status"] not in ("ok", "duplicate")]
            with st.expander(
                f"📋 일괄 예약 결과 — ✅ {len(ok)} / ⚠ {len(dup)} / ❌ {len(fails)}",
                expanded=bool(fails),
            ):
                for r in ok:
                    st.success(f"✅ {r['room']} · {r['title']}")
                for r in dup:
                    st.warning(f"⚠ {r['room']} · {r['title']} — {r['message']}")
                for r in fails:
                    st.error(f"❌ {r.get('room', '-')} · {r['title']} — {r['message']}")
                if st.button("결과 닫기", key=f"close_{result_key}"):
                    del st.session_state[result_key]
                    st.rerun()

        header = st.columns(COL_WIDTHS)
        header[0].caption("**팀**")
        header[1].caption("**소유자**")
        header[2].caption("**회의 제목**")
        header[3].caption("**시간**")
        header[4].caption("**참석자**")
        header[5].caption("**회의실 현황**")
        header[6].caption("**회의실 선택**")

        for idx, ev in enumerate(all_evs):
            row = st.columns(COL_WIDTHS)

            row[0].write(" / ".join(ev.get("teams", [])) or "—")
            row[1].write(ev.get("organizer_name") or "—")
            # 제목 + 장소/설명 커스텀 툴팁 (hover + 모바일 tap)
            tip_html_parts: list[str] = []
            if ev.get("location"):
                loc = _safe_esc(ev["location"]).replace("\n", " ")
                tip_html_parts.append(f'<span class="ev-tip-label">장소</span> {loc}')
            if ev.get("description"):
                desc = ev["description"].strip()
                desc_esc = _safe_esc(desc).replace("\n", "<br>")
                tip_html_parts.append(f'<span class="ev-tip-label">설명</span> {desc_esc}')
            if tip_html_parts:
                title_esc = _safe_esc(ev["title"])
                tip_body = "<br>".join(tip_html_parts)
                row[2].markdown(f'<span class="ev-tip" tabindex="0">{title_esc}<span class="ev-tip-body">{tip_body}</span></span>', unsafe_allow_html=True)
            else:
                row[2].write(ev["title"])
            row[3].write(f"{ev['start'].strftime('%H:%M')}~{ev['end'].strftime('%H:%M')}")

            attendees = ev.get("attendees", [])
            row[4].write(", ".join(attendees) if attendees else "—")

            has_original_room = bool(ev["rooms"])
            my_mirror_list = my_bookings.get(ev["id"], [])
            my_rooms = [
                m.get("extendedProperties", {})
                .get("private", {})
                .get("room_name", "?")
                for m in my_mirror_list
            ]

            key_base = f"all_{date_str}_{idx}_{ev['id']}"

            if has_original_room:
                row[5].markdown(f"🏢 `{' / '.join(ev['rooms'])}` _(원본)_")
                row[6].write("—")
            elif my_rooms:
                # 미러별 시간 정보 + 불일치 여부를 한 번만 계산
                mirror_infos = [
                    (m, *_mirror_time_info(m, ev["start"], ev["end"]))
                    for m in my_mirror_list
                ]
                any_mismatch = any(mm for _, _, _, mm in mirror_infos)
                trigger_label = f"{'⚠️ ' if any_mismatch else '✅ '}{' / '.join(my_rooms)} ▾"
                with row[5].popover(trigger_label, use_container_width=True):
                    st.caption("내 예약 — 시간 불일치 시 🔁 동기화")
                    for mirror, m_start, m_end, time_mismatch in mirror_infos:
                        mroom = (
                            mirror.get("extendedProperties", {})
                            .get("private", {})
                            .get("room_name", "?")
                        )
                        mid = mirror["id"]

                        if time_mismatch:
                            c = st.columns([2, 3, 1, 1])
                            c[0].write(f"🏢 {mroom}")
                            c[1].markdown(
                                f"⚠️ {m_start.strftime('%H:%M')}~{m_end.strftime('%H:%M')} → "
                                f"**{ev['start'].strftime('%H:%M')}~{ev['end'].strftime('%H:%M')}**"
                            )
                            sync_btn = c[2].button(
                                "🔁",
                                key=f"sync_{key_base}_{mid}",
                                help="원본 시간으로 재예약",
                            )
                            cancel_btn = c[3].button(
                                "✕",
                                key=f"cancel_one_{key_base}_{mid}",
                                type="secondary",
                            )
                        else:
                            c = st.columns([5, 1])
                            c[0].write(f"🏢 {mroom}")
                            sync_btn = False
                            cancel_btn = c[1].button(
                                "✕",
                                key=f"cancel_one_{key_base}_{mid}",
                                type="secondary",
                            )

                        if sync_btn:
                            old_span = (m_start, m_end) if m_start and m_end else None
                            with st.spinner("동기화 중..."):
                                cres = _cancel_mirror(ev["id"], mid, mroom, span=old_span)
                                if cres["status"] in ("ok", "not_found", "partial"):
                                    bres = book_meeting(
                                        service,
                                        ev,
                                        ROOM_RESOURCES[mroom],
                                        mroom,
                                    )
                                    if bres["status"] == "ok":
                                        st.success(f"{mroom} 동기화 완료")
                                        if bres.get("event"):
                                            my_bookings.setdefault(ev["id"], []).append(bres["event"])
                                            room_busy.setdefault(mroom, []).append((ev["start"], ev["end"]))
                                    else:
                                        st.error(f"재예약 실패: {bres['message']}")
                                else:
                                    st.error(f"기존 미러 취소 실패: {cres['message']}")
                            st.rerun()

                        if cancel_btn:
                            with st.spinner("취소 중..."):
                                result = _cancel_mirror(
                                    ev["id"], mid, mroom, span=(ev["start"], ev["end"])
                                )
                            status = result["status"]
                            if status == "ok":
                                st.success(result["message"])
                            elif status == "partial":
                                st.warning(result["message"])
                            elif status == "not_found":
                                st.info("이미 취소된 예약입니다. 목록에서 제거합니다.")
                            else:
                                st.error(result["message"])
                            st.rerun()
                row[6].write("—")
            else:
                row[5].markdown("🔴 **미배정**")
                my_sel_key = f"sel_{key_base}"
                available_set = set(available_rooms_at(room_busy, ev["start"], ev["end"]))
                # 다른 이벤트에서 이미 선택된 방이 시간 겹치면 제외 (같은 배치 내 중복 방지)
                for t_start, t_end, t_room, t_key in tentative_bookings:
                    if t_key == my_sel_key:
                        continue
                    if t_start < ev["end"] and t_end > ev["start"]:
                        available_set.discard(t_room)
                # 내가 이미 선택한 방은 옵션에 항상 포함 (multiselect 값 유지)
                my_current = set(st.session_state.get(my_sel_key, []))
                options = sorted(available_set | my_current)
                total = len(ROOM_RESOURCES)
                hidden = total - len(options)
                if options:
                    row[6].multiselect(
                        "회의실",
                        options,
                        key=my_sel_key,
                        label_visibility="collapsed",
                        placeholder=(
                            f"회의실 선택 (가능 {len(options)}개"
                            + (f" · 예약중 {hidden}개 숨김" if hidden else "")
                            + ")"
                        ),
                    )
                else:
                    row[6].markdown("_예약 가능한 회의실 없음_")

        # 일괄 예약 처리 — 하나의 이벤트에 여러 회의실 선택 시 회의실 개수만큼 book_meeting 호출
        if batch_clicked:
            pairs = []
            for _, ev, k in unassigned:
                rooms_selected = st.session_state.get(k, [])
                for room_choice in rooms_selected:
                    pairs.append((ev, room_choice))

            batch_results: list[dict] = []
            progress = st.progress(0.0, text=f"{len(pairs)}건 예약 시작...")
            for i, (ev, room_choice) in enumerate(pairs):
                progress.progress(
                    i / len(pairs),
                    text=f"[{i+1}/{len(pairs)}] {room_choice} · {ev['title']}",
                )
                result = book_meeting(
                    service,
                    ev,
                    ROOM_RESOURCES[room_choice],
                    room_choice,
                )
                batch_results.append({
                    "title":   ev["title"],
                    "room":    room_choice,
                    "status":  result["status"],
                    "message": result["message"],
                })
                # 캐시 패치 — 성공 건은 생성된 미러를 my_bookings에 바로 추가
                if result["status"] == "ok" and result.get("event"):
                    my_bookings.setdefault(ev["id"], []).append(result["event"])
                    room_busy.setdefault(room_choice, []).append((ev["start"], ev["end"]))
            progress.progress(1.0, text=f"완료: {len(pairs)}건 처리됨")
            progress.empty()

            st.session_state[result_key] = batch_results
            st.rerun()

# ─────────────────────────────────────────
# 문자 생성 탭
# ─────────────────────────────────────────
if active_day == "📋 문자 생성":
    st.markdown("### 📋 안내 문자")
    st.caption("각 박스의 내용을 복사하세요.")

    date_keys = sorted(by_date_team.keys())
    if not date_keys:
        st.info("조회된 일정이 없습니다.")
    else:
        for date_str in date_keys:
            team_events_for_date = by_date_team[date_str]
            sms = build_sms(date_str, team_events_for_date, my_bookings)
            date = datetime.strptime(date_str, "%Y-%m-%d")
            weekday = WEEKDAY_KO[date.weekday()]

            head_cols = st.columns([5, 1])
            head_cols[0].markdown(
                f"<h3 style='margin:0 0 4px 0;'>{date.month}/{date.day} ({weekday})</h3>",
                unsafe_allow_html=True,
            )
            btn_id = f"copy-{date_str}"
            data_id = f"data-{date_str}"
            sms_escaped = html.escape(sms)
            with head_cols[1]:
                components.html(
                    f"""
                    <textarea id="{data_id}" style="display:none;">{sms_escaped}</textarea>
                    <div style="text-align:right;">
                      <button id="{btn_id}"
                        onclick="
                          var t=document.getElementById('{data_id}').value;
                          navigator.clipboard.writeText(t).then(function(){{
                            var b=document.getElementById('{btn_id}');
                            var o=b.innerText;
                            b.innerText='\u2713 \ubcf5\uc0ac\ub428';
                            setTimeout(function(){{b.innerText=o;}},1500);
                          }});"
                        style="padding:6px 14px;font-size:13px;border-radius:6px;
                          border:1px solid #4b5563;background:#1f2937;color:#f3f4f6;
                          cursor:pointer;">\U0001f4cb \ubcf5\uc0ac</button>
                    </div>
                    """,
                    height=44,
                )
            st.text_area(
                "sms",
                sms,
                height=300,
                key=f"sms_{date_str}_{hash(sms)}",
                label_visibility="collapsed",
            )