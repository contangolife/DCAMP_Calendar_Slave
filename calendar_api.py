"""
Google Calendar API 로직 (조회 / 예약 / 취소)

핵심 설계:
- 팀원 회의를 "읽기"만 함 (남의 캘린더 수정 불가)
- 회의실 예약은 "내 캘린더에 미러 이벤트 생성 + 회의실만 초대"
- 미러 이벤트는 extendedProperties.private에 원본 ID를 심어 중복/취소 관리
"""
import json
import os
import re
from datetime import datetime, timedelta
from collections import defaultdict

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build

from config import (
    SCOPES, CREDENTIALS_FILE, TOKEN_FILE, BOOKING_TAG,
    TZ, WEEKDAY_KO, TEAM_MAP, TEAM_ORDER,
)

# ─────────────────────────────────────────
# 인증 (로컬 파일 + Streamlit Secrets 둘 다 지원)
# ─────────────────────────────────────────

def _deep_to_dict(obj):
    """Streamlit Secrets AttrDict를 일반 dict로 재귀 변환"""
    if hasattr(obj, "to_dict"):
        obj = obj.to_dict()
    if isinstance(obj, dict):
        return {k: _deep_to_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_to_dict(x) for x in obj]
    return obj


def _load_auth_data() -> tuple[dict | None, dict | None]:
    """
    credentials / token 정보를 로드.
    우선순위: Streamlit Secrets > 로컬 파일
    Returns: (credentials_info, token_info) or (None, None)
    """
    creds_info, token_info = None, None

    # 1. Streamlit Secrets 시도
    try:
        import streamlit as st
        if "google_credentials" in st.secrets:
            creds_info = _deep_to_dict(st.secrets["google_credentials"])
        if "google_token" in st.secrets:
            token_info = _deep_to_dict(st.secrets["google_token"])
    except Exception:
        pass

    # 2. 로컬 파일 fallback
    if creds_info is None and CREDENTIALS_FILE.exists():
        creds_info = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
    if token_info is None and TOKEN_FILE.exists():
        token_info = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))

    return creds_info, token_info


def _save_token_local(creds):
    """가능하면 로컬에 토큰 저장. Streamlit Cloud에서는 조용히 실패."""
    try:
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    except Exception:
        pass


def authenticate():
    """
    로컬: credentials.json + token.json 파일
    Cloud: Streamlit Secrets의 google_credentials, google_token
    """
    creds_info, token_info = _load_auth_data()
    creds = None

    # 1. 기존 토큰으로 시도
    if token_info:
        try:
            creds = Credentials.from_authorized_user_info(token_info, SCOPES)
        except Exception:
            creds = None

    # 2. 유효하면 바로 반환
    if creds and creds.valid:
        return build("calendar", "v3", credentials=creds)

    # 3. 만료됐지만 refresh 가능하면 갱신
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token_local(creds)
            return build("calendar", "v3", credentials=creds)
        except RefreshError:
            creds = None

    # 4. 새로 인증 필요 (Desktop OAuth flow — 로컬 전용)
    if not creds_info:
        raise RuntimeError(
            "credentials가 없습니다.\n"
            "- 로컬: credentials.json을 프로젝트 폴더에 두세요.\n"
            "- Cloud: Streamlit Secrets에 [google_credentials]를 등록하세요."
        )
    if "installed" not in creds_info:
        raise RuntimeError(
            "토큰이 만료되었거나 없습니다.\n"
            "로컬 PC에서 한 번 authenticate()를 실행해 token.json을 재생성한 후,\n"
            "그 내용을 Streamlit Secrets의 [google_token] 섹션에 업데이트하세요."
        )

    flow = InstalledAppFlow.from_client_config(creds_info, SCOPES)
    creds = flow.run_local_server(port=0)
    _save_token_local(creds)
    return build("calendar", "v3", credentials=creds)


# ─────────────────────────────────────────
# Per-user 웹 OAuth (Streamlit Cloud용)
# ─────────────────────────────────────────

