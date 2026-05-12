"""구글시트 양방향 동기화.

동기화 정책 (사용자 결정):
    aliases       — 양방향, last-write-wins (modified_at 기준)
    subscriptions — 앱 → 시트 단방향 (시트는 매번 덮어씀)
    transactions  — 앱 → 시트 단방향 (시트는 매번 덮어씀)

OAuth: 사용자 본인 GCP 프로젝트에서 OAuth Desktop 클라이언트 생성 →
credentials.json 다운로드 → data/google_credentials.json 에 저장.
첫 sync 시 브라우저가 열려 본인 구글 계정으로 인증 → token 은
data/google_token.json 에 자동 캐시 (이후 자동 갱신).

워크시트 이름:
    "alias_매핑"      A:입금자명 / B:회원ID / C:회원이름 / D:수정시각(ISO)
    "구독_매트릭스"   회원×월 매트릭스 (push 전용)
    "거래내역"        모든 토스 입금 거래 (push 전용)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, TYPE_CHECKING

from config import DATA_DIR, SUBSCRIPTION_PRICING
from core.models import FormApplicant

if TYPE_CHECKING:
    from core.payment_store import PaymentStore, Subscription, Transaction
    from core.models import Member


# ---------- 파일 경로 ----------

GOOGLE_CREDENTIALS_FILE = Path(DATA_DIR) / "google_credentials.json"
GOOGLE_TOKEN_FILE = Path(DATA_DIR) / "google_token.json"
SHEETS_CONFIG_FILE = Path(DATA_DIR) / "sheets_config.json"

OAUTH_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# 워크시트 이름 (시트 안의 탭 이름).
# 운영진의 기존 시트와 탭 이름이 겹치지 않도록 "초록앱_" 접두사 — 동기화는
# 이 세 탭만 만들고 쓰며, 시트의 다른 탭(원래 운영진이 쓰던 자료)은 일절 건드리지 않음.
SHEET_ALIASES = "초록앱_alias_매핑"
SHEET_SUBSCRIPTIONS = "초록앱_구독_매트릭스"
SHEET_TRANSACTIONS = "초록앱_거래내역"

# 자료실 신청 구글 폼 응답 탭 — 운영진이 폼으로 받아온다. 환경마다 탭 이름이
# 달라 후보를 순서대로 시도. 컬럼 순서 (A~Q, 17개):
#   A:타임스탬프 B:이름 C:전화번호 D:이메일 E:요금제 F:희망아이디 G:비밀번호 H:비밀번호확인
#   I:동의여부 J:시작일 K:만료일 L:결제안내발송 M:환영메일발송 N:만료알림발송 O:비활성화처리 P:상태 Q:메모
FORM_RESPONSE_SHEET_CANDIDATES = (
    "설문지 응답 시트1",
    "설문지 응답 시트 1",
    "설문지응답시트1",
    "Form Responses 1",
    "Form responses 1",
    "양식 응답 1",
)
FORM_RESPONSE_HEADERS = [
    "타임스탬프", "이름", "전화번호", "이메일",
    "요금제", "희망아이디", "비밀번호", "비밀번호확인", "동의여부",
    "시작일", "만료일", "결제안내발송", "환영메일발송",
    "만료알림발송", "비활성화처리", "상태", "메모",
]
# 주요 컬럼 인덱스 (0-기반).
FORM_COL_USERID = 5        # F  희망아이디 (매칭 키)
FORM_COL_PERIOD_FROM = 9   # J  시작일
FORM_COL_PERIOD_TO = 10    # K  만료일
FORM_COL_STATUS = 15       # P  상태


def _col_letter(n: int) -> str:
    """1-기반 컬럼 번호를 A1 표기 문자로 (1→A, 26→Z, 27→AA)."""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


FORM_LAST_COL = _col_letter(len(FORM_RESPONSE_HEADERS))           # "Q"
FORM_STATUS_COL = _col_letter(FORM_COL_STATUS + 1)               # "P"
FORM_PERIOD_FROM_COL = _col_letter(FORM_COL_PERIOD_FROM + 1)     # "J"
FORM_PERIOD_TO_COL = _col_letter(FORM_COL_PERIOD_TO + 1)         # "K"

# 회원관리 앱이 직접 생성한 행의 '메모' 값 — 구글 폼 제출과 구분용.
NEW_USER_MEMO = "회원관리 앱에 의해 생성"

# 동의여부 값이 이 중 하나(대소문자/공백 무시)면 동의함으로 간주.
_AGREED_TRUTHY = {"동의함", "동의", "예", "yes", "true", "y", "o", "ok"}


# ---------- 설정 (시트 ID + 마지막 sync 시각) ----------

@dataclass
class SheetsConfig:
    spreadsheet_id: str = ""
    last_sync_at: str = ""

    @classmethod
    def load(cls, path: Path = SHEETS_CONFIG_FILE) -> SheetsConfig:
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                spreadsheet_id=str(data.get("spreadsheet_id", "")),
                last_sync_at=str(data.get("last_sync_at", "")),
            )
        except (OSError, json.JSONDecodeError):
            return cls()

    def save(self, path: Path = SHEETS_CONFIG_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"spreadsheet_id": self.spreadsheet_id, "last_sync_at": self.last_sync_at},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


# ---------- 충돌 해결 (순수 함수, 테스트 용이) ----------

@dataclass(frozen=True)
class AliasEntry:
    """양쪽(SQLite/시트) 공통 표현."""
    payer_name: str
    member_user_id: str
    modified_at: datetime


@dataclass(frozen=True)
class MergeResult:
    """alias 병합 결과 — 어느 쪽에 무엇을 써야 하는지."""
    merged: list[AliasEntry]                # 최종 권위 있는 매핑 전체
    to_write_to_sqlite: list[AliasEntry]    # SQLite 에 새로 set_alias 해야 할 것
    to_write_to_sheet: list[AliasEntry]     # 시트에 새로 쓸 것 (전체 덮어쓰기)


def merge_aliases(
    sqlite_aliases: Iterable[AliasEntry],
    sheet_aliases: Iterable[AliasEntry],
) -> MergeResult:
    """양쪽 alias 를 last-write-wins 로 병합.

    충돌(같은 payer_name 양쪽 다 존재)은 modified_at 더 늦은 쪽 채택.
    한쪽에만 있으면 그쪽 값 그대로.
    """
    sql_map = {a.payer_name: a for a in sqlite_aliases}
    sheet_map = {a.payer_name: a for a in sheet_aliases}
    all_keys = set(sql_map) | set(sheet_map)

    merged: list[AliasEntry] = []
    to_sqlite: list[AliasEntry] = []
    for key in sorted(all_keys):
        s = sql_map.get(key)
        g = sheet_map.get(key)
        if s is None and g is not None:
            merged.append(g)
            to_sqlite.append(g)
        elif g is None and s is not None:
            merged.append(s)
        else:
            # 둘 다 존재 — 더 늦은 modified_at 이김
            assert s is not None and g is not None
            if g.modified_at > s.modified_at:
                merged.append(g)
                # SQLite 갱신 필요 — user_id 가 다르거나 시트가 새 값
                if g.member_user_id != s.member_user_id or g.modified_at != s.modified_at:
                    to_sqlite.append(g)
            else:
                merged.append(s)

    # 시트는 매번 전체 덮어쓰기로 단순화 (작은 데이터 + 충돌 회피)
    to_sheet = list(merged)
    return MergeResult(merged=merged, to_write_to_sqlite=to_sqlite, to_write_to_sheet=to_sheet)


# ---------- 시트 ID 추출 (URL 또는 ID 둘 다 허용) ----------

def normalize_spreadsheet_id(value: str) -> str:
    """사용자 입력이 URL 이면 ID 부분만 추출.

    예) https://docs.google.com/spreadsheets/d/<ID>/edit#gid=0 → <ID>
    """
    s = value.strip()
    if "/spreadsheets/d/" in s:
        try:
            after = s.split("/spreadsheets/d/", 1)[1]
            return after.split("/", 1)[0]
        except IndexError:
            return s
    return s


def sheet_url(spreadsheet_id: str) -> str:
    """시트 ID(또는 URL)로부터 표준 구글 스프레드시트 주소를 만든다. 비면 빈 문자열."""
    sid = normalize_spreadsheet_id(spreadsheet_id or "")
    if not sid:
        return ""
    return f"https://docs.google.com/spreadsheets/d/{sid}/edit"


# ---------- 요금제 문자열 → 개월 수 ----------

def parse_plan_months(text: str) -> int:
    """폼의 요금제 셀("3000=1개월" / "1개월" / "9000" 등)에서 개월 수 추출.

    1) "N개월" 패턴이 있으면 그 N.
    2) 없으면 문자열에서 첫 정수를 떼어 config.SUBSCRIPTION_PRICING 에 매핑.
    3) 둘 다 실패하면 0.
    """
    if not text:
        return 0
    s = str(text).strip()
    m = re.search(r"(\d+)\s*개월", s)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    m = re.search(r"\d+", s.replace(",", ""))
    if m:
        try:
            amount = int(m.group(0))
        except ValueError:
            return 0
        return SUBSCRIPTION_PRICING.get(amount, 0)
    return 0


# ---------- OAuth + Sheets API 클라이언트 ----------

class GoogleAuthError(Exception):
    """OAuth 인증 실패 또는 credentials.json 누락."""


def _ensure_credentials_file() -> bool:
    """data/google_credentials.json 이 없으면 PyInstaller 번들에서 복원.

    빌드 시점에 spec 의 datas 로 묶어둔 google_credentials.json 을 찾는다.
    찾는 위치 (우선순위 순):
        1) sys._MEIPASS / google_credentials.json     (frozen 빌드의 임시 폴더)
        2) sys._MEIPASS / data / google_credentials.json
        3) <exe-dir>/data/google_credentials.json     (개발 실행 시 동일 경로)

    Returns:
        True  — 이미 있거나 복원 성공
        False — 번들에도 없어서 복원 못함 (호출자가 사용자에게 안내)
    """
    if GOOGLE_CREDENTIALS_FILE.exists():
        return True
    import shutil
    import sys
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "google_credentials.json")
        candidates.append(Path(meipass) / "data" / "google_credentials.json")
    # frozen 이 아닌 환경 (개발 중) — 굳이 추가 처리 없음. data/ 가 정답.

    for src in candidates:
        try:
            if src.is_file():
                GOOGLE_CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, GOOGLE_CREDENTIALS_FILE)
                return True
        except OSError:
            continue
    return False


def _load_credentials():
    """OAuth 흐름 — 첫 호출이면 브라우저 인증, 이후 토큰 캐시 사용.

    google-auth-oauthlib 가 무거워 모듈 상단 import 를 피해 lazy 로드.
    PyInstaller 빌드 사이즈 영향 + 시작 속도.

    데스크톱 OAuth 클라이언트의 client_id 는 빌드 시 spec 으로 번들된다.
    data/google_credentials.json 이 없으면 _ensure_credentials_file 이 번들
    위치에서 자동 복원해 사용자는 브라우저 승인만 하면 된다.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not _ensure_credentials_file():
        raise GoogleAuthError(
            f"OAuth credentials 파일이 없습니다: {GOOGLE_CREDENTIALS_FILE}\n"
            "이 빌드에는 OAuth 클라이언트가 번들되어 있지 않습니다.\n"
            "Google Cloud Console 에서 OAuth Desktop 클라이언트를 만들고\n"
            "credentials.json 을 다운로드해 위 경로에 저장한 뒤 다시 시도하세요."
        )

    creds = None
    if GOOGLE_TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(
                str(GOOGLE_TOKEN_FILE), OAUTH_SCOPES
            )
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                raise GoogleAuthError(f"토큰 갱신 실패: {e}") from e
        else:
            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(GOOGLE_CREDENTIALS_FILE), OAUTH_SCOPES
                )
                creds = flow.run_local_server(port=0)
            except Exception as e:
                raise GoogleAuthError(f"브라우저 인증 실패: {e}") from e
        GOOGLE_TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    return creds


