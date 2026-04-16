"""
회의실 예약 대시보드 (Streamlit)

실행:
    cd "구글 드라이브 프로젝트"
    streamlit run dashboard.py
"""
from datetime import datetime

import streamlit as st

from config import ROOM_RESOURCES, TEAM_ORDER, WEEKDAY_KO, TZ
from calendar_api import (
    authenticate,
    fetch_team_events,
    fetch_my_bookings,
    classify_events,
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
# 인증 (1회)
# ─────────────────────────────────────────
if "service" not in st.session_state:
    with st.spinner("Google 인증 중..."):
        st.session_state.service = authenticate()
service = st.session_state.service

# ─────────────────────────────────────────
# 주차 선택
# ─────────────────────────────────────────
top_cols = st.columns([3, 1, 1])
with top_cols[0]:
    week_label = st.radio(
        "주차 선택",
        ["지난주", "이번주", "다음주"],
        horizontal=True,
        index=1,
    )
week_offset = {"지난주": -1, "이번주": 0, "다음주": 1}[week_label]

with top_cols[1]:
    st.write("")
    st.write("")
    refresh = st.button("🔄 새로고침", use_container_width=True)

time_min, time_max = get_week_range(week_offset)
st.caption(
    f"조회 기간: **{time_min.strftime('%Y-%m-%d')} ~ {time_max.strftime('%Y-%m-%d')}**"
)

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
        my_bookings = fetch_my_bookings(service, week_offset)
        st.session_state[cache_key] = {
            "by_date_team": by_date_team,
            "my_bookings":  my_bookings,
            "errors":       errors,
        }

cache = st.session_state[cache_key]
by_date_team = cache["by_date_team"]
my_bookings  = cache["my_bookings"]
errors       = cache["errors"]


def invalidate_cache():
    """예약/취소 후 캐시 갱신"""
    if cache_key in st.session_state:
        del st.session_state[cache_key]


# ─────────────────────────────────────────
# 오류 표시
# ─────────────────────────────────────────
if errors:
    with st.expander(f"⚠️ 조회 오류 {len(errors)}건"):
        for e in errors:
            st.text(e)

# ─────────────────────────────────────────
# 메인 테이블 (탭별)
# ─────────────────────────────────────────
tabs = st.tabs(TEAM_ORDER + ["📋 문자 생성"])

for tab, team in zip(tabs[:-1], TEAM_ORDER):
    with tab:
        date_keys = sorted(by_date_team.keys())
        if not date_keys:
            st.info("조회된 일정이 없습니다.")
            continue

        for date_str in date_keys:
            team_evs = by_date_team[date_str].get(team, [])
            if not team_evs:
                continue

            date = datetime.strptime(date_str, "%Y-%m-%d")
            weekday = WEEKDAY_KO[date.weekday()]
            st.subheader(f"{date.month}/{date.day} ({weekday})")

            # 중복 제거 + 시간 정렬
            seen = set()
            unique_evs = [e for e in team_evs if not (e["id"] in seen or seen.add(e["id"]))]
            unique_evs.sort(key=lambda x: x["start"])

            # 헤더
            header = st.columns([3.5, 1.3, 1.8, 1.6, 1])
            header[0].caption("**회의 제목**")
            header[1].caption("**시간**")
            header[2].caption("**회의실 현황**")
            header[3].caption("**회의실 선택**")
            header[4].caption("**액션**")

            for idx, ev in enumerate(unique_evs):
                row = st.columns([3.5, 1.3, 1.8, 1.6, 1])

                # 제목
                row[0].write(ev["title"])

                # 시간
                time_txt = f"{ev['start'].strftime('%H:%M')}~{ev['end'].strftime('%H:%M')}"
                row[1].write(time_txt)

                has_original_room = bool(ev["rooms"])
                my_booking = my_bookings.get(ev["id"])

                # 회의실 현황
                if has_original_room:
                    row[2].markdown(f"🏢 `{' / '.join(ev['rooms'])}` _(원본)_")
                elif my_booking:
                    room_name = (
                        my_booking.get("extendedProperties", {})
                        .get("private", {})
                        .get("room_name", "?")
                    )
                    row[2].markdown(f"✅ `{room_name}` _(내 예약)_")
                else:
                    row[2].markdown("🔴 **미배정**")

                # 회의실 선택 + 액션
                key_base = f"{team}_{date_str}_{idx}_{ev['id']}"

                if has_original_room:
                    row[3].write("—")
                    row[4].write("—")
                elif my_booking:
                    row[3].write("—")
                    if row[4].button("취소", key=f"cancel_{key_base}", type="secondary"):
                        with st.spinner("취소 중..."):
                            result = cancel_booking(service, ev["id"], week_offset)
                        if result["status"] == "ok":
                            st.success(result["message"])
                        else:
                            st.error(result["message"])
                        invalidate_cache()
                        st.rerun()
                else:
                    room_choice = row[3].selectbox(
                        "회의실",
                        ["선택..."] + sorted(ROOM_RESOURCES.keys()),
                        key=f"sel_{key_base}",
                        label_visibility="collapsed",
                    )
                    if row[4].button("예약", key=f"book_{key_base}", type="primary"):
                        if room_choice == "선택...":
                            st.warning("회의실을 먼저 선택해주세요.")
                        else:
                            with st.spinner("예약 중..."):
                                result = book_meeting(
                                    service,
                                    ev,
                                    ROOM_RESOURCES[room_choice],
                                    room_choice,
                                )
                            if result["status"] == "ok":
                                st.success(result["message"])
                                invalidate_cache()
                                st.rerun()
                            else:
                                st.error(result["message"])

            st.divider()

# ─────────────────────────────────────────
# 문자 생성 탭
# ─────────────────────────────────────────
with tabs[-1]:
    st.markdown("### 📋 안내 문자")
    st.caption("각 박스의 내용을 복사해서 팀 카톡방에 붙여넣으세요.")

    date_keys = sorted(by_date_team.keys())
    if not date_keys:
        st.info("조회된 일정이 없습니다.")
    else:
        for date_str in date_keys:
            team_events_for_date = by_date_team[date_str]
            sms = build_sms(date_str, team_events_for_date, my_bookings)
            date = datetime.strptime(date_str, "%Y-%m-%d")
            weekday = WEEKDAY_KO[date.weekday()]
            st.text_area(
                f"{date.month}/{date.day} ({weekday})",
                sms,
                height=300,
                key=f"sms_{date_str}",
            )