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
# 메인 테이블 (전체 팀 통합 뷰)
# ─────────────────────────────────────────
tabs = st.tabs(["📅 일정 전체", "📋 문자 생성"])

COL_WIDTHS = [1.4, 1.3, 2.8, 1.2, 2.0, 1.8, 1.6, 1.0]

with tabs[0]:
    date_keys = sorted(by_date_team.keys())
    if not date_keys:
        st.info("조회된 일정이 없습니다.")
    else:
        for date_str in date_keys:
            # 팀 간 중복 제거: 같은 이벤트가 여러 팀 리스트에 들어있으면 한 번만 표시
            all_evs: list = []
            seen: set = set()
            for team in TEAM_ORDER:
                for ev in by_date_team[date_str].get(team, []):
                    if ev["id"] in seen:
                        continue
                    seen.add(ev["id"])
                    all_evs.append(ev)

            if not all_evs:
                continue

            all_evs.sort(key=lambda x: x["start"])

            date = datetime.strptime(date_str, "%Y-%m-%d")
            weekday = WEEKDAY_KO[date.weekday()]

            # 미배정 이벤트와 해당 multiselect 키 준비
            unassigned: list[tuple[int, dict, str]] = []
            for idx, ev in enumerate(all_evs):
                if ev["rooms"]:
                    continue
                if ev["id"] in my_bookings:
                    continue
                sel_key = f"sel_all_{date_str}_{idx}_{ev['id']}"
                unassigned.append((idx, ev, sel_key))

            # 현재 multiselect에서 실제 선택된 회의실 총 개수
            selected_rooms_count = sum(
                len(st.session_state.get(k, [])) for _, _, k in unassigned
            )
            selected_evs_count = sum(
                1 for _, _, k in unassigned
                if len(st.session_state.get(k, [])) > 0
            )

            # 요일 헤더 + 일괄 예약 버튼
            head_cols = st.columns([5, 2])
            head_cols[0].subheader(f"{date.month}/{date.day} ({weekday})")

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

            st.divider()

# ─────────────────────────────────────────
# 문자 생성 탭
# ─────────────────────────────────────────
with tabs[-1]:
    st.markdown("### 📋 안내 문자")
    st.caption("각 박스의 내용을 복사해서 팀 카톡방에 붙여넣으세요.")

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