def _get_oauth_client_config() -> dict:
    """Streamlit Secrets [oauth] 섹션에서 웹 OAuth 클라이언트 설정 로드"""
    import streamlit as st
    return {
        "web": {
            "client_id":     st.secrets["oauth"]["client_id"],
            "client_secret": st.secrets["oauth"]["client_secret"],
            "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
            "token_uri":     "https://oauth2.googleapis.com/token",
        }
    }


def has_oauth_config() -> bool:
    """Streamlit Secrets에 [oauth] 설정이 있는지 확인"""
    try:
        import streamlit as st
        return "oauth" in st.secrets and "client_id" in st.secrets["oauth"]
    except Exception:
        return False


def get_auth_url(redirect_uri: str) -> str:
    """Google OAuth 인증 URL 생성 (PKCE 없이 — 리디렉트 후 session 유실 문제 회피)"""
    import urllib.parse
    config = _get_oauth_client_config()["web"]
    params = {
        "client_id": config["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{config['auth_uri']}?{urllib.parse.urlencode(params)}"


def exchange_code_for_credentials(code: str, redirect_uri: str) -> Credentials:
    """OAuth 인가 코드를 Credentials로 교환 (PKCE 없이 직접 POST)"""
    import requests as _requests
    config = _get_oauth_client_config()["web"]
    resp = _requests.post(config["token_uri"], data={
        "code": code,
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })
    token_data = resp.json()
    if "error" in token_data:
        raise RuntimeError(token_data.get("error_description", token_data["error"]))
    return Credentials(
        token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token"),
        token_uri=config["token_uri"],
        client_id=config["client_id"],
        client_secret=config["client_secret"],
        scopes=SCOPES,
    )


def build_service_from_creds_json(creds_json: str):
    """
    저장된 JSON 문자열로부터 Calendar 서비스 빌드.
    만료 시 자동 갱신.
    Returns: (service, updated_creds_json)
    """
    creds = Credentials.from_authorized_user_info(json.loads(creds_json), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("calendar", "v3", credentials=creds), creds.to_json()


def get_user_email(service) -> str:
    """Calendar API로 로그인된 사용자의 이메일 조회 (추가 스코프 불필요)"""
    try:
        cal = service.calendars().get(calendarId="primary").execute()
        return cal.get("id", "")
    except Exception:
        return ""


# ─────────────────────────────────────────
# 시간 유틸
# ─────────────────────────────────────────

def get_week_range(week_offset: int = 0) -> tuple[datetime, datetime]:
    """week_offset: 0=이번주, 1=다음주, 2=다다음주. 평일(월~금) 범위만 반환."""
    today = datetime.now(TZ)
    monday = (today - timedelta(days=today.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    monday = monday + timedelta(weeks=week_offset)
    friday_end = monday + timedelta(days=4, hours=23, minutes=59, seconds=59)
    return monday, friday_end


def parse_event_time(event: dict) -> tuple[datetime | None, datetime | None]:
    s = event.get("start", {}).get("dateTime")
    e = event.get("end", {}).get("dateTime")
    if not s or not e:
        return None, None
    return (
        datetime.fromisoformat(s).astimezone(TZ),
        datetime.fromisoformat(e).astimezone(TZ),
    )


# ─────────────────────────────────────────
# 회의실 이름 파싱
# ─────────────────────────────────────────

def clean_room_name(raw: str) -> str:
    """'Taap-선릉-4-4-B (6)' → '선릉 4-B'"""
    m = re.search(r"Taap[-_]?(선릉|마포)[-_](\d+)[-_]\d+[-_]([A-Za-z])", raw, re.IGNORECASE)
    if m:
        return f"{m.group(1)} {m.group(2)}-{m.group(3).upper()}"
    m2 = re.search(r"(선릉|마포)\s*(\d+[-][A-Za-z])", raw)
    if m2:
        return f"{m2.group(1)} {m2.group(2)}"
    return raw.strip()


def extract_attendee_names(event: dict, email_to_name: dict | None = None) -> list[str]:
    """참석자 이름 목록 (회의실 리소스 제외, email_to_name → displayName → 이메일 local-part)"""
    names: list[str] = []
    seen: set[str] = set()
    for att in event.get("attendees", []):
        email = att.get("email", "")
        if not email or "@resource.calendar.google.com" in email:
            continue
        if email_to_name and email in email_to_name:
            name = email_to_name[email]
        else:
            name = att.get("displayName") or email.split("@")[0]
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def get_booked_rooms(event: dict, room_resources: dict) -> list[str]:
    """
    이벤트에 실제로 예약된 회의실 이름 목록.
    - 회의실 리소스 attendee만 신뢰 (location 텍스트는 오타/장식용이라 무시)
    - declined된 회의실은 제외 (Google이 busy라서 자동 거절한 경우)
    """
    rooms = []
    for att in event.get("attendees", []):
        email = att.get("email", "")
        if "@resource.calendar.google.com" not in email:
            continue
        if att.get("responseStatus") == "declined":
            continue
        matched = next((name for name, rid in room_resources.items() if rid == email), None)
        if matched:
            rooms.append(matched)
        else:
            display = att.get("displayName", "")
            if display:
                rooms.append(clean_room_name(display))
    return rooms


# ─────────────────────────────────────────
# 조회
# ─────────────────────────────────────────

def fetch_team_events(service, week_offset: int = 0) -> tuple[list, list[str]]:
    """팀원 전체의 이번 주 이벤트 조회 (중복 제거)"""
    time_min, time_max = get_week_range(week_offset)
    event_map: dict[str, dict] = {}
    errors: list[str] = []

    for email in TEAM_MAP.keys():
        try:
            page_token = None
            while True:
                result = service.events().list(
                    calendarId=email,
                    timeMin=time_min.isoformat(),
                    timeMax=time_max.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=250,
                    pageToken=page_token,
                ).execute()
                for ev in result.get("items", []):
                    # 대시보드가 만든 미러 이벤트는 제외 (본인 로그인 시 자기 primary 캘린더에서 노출 방지)
                    if (
                        ev.get("extendedProperties", {})
                        .get("private", {})
                        .get("booked_by_script")
                        == BOOKING_TAG
                    ):
                        continue
                    eid = ev.get("id", "")
                    if eid not in event_map:
                        event_map[eid] = ev
                        event_map[eid]["_team_emails"] = set()
                    event_map[eid]["_team_emails"].add(email)
                page_token = result.get("nextPageToken")
                if not page_token:
                    break
        except Exception as e:
            errors.append(f"{email}: {e}")

    return list(event_map.values()), errors


def _iter_event_dates(event: dict):
    """이벤트가 커버하는 날짜 yield — 시간 있는 이벤트는 시작일, 종일 이벤트는 모든 해당일"""
    start = event.get("start", {}) or {}
    end = event.get("end", {}) or {}

    if "dateTime" in start:
        s = datetime.fromisoformat(start["dateTime"]).astimezone(TZ)
        yield s.strftime("%Y-%m-%d")
        return

    if "date" in start:
        s_date = datetime.strptime(start["date"], "%Y-%m-%d")
        e_str = end.get("date", start["date"])
        e_date = datetime.strptime(e_str, "%Y-%m-%d")
        if e_date <= s_date:
            yield s_date.strftime("%Y-%m-%d")
            return
        d = s_date
        while d < e_date:  # Google 종일 이벤트의 end.date는 exclusive
            yield d.strftime("%Y-%m-%d")
            d += timedelta(days=1)


def classify_events_per_person(events: list) -> dict:
    """
    팀원별 날짜별 이벤트 목록. 9-18 업무시간/점심 필터 없음 — 휴가/근무지/종일 이벤트 포함.
    Returns: {date_str: {email: [events]}}
    """
    by_date_person: dict = defaultdict(lambda: defaultdict(list))
    for ev in events:
        team_emails = ev.get("_team_emails", set())
        if not team_emails:
            continue
        for date_str in _iter_event_dates(ev):
            try:
                d = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                continue
            if d.weekday() >= 5:
                continue
            for email in team_emails:
                if email in TEAM_MAP:
                    by_date_person[date_str][email].append(ev)
    return {d: dict(v) for d, v in by_date_person.items()}


_HANGUL_RE = re.compile(r'[\uac00-\ud7a3]')


def _pick_korean_name(name_entries: list) -> str:
    """여러 name 항목 중 한국어 이름 우선, 없으면 첫 번째"""
    korean = ""
    fallback = ""
    for n in name_entries:
        dn = n.get("displayName", "")
        if not dn:
            continue
        if not fallback:
            fallback = dn
        if _HANGUL_RE.search(dn):
            korean = dn
            break
    return korean or fallback


def _fetch_contact_names(cal_service) -> dict[str, str]:
    """Google Contacts에서 팀원 이름 조회 (한국어 우선)."""
    names: dict[str, str] = {}
    try:
        people_svc = build("people", "v1", http=cal_service._http)
    except Exception:
        return names

    try:
        page_token = None
        while True:
            result = people_svc.people().connections().list(
                resourceName="people/me",
                personFields="names,emailAddresses",
                pageSize=1000,
                pageToken=page_token,
            ).execute()
            for person in result.get("connections", []):
                _match_contact(person, names)
            page_token = result.get("nextPageToken")
            if not page_token:
                break
    except Exception:
        pass
    return names


def _match_contact(person: dict, names: dict) -> None:
    """연락처 한 건에서 이메일 → 한국어 이름 저장 (팀원 외 참석자 포함)"""
    emails = [
        e.get("value", "").lower()
        for e in person.get("emailAddresses", [])
    ]
    display = _pick_korean_name(person.get("names", []))
    if not display:
        return
    for em in emails:
        existing = names.get(em, "")
        if not existing or (not _HANGUL_RE.search(existing) and _HANGUL_RE.search(display)):
            names[em] = display


def build_email_to_name(service=None) -> dict[str, str]:
    """
    팀원 이메일 → 한국어 이름.
    1순위: 내 연락처 (Contacts API)
    2순위: 이메일 앞부분 (fallback)
    """
    email_to_name: dict[str, str] = {}

    if service:
        email_to_name.update(_fetch_contact_names(service))

    for email in TEAM_MAP:
        email_to_name.setdefault(email, email.split("@")[0])
    return email_to_name


def classify_events(events: list, room_resources: dict, email_to_name: dict | None = None) -> dict:
    """이벤트를 {날짜: {팀: [entries]}} 구조로 변환"""
    by_date_team: dict = defaultdict(lambda: defaultdict(list))

    for ev in events:
        start, end = parse_event_time(ev)
        if not start:
            continue
        if start.weekday() >= 5:
            continue

        # 회의실 예약 가능 시간(09:00~18:00) 밖 일정 제외
        biz_start = start.replace(hour=9, minute=0, second=0, microsecond=0)
        biz_end = start.replace(hour=18, minute=0, second=0, microsecond=0)
        if start < biz_start or end > biz_end:
            continue

        # 점심시간(12:00~13:00) 포함 일정 제외
        lunch_start = start.replace(hour=12, minute=0, second=0, microsecond=0)
        lunch_end = start.replace(hour=13, minute=0, second=0, microsecond=0)
        if start < lunch_end and end > lunch_start:
            continue

        teams = {TEAM_MAP[em] for em in ev.get("_team_emails", set()) if em in TEAM_MAP}
        if not teams:
            continue

        date_key = start.strftime("%Y-%m-%d")
        organizer = ev.get("organizer", {}) or {}
        organizer_email = organizer.get("email", "")
        if email_to_name and organizer_email in email_to_name:
            organizer_name = email_to_name[organizer_email]
        else:
            organizer_name = organizer.get("displayName") or (
                organizer_email.split("@")[0] if organizer_email else ""
            )

        raw_desc = ev.get("description", "") or ""
        clean_desc = re.sub(r"<[^>]+>", "", raw_desc).strip()

        entry = {
            "id":    ev.get("id", ""),
            "title": ev.get("summary", "(제목 없음)").strip(),
            "start": start,
            "end":   end,
            "rooms": get_booked_rooms(ev, room_resources),
            "organizer": organizer_email,
            "organizer_name": organizer_name,
            "teams": sorted(teams, key=lambda t: TEAM_ORDER.index(t) if t in TEAM_ORDER else 99),
            "attendees": extract_attendee_names(ev, email_to_name),
            "location": (ev.get("location") or "").strip(),
            "description": clean_desc,
        }
        for team in teams:
            by_date_team[date_key][team].append(entry)

    return by_date_team


# ─────────────────────────────────────────
# 내 미러 예약 조회/관리
# ─────────────────────────────────────────

def fetch_my_bookings(service, week_offset: int = 0) -> dict[str, list[dict]]:
    """
    내 캘린더에서 이 스크립트가 만든 예약 이벤트 전체 조회.
    같은 원본 일정에 여러 회의실(마포/선릉 동시 등)을 잡으면 미러가 여러 개 생기므로
    원본 id 하나에 미러 리스트로 반환.
    Returns: {원본_이벤트_id: [미러_이벤트, ...]}
    """
    time_min, time_max = get_week_range(week_offset)
    bookings: dict[str, list[dict]] = defaultdict(list)
    page_token = None
    while True:
        result = service.events().list(
            calendarId="primary",
            timeMin=time_min.isoformat(),
            timeMax=time_max.isoformat(),
            singleEvents=True,
            privateExtendedProperty=f"booked_by_script={BOOKING_TAG}",
            pageToken=page_token,
        ).execute()
        for ev in result.get("items", []):
            props = ev.get("extendedProperties", {}).get("private", {})
            original_id = props.get("original_event_id")
            if not original_id:
                continue
            # 회의실이 거절한 미러는 실제로 예약되지 않은 상태 (Taap/리소스가 예약 실패로 응답)
            # declined는 스킵, 그 외(accepted/needsAction/tentative)는 유효한 예약으로 포함
            room_declined = any(
                "@resource.calendar.google.com" in att.get("email", "")
                and att.get("responseStatus") == "declined"
                for att in ev.get("attendees", [])
            )
            if room_declined:
                continue
            bookings[original_id].append(ev)
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return dict(bookings)


def is_room_available(service, room_cal_id: str, start: datetime, end: datetime) -> bool:
    """회의실 캘린더를 직접 읽어 시간 겹침 확인"""
    try:
        result = service.events().list(
            calendarId=room_cal_id,
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
        ).execute()
        for ev in result.get("items", []):
            s = ev.get("start", {}).get("dateTime")
            e = ev.get("end", {}).get("dateTime")
            if not s or not e:
                continue
            ev_start = datetime.fromisoformat(s).astimezone(TZ)
            ev_end = datetime.fromisoformat(e).astimezone(TZ)
            if ev_start < end and ev_end > start:
                return False
        return True
    except Exception:
        # 권한 없으면 확인 스킵 (insert 시점에 Google이 거부함)
        return True


def fetch_room_busy_times(
    service,
    week_offset: int = 0,
    room_resources: dict[str, str] | None = None,
) -> dict[str, list[tuple[datetime, datetime]]]:
    """
    freebusy API로 모든 회의실의 예약된 시간대를 한 번에 조회.
    Returns: {회의실_이름: [(busy_start, busy_end), ...]}
    권한 없거나 에러 시 해당 회의실은 빈 리스트 → 예약 가능으로 취급 (insert 시점에 Google이 거부함)
    """
    from config import ROOM_RESOURCES
    if room_resources is None:
        room_resources = ROOM_RESOURCES

    time_min, time_max = get_week_range(week_offset)
    email_to_name = {email: name for name, email in room_resources.items()}
    busy_map: dict[str, list[tuple[datetime, datetime]]] = {
        name: [] for name in room_resources.keys()
    }

    try:
        result = service.freebusy().query(body={
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
            "items": [{"id": email} for email in room_resources.values()],
        }).execute()
    except Exception:
        return busy_map

    for email, info in (result.get("calendars") or {}).items():
        name = email_to_name.get(email)
        if not name:
            continue
        spans: list[tuple[datetime, datetime]] = []
        for b in info.get("busy", []):
            s, e = b.get("start"), b.get("end")
            if not s or not e:
                continue
            try:
                spans.append((
                    datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(TZ),
                    datetime.fromisoformat(e.replace("Z", "+00:00")).astimezone(TZ),
                ))
            except Exception:
                continue
        busy_map[name] = spans
    return busy_map


def available_rooms_at(
    busy_map: dict[str, list[tuple[datetime, datetime]]],
    start: datetime,
    end: datetime,
) -> list[str]:
    """주어진 시간대 [start, end)에 비어있는 회의실 이름 리스트 (정렬)"""
    result = []
    for name, spans in busy_map.items():
        if not any(bs < end and be > start for bs, be in spans):
            result.append(name)
    return sorted(result)


# ─────────────────────────────────────────
# 예약 / 취소
# ─────────────────────────────────────────

def book_meeting(
    service,
    entry: dict,
    room_resource_email: str,
    room_name: str,
) -> dict:
    """
    내 캘린더에 미러 이벤트 생성 + 회의실만 초대
    Returns: {"status": "ok"|"duplicate"|"conflict"|"error", "event_id": ..., "message": ...}
    """
    # 1. 중복 및 배치 내 충돌 체크 — BOOKING_CALENDAR 조회 (읽기-쓰기 일관성 ↑)
    existing = fetch_my_bookings(service, _week_offset_for_date(entry["start"]))

    # 1a. 같은 원본 이벤트에 같은 방 — 중복
    for mirror in existing.get(entry["id"], []):
        m_room = (
            mirror.get("extendedProperties", {})
            .get("private", {})
            .get("room_name")
        )
        if m_room == room_name:
            return {
                "status": "duplicate",
                "event_id": mirror["id"],
                "message": f"{room_name}은(는) 이미 예약되어 있습니다.",
            }

    # 1b. 다른 원본이라도 같은 방 + 시간 겹침 — 충돌
    # (회의실 캘린더 반영 전에 같은 배치에서 연달아 예약한 경우를 잡기 위함)
    for mirrors in existing.values():
        for mirror in mirrors:
            if (
                mirror.get("extendedProperties", {})
                .get("private", {})
                .get("room_name")
                != room_name
            ):
                continue
            s_raw = mirror.get("start", {}).get("dateTime")
            e_raw = mirror.get("end", {}).get("dateTime")
            if not s_raw or not e_raw:
                continue
            m_start = datetime.fromisoformat(s_raw).astimezone(TZ)
            m_end = datetime.fromisoformat(e_raw).astimezone(TZ)
            if m_start < entry["end"] and m_end > entry["start"]:
                return {
                    "status": "conflict",
                    "event_id": None,
                    "message": (
                        f"{room_name}이(가) "
                        f"{m_start.strftime('%H:%M')}~{m_end.strftime('%H:%M')}에 "
                        f"이미 대시보드 예약되어 있습니다."
                    ),
                }

    # 2. 회의실 캘린더 가용성 체크 (다른 사용자/외부 예약 대비)
    if not is_room_available(service, room_resource_email, entry["start"], entry["end"]):
        return {
            "status": "conflict",
            "event_id": None,
            "message": f"{room_name}이(가) 해당 시간에 이미 예약되어 있습니다.",
        }

    # 3. 미러 이벤트 생성
    body = {
        "summary": f"[예약] {entry['title']}",
        "start":   {"dateTime": entry["start"].isoformat(), "timeZone": "Asia/Seoul"},
        "end":     {"dateTime": entry["end"].isoformat(),   "timeZone": "Asia/Seoul"},
        "attendees": [{"email": room_resource_email, "displayName": room_name}],
        "reminders": {"useDefault": False},
        "description": f"원본 이벤트 대리 예약\n원본 ID: {entry['id']}\n원본 주최자: {entry.get('organizer', '')}",
        "extendedProperties": {
            "private": {
                "original_event_id":  entry["id"],
                "original_title":     entry["title"],
                "room_name":          room_name,
                "booked_by_script":   BOOKING_TAG,
            }
        },
    }
    try:
        created = service.events().insert(
            calendarId="primary",
            body=body,
            sendUpdates="none",
        ).execute()
        return {
            "status": "ok",
            "event_id": created["id"],
            "event": created,
            "original_id": entry["id"],
            "message": f"{room_name} 예약 완료",
        }
    except Exception as e:
        return {
            "status": "error",
            "event_id": None,
            "message": f"예약 실패: {e}",
        }


def cancel_booking(
    service,
    original_event_id: str,
    week_offset: int = 0,
    mirror_event_ids: set[str] | None = None,
) -> dict:
    """
    원본 id에 연결된 미러 예약 이벤트 삭제.
    mirror_event_ids 지정 시 해당 미러만 부분 취소, None이면 전체 취소.
    """
    bookings = fetch_my_bookings(service, week_offset)
    mirrors = bookings.get(original_event_id, [])
    if mirror_event_ids is not None:
        mirrors = [m for m in mirrors if m["id"] in mirror_event_ids]
    if not mirrors:
        return {"status": "not_found", "message": "해당 예약을 찾을 수 없습니다."}

    succeeded = 0
    errors: list[str] = []
    deleted_mirror_ids: list[str] = []
    for mirror in mirrors:
        room = (
            mirror.get("extendedProperties", {})
            .get("private", {})
            .get("room_name", "?")
        )
        try:
            service.events().delete(
                calendarId="primary",
                eventId=mirror["id"],
                sendUpdates="none",
            ).execute()
            succeeded += 1
            deleted_mirror_ids.append(mirror["id"])
        except Exception as e:
            errors.append(f"{room}: {e}")

    if errors and succeeded == 0:
        return {
            "status": "error",
            "message": f"취소 실패: {'; '.join(errors)}",
            "deleted_mirror_ids": deleted_mirror_ids,
        }
    if errors:
        return {
            "status": "partial",
            "message": f"{succeeded}건 취소, 실패: {'; '.join(errors)}",
            "deleted_mirror_ids": deleted_mirror_ids,
        }
    return {
        "status": "ok",
        "message": f"{succeeded}건 취소 완료",
        "deleted_mirror_ids": deleted_mirror_ids,
    }


def _week_offset_for_date(dt: datetime) -> int:
    """datetime이 어느 주의 offset에 해당하는지 계산"""
    today = datetime.now(TZ)
    this_monday = (today - timedelta(days=today.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    target_monday = (dt - timedelta(days=dt.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return (target_monday - this_monday).days // 7


# ─────────────────────────────────────────
# SMS 생성
# ─────────────────────────────────────────

def build_sms(date_str: str, team_events: dict, my_bookings: dict) -> str:
    """
    team_events: {팀: [entries]}
    my_bookings: {원본_id: [미러_이벤트, ...]}

    내가 이 대시보드로 직접 예약한 회의만 포함 (남이 잡은 방은 제외).
    팀 이름은 일정 유무와 관계없이 항상 표시.
    """
    date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TZ)
    weekday = WEEKDAY_KO[date.weekday()]

    lines = [f"💡 {date.month}/{date.day} {weekday}요일, 회의실 안내드립니다!", ""]

    for team in TEAM_ORDER:
        lines.append(f"□ {team}")
        lines.append("")

        evs = team_events.get(team)
        if evs:
            seen: set = set()
            unique_evs = [e for e in evs if not (e["id"] in seen or seen.add(e["id"]))]
            unique_evs.sort(key=lambda x: x["start"])

            for ev in unique_evs:
                my_mirrors = my_bookings.get(ev["id"], [])
                if not my_mirrors:
                    continue
                rooms: list[str] = []
                for mirror in my_mirrors:
                    room_name = (
                        mirror.get("extendedProperties", {})
                        .get("private", {})
                        .get("room_name")
                    )
                    if room_name and room_name not in rooms:
                        rooms.append(room_name)
                if not rooms:
                    continue
                room_str = " / ".join(rooms)
                t = f"{ev['start'].strftime('%H:%M')}~{ev['end'].strftime('%H:%M')}"
                lines.append(f"  [{room_str}] {t} {ev['title']}")
                lines.append("")

        lines.append("")

    lines.append("추가 일정이나 변경 사항 있으시면 반영하겠습니다. 감사합니다.")
    return "\n".join(lines)