class SheetsSyncClient:
    """구글 시트 동기화 — 한 번 인증 후 여러 번 호출 가능."""

    def __init__(self, spreadsheet_id: str) -> None:
        if not spreadsheet_id:
            raise GoogleAuthError("spreadsheet_id 가 비어있습니다.")
        self.spreadsheet_id = spreadsheet_id
        self._service = None  # lazy

    def _svc(self):
        if self._service is None:
            from googleapiclient.discovery import build
            creds = _load_credentials()
            self._service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        return self._service

    def ensure_worksheets(self) -> None:
        """필요한 워크시트(탭) 가 없으면 생성."""
        meta = self._svc().spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
        existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
        wanted = (SHEET_ALIASES, SHEET_SUBSCRIPTIONS, SHEET_TRANSACTIONS)
        requests = [
            {"addSheet": {"properties": {"title": name}}}
            for name in wanted if name not in existing
        ]
        if requests:
            self._svc().spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"requests": requests},
            ).execute()

    # ---------- aliases (양방향) ----------

    def read_aliases(self) -> list[AliasEntry]:
        result = self._svc().spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range=f"{SHEET_ALIASES}!A:D",
        ).execute()
        rows = result.get("values", [])
        out: list[AliasEntry] = []
        # 첫 행은 헤더 — A1 이 "입금자명" 이면 스킵
        start = 1 if (rows and rows[0] and rows[0][0] == "입금자명") else 0
        for r in rows[start:]:
            if len(r) < 2 or not r[0]:
                continue
            payer = str(r[0])
            uid = str(r[1])
            ts_str = r[3] if len(r) >= 4 else ""
            try:
                ts = datetime.fromisoformat(str(ts_str))
            except (TypeError, ValueError):
                ts = datetime.fromtimestamp(0)  # 시트 행에 ts 없으면 가장 오래된 것으로 → SQLite 우선
            out.append(AliasEntry(payer, uid, ts))
        return out

    def write_aliases(
        self, entries: Iterable[AliasEntry], member_name_lookup: dict[str, str] | None = None
    ) -> None:
        """전체 덮어쓰기 — 시트의 alias_매핑 시트를 비운 후 헤더+엔트리."""
        lookup = member_name_lookup or {}
        values: list[list[str]] = [["입금자명", "회원ID", "회원이름", "수정시각"]]
        for e in entries:
            values.append([
                e.payer_name,
                e.member_user_id,
                lookup.get(e.member_user_id, ""),
                e.modified_at.isoformat(timespec="seconds"),
            ])
        # 기존 내용 클리어 후 쓰기
        self._svc().spreadsheets().values().clear(
            spreadsheetId=self.spreadsheet_id,
            range=f"{SHEET_ALIASES}!A:D",
        ).execute()
        self._svc().spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{SHEET_ALIASES}!A1",
            valueInputOption="RAW",
            body={"values": values},
        ).execute()

    # ---------- subscriptions (단방향 push) ----------

    def push_subscriptions(
        self,
        subscriptions: Iterable[Subscription],
        member_lookup: dict[str, Member],
    ) -> None:
        values: list[list[str]] = [
            ["회원ID", "회원이름", "닉네임", "개월", "시작일", "만료일", "거래ID"],
        ]
        for s in subscriptions:
            m = member_lookup.get(s.member_user_id)
            values.append([
                s.member_user_id,
                m.name if m else "",
                m.nickname if m else "",
                str(s.months),
                s.period_from.isoformat(),
                s.period_to.isoformat(),
                str(s.transaction_id),
            ])
        self._svc().spreadsheets().values().clear(
            spreadsheetId=self.spreadsheet_id,
            range=f"{SHEET_SUBSCRIPTIONS}!A:G",
        ).execute()
        self._svc().spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{SHEET_SUBSCRIPTIONS}!A1",
            valueInputOption="RAW",
            body={"values": values},
        ).execute()

    # ---------- transactions (단방향 push) ----------

    def push_transactions(self, transactions: Iterable[Transaction]) -> None:
        values: list[list[str]] = [
            ["거래일시", "입금자명", "금액", "거래기관", "메모", "원본파일"],
        ]
        for t in transactions:
            values.append([
                t.txn_at.isoformat(timespec="seconds"),
                t.payer_name,
                str(t.amount),
                t.bank,
                t.memo,
                t.source_file,
            ])
        self._svc().spreadsheets().values().clear(
            spreadsheetId=self.spreadsheet_id,
            range=f"{SHEET_TRANSACTIONS}!A:F",
        ).execute()
        self._svc().spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{SHEET_TRANSACTIONS}!A1",
            valueInputOption="RAW",
            body={"values": values},
        ).execute()

    # ---------- 자료실 신청 폼 응답 (읽기 + 행 추가) ----------

    def _list_sheet_titles(self) -> list[str]:
        meta = self._svc().spreadsheets().get(
            spreadsheetId=self.spreadsheet_id,
        ).execute()
        return [s["properties"]["title"] for s in meta.get("sheets", [])]

    def _find_form_sheet_title(self) -> str | None:
        """존재하는 폼 응답 탭 이름을 후보 순서대로 찾아 반환. 없으면 None."""
        titles = set(self._list_sheet_titles())
        for cand in FORM_RESPONSE_SHEET_CANDIDATES:
            if cand in titles:
                return cand
        return None

    def read_form_responses(self) -> list[FormApplicant]:
        """폼 응답 탭(설문지 응답 시트1)을 읽어 FormApplicant 목록 반환.

        탭이 없으면 빈 리스트. 1행이 헤더('타임스탬프')면 스킵. 희망아이디(F열)가
        비면 행 무시. 동의여부는 _AGREED_TRUTHY 로 bool 변환.
        """
        title = self._find_form_sheet_title()
        if title is None:
            return []
        result = self._svc().spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range=f"{title}!A:{FORM_LAST_COL}",
        ).execute()
        rows = result.get("values", [])
        if not rows:
            return []
        # 헤더 행 스킵 — A1 이 "타임스탬프" 거나, F1 이 "희망아이디" 면 헤더로 본다.
        first = rows[0]
        is_header = bool(first) and (
            (len(first) >= 1 and str(first[0]).strip() == "타임스탬프")
            or (len(first) >= 6 and str(first[FORM_COL_USERID]).strip() == "희망아이디")
        )
        start = 1 if is_header else 0

        def cell(r: list, idx: int) -> str:
            return str(r[idx]).strip() if len(r) > idx else ""

        out: list[FormApplicant] = []
        for r in rows[start:]:
            uid = cell(r, 5)  # F열 = 희망아이디
            if not uid:
                continue
            plan_raw = cell(r, 4)
            agreed_raw = cell(r, 8).lower().replace(" ", "")
            out.append(FormApplicant(
                member_user_id=uid,
                applied_at=cell(r, 0),
                name=cell(r, 1),
                phone=cell(r, 2),
                email=cell(r, 3),
                plan_raw=plan_raw,
                plan_months=parse_plan_months(plan_raw),
                agreed=agreed_raw in _AGREED_TRUTHY,
            ))
        return out

    def append_form_response(self, values: list[str]) -> str:
        """폼 응답 탭에 새 행 추가.

        values 는 FORM_RESPONSE_HEADERS 순서(A~Q, 17개):
            [타임스탬프, 이름, 전화번호, 이메일, 요금제, 희망아이디, 비밀번호,
             비밀번호확인, 동의여부, 시작일, 만료일, 결제안내발송, 환영메일발송,
             만료알림발송, 비활성화처리, 상태, 메모]
        17개보다 적게 주면 빈 칸으로 패딩, 많으면 절단. 탭이 없으면 만들고
        헤더를 먼저 쓴 뒤 append. 반환값은 사용된 탭 이름.
        """
        title = self._find_form_sheet_title()
        if title is None:
            title = FORM_RESPONSE_SHEET_CANDIDATES[0]
            self._svc().spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
            ).execute()
            self._svc().spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"{title}!A1",
                valueInputOption="RAW",
                body={"values": [FORM_RESPONSE_HEADERS]},
            ).execute()
        n = len(FORM_RESPONSE_HEADERS)
        row = list(values)[:n] + [""] * max(0, n - len(values))
        self._svc().spreadsheets().values().append(
            spreadsheetId=self.spreadsheet_id,
            range=f"{title}!A:{FORM_LAST_COL}",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()
        return title

    def _find_form_row(self, member_user_id: str) -> tuple[str, int] | None:
        """폼 응답 탭에서 희망아이디(F열) 가 일치하는 첫 행을 찾는다.

        Returns: (탭 제목, 1-기반 행 번호) — 탭이 없거나 일치 행이 없으면 None.
        여러 행이 같은 user_id 면 처음 발견된 행만 반환.
        """
        target = (member_user_id or "").strip().lower()
        if not target:
            return None
        title = self._find_form_sheet_title()
        if title is None:
            return None
        result = self._svc().spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range=f"{title}!A:{FORM_LAST_COL}",
        ).execute()
        rows = result.get("values", [])
        if not rows:
            return None
        first = rows[0]
        is_header = bool(first) and (
            (len(first) >= 1 and str(first[0]).strip() == "타임스탬프")
            or (len(first) > FORM_COL_USERID and str(first[FORM_COL_USERID]).strip() == "희망아이디")
        )
        for i, r in enumerate(rows):
            if i == 0 and is_header:
                continue
            uid_cell = (
                str(r[FORM_COL_USERID]).strip().lower()
                if len(r) > FORM_COL_USERID else ""
            )
            if uid_cell == target:
                return title, i + 1  # 시트는 1-기반
        return None

    def _set_form_cell(self, title: str, a1: str, value) -> None:
        self._svc().spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{title}!{a1}",
            valueInputOption="RAW",
            body={"values": [[value]]},
        ).execute()

    def update_form_activation(
        self,
        member_user_id: str,
        *,
        status: str | None = None,
        period_from: date | None = None,
        period_to: date | None = None,
    ) -> bool:
        """폼 응답 탭에서 희망아이디(F열) 가 일치하는 행에 상태/시작일/만료일 기록.

        주어진 값만 쓴다 — status 만 주면 '상태'(P열)만, period_from/period_to 도
        주면 '시작일'(J열)·'만료일'(K열)도. 날짜는 ISO(YYYY-MM-DD) 로 기록.
        일치 행이 없으면 False (앱으로만 만든 DSM 사용자 등 — 폼 행 없음).
        탭이 없으면 False.
        """
        found = self._find_form_row(member_user_id)
        if found is None:
            return False
        title, row_num = found
        if status is not None:
            self._set_form_cell(title, f"{FORM_STATUS_COL}{row_num}", status)
        if period_from is not None:
            self._set_form_cell(title, f"{FORM_PERIOD_FROM_COL}{row_num}", period_from.isoformat())
        if period_to is not None:
            self._set_form_cell(title, f"{FORM_PERIOD_TO_COL}{row_num}", period_to.isoformat())
        return True

    def update_form_status(self, member_user_id: str, status: str) -> bool:
        """폼 응답 탭에서 희망아이디(F열) 가 일치하는 행의 '상태'(P열)를 갱신.

        일치 행이 없으면 False (앱으로만 만든 DSM 사용자 등 — 폼 행 없음).
        탭이 없으면 False. 여러 행이 같은 user_id 면 처음 발견된 행만 갱신.
        """
        return self.update_form_activation(member_user_id, status=status)


