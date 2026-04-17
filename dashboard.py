"""
회의실 예약 대시보드 (Streamlit)

실행:
    cd "구글 드라이브 프로젝트"
    streamlit run dashboard.py
"""
import html
from datetime import datetime, timedelta

import streamlit as st

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
        st.markdown("### Google 계정으로 로그인")
        auth_url = get_auth_url(REDIRECT_URI)
        st.markdown(
            f'<a href="{auth_url}" target="_self" style="display:inline-block;'
            f"padding:0.6rem 1.2rem;background-color:#ff4b4b;color:white;"
            f'border-radius:0.5rem;text-decoration:none;font-weight:600;">'
            f"Google 계정으로 로그인</a>",
            unsafe_allow_html=True,
        )
        st.caption("@dcamp.kr 계정으로 로그인하면 회의실 예약이 가능합니다.")
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
        by_date_team = classify_events(events, ROOM_RESOURCES)
        by_date_person = classify_events_per_person(events)
        email_to_name = build_email_to_name(events)
        my_bookings = fetch_my_bookings(service, week_offset)
        st.session_state[cache_key] = {
            "by_date_team":   by_date_team,
            "by_date_person": by_date_person,
            "email_to_name":  email_to_name,
            "my_bookings":    my_bookings,
            "errors":         errors,
        }

cache = st.session_state[cache_key]
by_date_team   = cache["by_date_team"]
by_date_person = cache["by_date_person"]
email_to_name  = cache["email_to_name"]
my_bookings    = cache["my_bookings"]
errors         = cache["errors"]


def invalidate_cache():
    """예약/취소 후 캐시 갱신"""
    if cache_key in st.session_state:
        del st.session_state[cache_key]


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
        border: 1px solid #e5e7eb;
        border-radius: 8px;
        padding: 8px 10px;
        background: #ffffff;
    }
    .tm-name {
        font-weight: 600;
        font-size: 13px;
        color: #111827;
        line-height: 1.2;
    }
    .tm-team {
        font-size: 10px;
        color: #6b7280;
        margin-bottom: 6px;
    }
    .tm-events { display: flex; flex-direction: column; gap: 3px; }
    .tm-ev {
        font-size: 11px;
        padding: 3px 6px;
        background: #f3f4f6;
        border-radius: 4px;
        color: #374151;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    .tm-ev-ooo { background: #fee2e2; color: #991b1b; }
    .tm-ev-loc { background: #dbeafe; color: #1e40af; }
    .tm-ev-focus { background: #ede9fe; color: #5b21b6; }
    .tm-free { font-size: 11px; color: #9ca3af; font-style: italic; }
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
    cards: list[str] = []
    for email, team in TEAM_MAP.items():
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
# 메인 테이블 (요일별 탭)
# ─────────────────────────────────────────
WEEKDAY_TAB_LABELS = ["월", "화", "수", "목", "금"]
tabs = st.tabs(WEEKDAY_TAB_LABELS + ["📋 문자 생성"])

COL_WIDTHS = [1.4, 1.3, 2.8, 1.2, 2.0, 1.8, 1.6, 1.0]

monday, _ = get_week_range(week_offset)

for weekday_idx, (tab, day_label) in enumerate(zip(tabs[:-1], WEEKDAY_TAB_LABELS)):
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
        header[7].caption("**액션**")

        for idx, ev in enumerate(all_evs):
            row = st.columns(COL_WIDTHS)

            row[0].write(" / ".join(ev.get("teams", [])) or "—")
            row[1].write(ev.get("organizer_name") or "—")
            # 제목에 장소/설명 툴팁 (hover 시 표시)
            tooltip_parts = []
            if ev.get("location"):
                tooltip_parts.append(f"장소: {ev['location']}")
            if ev.get("description"):
                tooltip_parts.append(f"설명: {ev['description']}")
            if tooltip_parts:
                tip = html.escape("\n".join(tooltip_parts))
                title_esc = html.escape(ev["title"])
                row[2].markdown(
                    f'<span title="{tip}" style="cursor:help">{title_esc}</span>',
                    unsafe_allow_html=True,
                )
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

            if has_original_room:
                row[5].markdown(f"🏢 `{' / '.join(ev['rooms'])}` _(원본)_")
            elif my_rooms:
                row[5].markdown(f"✅ `{' / '.join(my_rooms)}` _(내 예약)_")
            else:
                row[5].markdown("🔴 **미배정**")

            key_base = f"all_{date_str}_{idx}_{ev['id']}"

            if has_original_room:
                row[6].write("—")
                row[7].write("—")
            elif my_rooms:
                row[6].write("—")
                if row[7].button("취소", key=f"cancel_{key_base}", type="secondary"):
                    with st.spinner("취소 중..."):
                        result = cancel_booking(service, ev["id"], week_offset)
                    if result["status"] == "ok":
                        st.success(result["message"])
                    elif result["status"] == "partial":
                        st.warning(result["message"])
                    else:
                        st.error(result["message"])
                    # 캐시 패치 — 재조회 없이 삭제된 미러만 제거
                    deleted = set(result.get("deleted_mirror_ids", []))
                    if deleted:
                        remaining = [
                            m for m in my_bookings.get(ev["id"], [])
                            if m["id"] not in deleted
                        ]
                        if remaining:
                            my_bookings[ev["id"]] = remaining
                        else:
                            my_bookings.pop(ev["id"], None)
                    st.rerun()
            else:
                row[6].multiselect(
                    "회의실",
                    sorted(ROOM_RESOURCES.keys()),
                    key=f"sel_{key_base}",
                    label_visibility="collapsed",
                    placeholder="회의실 선택 (여러 개 가능)",
                )
                row[7].write("—")

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
            progress.progress(1.0, text=f"완료: {len(pairs)}건 처리됨")
            progress.empty()

            st.session_state[result_key] = batch_results
            # invalidate_cache() 하지 않음 — 캐시는 위에서 in-place로 패치됨
            st.rerun()

# ─────────────────────────────────────────
# 문자 생성 탭
# ─────────────────────────────────────────
with tabs[-1]:
    st.markdown("### 📋 안내 문자")
    st.caption("각 박스의 내용을 복사하세요.")

    date_keys = sorted(by_date_team.keys())
    rendered = 0
    if not date_keys:
        st.info("조회된 일정이 없습니다.")
    else:
        for date_str in date_keys:
            team_events_for_date = by_date_team[date_str]
            sms = build_sms(date_str, team_events_for_date, my_bookings)
            if not sms:
                continue
            date = datetime.strptime(date_str, "%Y-%m-%d")
            weekday = WEEKDAY_KO[date.weekday()]
            # key에 내용 해시를 포함 — session_state가 value를 덮어쓰지 않도록
            # (Streamlit은 같은 key면 session_state[key]를 우선해서 옛 SMS가 고정되는 이슈가 있음)
            st.text_area(
                f"{date.month}/{date.day} ({weekday})",
                sms,
                height=300,
                key=f"sms_{date_str}_{hash(sms)}",
            )
            rendered += 1
        if rendered == 0:
            st.info("배정된 회의실이 없어 안내할 일정이 없습니다.")