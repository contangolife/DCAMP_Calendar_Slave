"""
회의실 예약 대시보드 설정값
"""
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).parent

# ─────────────────────────────────────────
# Google OAuth
# ─────────────────────────────────────────
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/contacts.readonly",
]
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"

# 스크립트가 생성한 "미러 예약 이벤트"임을 구분하는 태그
# 이 태그를 통해 내 캘린더의 일반 이벤트와 구분함
BOOKING_TAG = "dashboard_v1"

# 모든 운영자가 공유하는 "미러 예약 전용" 캘린더.
# 개인 primary 대신 여기에 이벤트를 생성/조회해서 운영자가 바뀌어도 상태가 유지됨.
BOOKING_CALENDAR_ID = (
    "c_966f99944c3407ebbad59fcc91c00f9f0e002a3715b4e0049923e8c313392c2f"
    "@group.calendar.google.com"
)

# ─────────────────────────────────────────
# 시간/요일
# ─────────────────────────────────────────
TZ = ZoneInfo("Asia/Seoul")
WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]

# ─────────────────────────────────────────
# 팀 분류
# ─────────────────────────────────────────
TEAM_ORDER = ["투자전략팀", "펀드팀", "투자팀"]

TEAM_MAP: dict[str, str] = {
    # 투자전략팀
    "seokjoo@dcamp.kr":     "투자전략팀",
    "hanui@dcamp.kr":       "투자전략팀",
    "hyohyun@dcamp.kr":     "투자전략팀",
    "juheum@dcamp.kr":      "투자전략팀",
    # 펀드팀
    "myeongcheol@dcamp.kr": "펀드팀",
    "jiyoungheo@dcamp.kr":  "펀드팀",
    "sanghyeok@dcamp.kr":   "펀드팀",
    "ykpark@dcamp.kr":      "펀드팀",
    "seoha@dcamp.kr":       "펀드팀",
    "jiny@dcamp.kr":        "펀드팀",
    # 투자팀
    "youngsang@dcamp.kr":   "투자팀",
    "jinha@dcamp.kr":       "투자팀",
    "hansol.kim@dcamp.kr":  "투자팀",
    "nakhwan@dcamp.kr":     "투자팀",
    "eunjeong@dcamp.kr":    "투자팀",
    "inho@dcamp.kr":        "투자팀",
    "gayoung@dcamp.kr":     "투자팀",
}

# ─────────────────────────────────────────
# 회의실 리소스
# (Cell 2 로그에서 확인된 실제 리소스 ID들)
# ─────────────────────────────────────────
ROOM_RESOURCES: dict[str, str] = {
    # ── 마포 15층 ──
    "마포 15-E": "c_18888qdfu0smigdvl02mt8u8mbccs@resource.calendar.google.com",
    # ── 마포 17층 ──
    "마포 17-A": "c_188agei1qgc6ig03mm09esun59204@resource.calendar.google.com",
    "마포 17-B": "c_188699m48baicidkk4acodnp3ou4c@resource.calendar.google.com",
    "마포 17-C": "c_188c2ai9i7pqoiupi3t3077hi9q3c@resource.calendar.google.com",
    "마포 17-D": "c_188aiste755g4g9kl7qlhqt9dh8to@resource.calendar.google.com",
    "마포 17-E": "c_1882cghiksqligtklqjilt7mcgjjc@resource.calendar.google.com",
    # ── 마포 18층 ──
    "마포 18-A": "c_1882is5r6jjlkicektsrete5on4uk@resource.calendar.google.com",
    "마포 18-B": "c_1888rnc3fr0qkg26hr13vj39th48e@resource.calendar.google.com",
    "마포 18-C": "c_188bqsjc2q430j1ql05ue2qbo972k@resource.calendar.google.com",
    "마포 18-D": "c_188d5kpikb39kjs4h101uptmarnse@resource.calendar.google.com",
    # ── 선릉 2층 ──
    "선릉 2-A": "c_188cephv2n692jtji6hv2svan5e60@resource.calendar.google.com",
    "선릉 2-B": "c_188fjm7paiu72ihgmbq91mj5mgak6@resource.calendar.google.com",
    "선릉 2-C": "c_1889ts1fthf2sg42n5q24sivmue16@resource.calendar.google.com",
    # ── 선릉 4층 ──
    "선릉 4-A": "c_188e6gbi8stp0g91giiao5sddd1s8@resource.calendar.google.com",
    "선릉 4-B": "c_18825va2grf3oi1rlbnjbilk95ju6@resource.calendar.google.com",
    "선릉 4-C": "c_1885df2a2fojiiqamt7q85ctj2ib4@resource.calendar.google.com",
    "선릉 4-D": "c_1887b36b9c2fkhhkknqcprperuc1m@resource.calendar.google.com",
}