# ---------- 통합 동기화 ----------

@dataclass
class SyncSummary:
    aliases_pulled_to_sqlite: int = 0
    aliases_pushed_to_sheet: int = 0
    subscriptions_pushed: int = 0
    transactions_pushed: int = 0
    form_applicants_pulled: int = 0


def run_full_sync(
    store: PaymentStore,
    members: list[Member],
    spreadsheet_id: str,
) -> SyncSummary:
    """전체 동기화 1회 — aliases 양방향 → subs/txns push → 폼 신청자 pull."""
    client = SheetsSyncClient(spreadsheet_id)
    client.ensure_worksheets()

    # aliases — 양방향
    sqlite_aliases = [
        AliasEntry(payer, uid, ts)
        for payer, uid, ts in store.all_aliases_detailed()
    ]
    sheet_aliases = client.read_aliases()
    merge = merge_aliases(sqlite_aliases, sheet_aliases)
    for e in merge.to_write_to_sqlite:
        store.set_alias(e.payer_name, e.member_user_id, modified_at=e.modified_at)
    member_name_lookup = {m.user_id: m.name for m in members}
    client.write_aliases(merge.to_write_to_sheet, member_name_lookup=member_name_lookup)

    # subscriptions — 앱 → 시트 push
    member_lookup = {m.user_id: m for m in members}
    subs = store.all_subscriptions()
    client.push_subscriptions(subs, member_lookup)

    # transactions — 앱 → 시트 push
    txns = store.all_transactions()
    client.push_transactions(txns)

    # 자료실 신청 폼 응답 — 시트 → 앱 pull (실패해도 나머지 동기화는 유지)
    applicants_pulled = 0
    try:
        applicants = client.read_form_responses()
        applicants_pulled = store.upsert_form_applicants(applicants)
    except Exception:
        applicants_pulled = 0

    return SyncSummary(
        aliases_pulled_to_sqlite=len(merge.to_write_to_sqlite),
        aliases_pushed_to_sheet=len(merge.to_write_to_sheet),
        subscriptions_pushed=len(subs),
        transactions_pushed=len(txns),
        form_applicants_pulled=applicants_pulled,
    )


# ---------- 폼 시트 '상태' 컬럼 갱신 (DSM 활성/비활성 버튼 연동) ----------

def push_form_status(
    member_user_id: str,
    status: str,
    *,
    period_from: "date | None" = None,
    period_to: "date | None" = None,
) -> str:
    """폼 응답 시트의 해당 회원 행 '상태' 컬럼을 status 로 갱신 — best-effort.

    period_from/period_to 를 주면 같은 행의 '시작일'(J)·'만료일'(K)도 함께 기록한다
    (DSM 사용자 생성/활성화 시 구독 기간을 시트에 적어 두는 용도).

    인증 전(토큰 캐시 없음) 이면 브라우저 OAuth 가 갑자기 뜨지 않도록 조용히
    스킵한다. 시트 ID 미설정도 스킵.

    Returns:
        ""            — 시트 미설정 또는 인증 전 (스킵)
        "updated"     — 행 찾아 갱신됨
        "not_found"   — 폼 시트엔 해당 user_id 행이 없음 (앱으로만 만든 사용자 등)
        "error:<msg>" — 갱신 시도 중 오류
    """
    if not GOOGLE_TOKEN_FILE.exists():
        return ""
    cfg = SheetsConfig.load()
    sid = (cfg.spreadsheet_id or "").strip()
    if not sid:
        return ""
    try:
        client = SheetsSyncClient(sid)
        ok = client.update_form_activation(
            member_user_id, status=status, period_from=period_from, period_to=period_to,
        )
        return "updated" if ok else "not_found"
    except GoogleAuthError as e:
        return f"error:auth:{e}"
    except Exception as e:
        return f"error:{e}"
