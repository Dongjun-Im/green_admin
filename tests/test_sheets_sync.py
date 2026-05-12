"""구글시트 양방향 동기화 — 충돌 해결 룰 단위 테스트.

실제 Google API 호출은 하지 않고 순수 함수(merge_aliases) 와 자격 파일
번들 복원 로직만 검증.
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import pytest

from core import sheets_sync
from core.sheets_sync import (
    AliasEntry,
    SheetsSyncClient,
    _ensure_credentials_file,
    merge_aliases,
    normalize_spreadsheet_id,
    parse_plan_months,
)


def _e(payer: str, uid: str, ts: datetime) -> AliasEntry:
    return AliasEntry(payer_name=payer, member_user_id=uid, modified_at=ts)


def test_merge_only_in_sqlite_kept_unchanged():
    """SQLite 에만 있는 매핑 → merged 에 그대로, 시트에는 새로 추가."""
    sql = [_e("홍길동", "hong", datetime(2026, 5, 1))]
    sheet: list[AliasEntry] = []
    r = merge_aliases(sql, sheet)
    assert {e.payer_name for e in r.merged} == {"홍길동"}
    assert r.to_write_to_sqlite == []  # SQLite 는 변경 불필요
    assert {e.payer_name for e in r.to_write_to_sheet} == {"홍길동"}


def test_merge_only_in_sheet_pulled_to_sqlite():
    """시트에만 있는 매핑 → SQLite 에 추가."""
    sql: list[AliasEntry] = []
    sheet = [_e("이지은", "iu", datetime(2026, 5, 5))]
    r = merge_aliases(sql, sheet)
    assert len(r.to_write_to_sqlite) == 1
    assert r.to_write_to_sqlite[0].member_user_id == "iu"


def test_merge_conflict_sheet_newer_wins():
    """양쪽 모두 있고 시트가 더 최근 → 시트 값으로 SQLite 갱신."""
    sql = [_e("홍길동", "hong_old", datetime(2026, 5, 1, 10, 0))]
    sheet = [_e("홍길동", "hong_new", datetime(2026, 5, 1, 12, 0))]
    r = merge_aliases(sql, sheet)
    chosen = next(e for e in r.merged if e.payer_name == "홍길동")
    assert chosen.member_user_id == "hong_new"
    assert r.to_write_to_sqlite == [_e("홍길동", "hong_new", datetime(2026, 5, 1, 12, 0))]


def test_merge_conflict_sqlite_newer_wins():
    """양쪽 모두 있고 SQLite 가 더 최근 → SQLite 값 유지, 시트는 덮어씀."""
    sql = [_e("홍길동", "hong_new", datetime(2026, 5, 1, 12, 0))]
    sheet = [_e("홍길동", "hong_old", datetime(2026, 5, 1, 10, 0))]
    r = merge_aliases(sql, sheet)
    chosen = next(e for e in r.merged if e.payer_name == "홍길동")
    assert chosen.member_user_id == "hong_new"
    assert r.to_write_to_sqlite == []  # SQLite 변경 불필요


def test_merge_same_value_same_ts_no_sqlite_write():
    """양쪽 동일 + 동일 timestamp → SQLite 갱신 불필요."""
    ts = datetime(2026, 5, 1, 10, 0)
    sql = [_e("홍길동", "hong", ts)]
    sheet = [_e("홍길동", "hong", ts)]
    r = merge_aliases(sql, sheet)
    assert r.to_write_to_sqlite == []
    assert len(r.merged) == 1


def test_merge_mixed_scenarios():
    """현실적인 혼합 케이스 — SQLite/시트 양쪽 + 충돌 + 동일."""
    sql = [
        _e("홍길동", "hong",   datetime(2026, 5, 1)),  # 양쪽 동일
        _e("김철수", "kim_old", datetime(2026, 5, 1)),  # 충돌, sheet win
        _e("박영희", "park",   datetime(2026, 5, 5)),  # SQLite only
    ]
    sheet = [
        _e("홍길동", "hong",     datetime(2026, 5, 1)),
        _e("김철수", "kim_new",  datetime(2026, 6, 1)),  # 더 최근
        _e("이지은", "iu",       datetime(2026, 5, 8)),  # sheet only
    ]
    r = merge_aliases(sql, sheet)
    merged_by = {e.payer_name: e for e in r.merged}
    assert merged_by["홍길동"].member_user_id == "hong"
    assert merged_by["김철수"].member_user_id == "kim_new"
    assert merged_by["박영희"].member_user_id == "park"
    assert merged_by["이지은"].member_user_id == "iu"
    # SQLite 갱신: 김철수(시트가 더 최근), 이지은(시트에만)
    sqlite_writes = {e.payer_name: e.member_user_id for e in r.to_write_to_sqlite}
    assert sqlite_writes == {"김철수": "kim_new", "이지은": "iu"}


# ---------- normalize_spreadsheet_id ----------

def test_normalize_spreadsheet_id_from_url():
    url = "https://docs.google.com/spreadsheets/d/1ABCxyz_123-fakeID/edit#gid=0"
    assert normalize_spreadsheet_id(url) == "1ABCxyz_123-fakeID"


def test_normalize_spreadsheet_id_from_url_without_edit():
    url = "https://docs.google.com/spreadsheets/d/abc123"
    assert normalize_spreadsheet_id(url) == "abc123"


def test_normalize_spreadsheet_id_already_id():
    assert normalize_spreadsheet_id("plain_id_string") == "plain_id_string"


def test_normalize_spreadsheet_id_strips_whitespace():
    assert normalize_spreadsheet_id("  abc123  ") == "abc123"


# ---------- _ensure_credentials_file ----------

def test_ensure_credentials_returns_true_when_file_exists(monkeypatch, tmp_path):
    """data/google_credentials.json 이 이미 있으면 그대로 True."""
    target = tmp_path / "google_credentials.json"
    target.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sheets_sync, "GOOGLE_CREDENTIALS_FILE", target)
    assert _ensure_credentials_file() is True


def test_ensure_credentials_restores_from_meipass_root(monkeypatch, tmp_path):
    """frozen 빌드에서 sys._MEIPASS 루트의 번들 자격을 복사."""
    bundle_dir = tmp_path / "meipass"
    bundle_dir.mkdir()
    (bundle_dir / "google_credentials.json").write_text(
        '{"installed":{"client_id":"x"}}', encoding="utf-8",
    )
    target = tmp_path / "data" / "google_credentials.json"
    monkeypatch.setattr(sheets_sync, "GOOGLE_CREDENTIALS_FILE", target)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_dir), raising=False)

    assert _ensure_credentials_file() is True
    assert target.exists()
    assert "client_id" in target.read_text(encoding="utf-8")


def test_ensure_credentials_restores_from_meipass_data_subdir(monkeypatch, tmp_path):
    """루트엔 없고 data/ 하위에만 있는 빌드 변종도 처리."""
    bundle_dir = tmp_path / "meipass"
    (bundle_dir / "data").mkdir(parents=True)
    (bundle_dir / "data" / "google_credentials.json").write_text(
        '{"installed":{"client_id":"y"}}', encoding="utf-8",
    )
    target = tmp_path / "out" / "google_credentials.json"
    monkeypatch.setattr(sheets_sync, "GOOGLE_CREDENTIALS_FILE", target)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_dir), raising=False)

    assert _ensure_credentials_file() is True
    assert target.exists()


def test_ensure_credentials_returns_false_when_no_bundle(monkeypatch, tmp_path):
    """번들에도 없으면 False — 호출자가 사용자에게 안내."""
    target = tmp_path / "google_credentials.json"  # 없음
    monkeypatch.setattr(sheets_sync, "GOOGLE_CREDENTIALS_FILE", target)
    bundle_dir = tmp_path / "empty_meipass"
    bundle_dir.mkdir()
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_dir), raising=False)

    assert _ensure_credentials_file() is False
    assert not target.exists()


# ---------- parse_plan_months ----------

def test_parse_plan_months_amount_equals_label():
    assert parse_plan_months("3000=1개월") == 1
    assert parse_plan_months("9000=3개월") == 3
    assert parse_plan_months("12000=6개월") == 6
    assert parse_plan_months("24000=12개월") == 12


def test_parse_plan_months_label_only():
    assert parse_plan_months("1개월") == 1
    assert parse_plan_months("6 개월") == 6
    assert parse_plan_months("12개월 구독") == 12


def test_parse_plan_months_amount_only():
    assert parse_plan_months("9000") == 3
    assert parse_plan_months("12,000원") == 6
    assert parse_plan_months("24000원 결제") == 12


def test_parse_plan_months_unknown_returns_zero():
    assert parse_plan_months("") == 0
    assert parse_plan_months("기타") == 0
    assert parse_plan_months("5000") == 0  # 단가표에 없는 금액


# ---------- 폼 응답 읽기 / 행 추가 (가짜 service 주입) ----------

class _FakeValues:
    def __init__(self, get_result=None, capture=None):
        self._get_result = get_result or {}
        self._capture = capture if capture is not None else {}

    def get(self, *, spreadsheetId=None, range=None, **kwargs):
        self._capture["get_range"] = range
        return _Exec(self._get_result)

    def append(self, *, spreadsheetId=None, range=None, valueInputOption=None,
               insertDataOption=None, body=None, **kwargs):
        self._capture["append_range"] = range
        self._capture["append_body"] = body
        self._capture["append_opts"] = (valueInputOption, insertDataOption)
        return _Exec({"updates": {"updatedRows": 1}})

    def update(self, *, spreadsheetId=None, range=None, valueInputOption=None,
               body=None, **kwargs):
        self._capture.setdefault("updates", []).append((range, body))
        return _Exec({})

    def clear(self, *, spreadsheetId=None, range=None, **kwargs):
        return _Exec({})


class _FakeSpreadsheets:
    def __init__(self, sheet_titles, values: _FakeValues, capture):
        self._sheet_titles = sheet_titles
        self._values = values
        self._capture = capture

    def get(self, *, spreadsheetId=None, **kwargs):
        return _Exec({"sheets": [{"properties": {"title": t}} for t in self._sheet_titles]})

    def batchUpdate(self, *, spreadsheetId=None, body=None, **kwargs):
        self._capture.setdefault("batchUpdate", []).append(body)
        for req in (body or {}).get("requests", []):
            title = req.get("addSheet", {}).get("properties", {}).get("title")
            if title:
                self._sheet_titles.append(title)
        return _Exec({})

    def values(self):
        return self._values


class _FakeService:
    def __init__(self, sheet_titles, get_result=None):
        self._capture: dict = {}
        self._values = _FakeValues(get_result=get_result, capture=self._capture)
        self._spreadsheets = _FakeSpreadsheets(sheet_titles, self._values, self._capture)

    def spreadsheets(self):
        return self._spreadsheets


class _Exec:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


def _client_with_fake(sheet_titles, get_result=None) -> SheetsSyncClient:
    c = SheetsSyncClient("fake_sid")
    svc = _FakeService(list(sheet_titles), get_result=get_result)
    c._service = svc  # _svc() 가 lazy build 를 건너뛴다
    return c


def test_read_form_responses_parses_rows():
    rows = [
        ["타임스탬프", "이름", "전화번호", "이메일", "요금제", "희망아이디", "비밀번호", "비밀번호확인", "동의여부"],
        ["2026-06-01 10:00:00", "홍길동", "010-1111-2222", "h@a.com", "3000=1개월", "hong", "pw1", "pw1", "동의함"],
        ["2026-06-02 11:00:00", "김철수", "010-3333-4444", "k@a.com", "12000=6개월", "kim", "pw2", "pw2", "예"],
        ["2026-06-03 12:00:00", "익명", "", "", "9000=3개월", "", "x", "x", "동의함"],  # 희망아이디 빈 행 → 스킵
    ]
    c = _client_with_fake(["설문지 응답 시트1"], get_result={"values": rows})
    out = c.read_form_responses()
    assert [a.member_user_id for a in out] == ["hong", "kim"]
    assert out[0].name == "홍길동"
    assert out[0].plan_months == 1
    assert out[0].agreed is True
    assert out[1].plan_months == 6
    assert out[1].agreed is True


def test_read_form_responses_no_tab_returns_empty():
    c = _client_with_fake(["다른탭"], get_result={"values": []})
    assert c.read_form_responses() == []


_FORM_ROW_17 = [
    "2026-06-05 09:00:00", "박영희", "010-5555", "p@a.com", "24000=12개월",
    "park", "pw", "pw", "동의함", "", "", "", "", "", "", "활성", "회원관리 앱에 의해 생성",
]


def test_append_form_response_to_existing_tab():
    c = _client_with_fake(["설문지 응답 시트1"], get_result={"values": []})
    title = c.append_form_response(list(_FORM_ROW_17))
    assert title == "설문지 응답 시트1"
    cap = c._service._capture
    assert cap["append_range"] == "설문지 응답 시트1!A:Q"
    row = cap["append_body"]["values"][0]
    assert row[5] == "park"   # F열 = 희망아이디
    assert row[15] == "활성"  # P열 = 상태
    assert row[16] == "회원관리 앱에 의해 생성"  # Q열 = 메모
    assert len(row) == 17


def test_append_form_response_pads_short_row_to_17():
    c = _client_with_fake(["설문지 응답 시트1"], get_result={"values": []})
    # 9개만 줘도 17개로 패딩
    c.append_form_response(
        ["2026-06-05 09:00:00", "박영희", "010", "p@a.com", "3000=1개월", "park", "pw", "pw", "동의함"],
    )
    row = c._service._capture["append_body"]["values"][0]
    assert len(row) == 17
    assert row[15] == ""  # 상태 빈칸


def test_append_form_response_creates_tab_if_missing():
    c = _client_with_fake(["users"], get_result={"values": []})
    title = c.append_form_response(list(_FORM_ROW_17))
    assert title == "설문지 응답 시트1"
    cap = c._service._capture
    assert cap["batchUpdate"]  # addSheet 호출됨
    header_writes = [body["values"][0] for _r, body in cap.get("updates", [])]
    expected_header = [
        "타임스탬프", "이름", "전화번호", "이메일", "요금제", "희망아이디",
        "비밀번호", "비밀번호확인", "동의여부", "시작일", "만료일",
        "결제안내발송", "환영메일발송", "만료알림발송", "비활성화처리", "상태", "메모",
    ]
    assert expected_header in header_writes
    assert cap["append_body"]["values"][0][5] == "park"


# ---------- update_form_status ----------

def test_update_form_status_finds_row_and_updates_P_column():
    rows = [
        ["타임스탬프", "이름", "전화번호", "이메일", "요금제", "희망아이디", "비밀번호", "비밀번호확인", "동의여부",
         "시작일", "만료일", "결제안내발송", "환영메일발송", "만료알림발송", "비활성화처리", "상태", "메모"],
        ["2026-06-01", "홍길동", "010", "h@a", "3000=1개월", "hong", "pw", "pw", "동의함", "", "", "", "", "", "", "활성", ""],
        ["2026-06-02", "김철수", "010", "k@a", "9000=3개월", "kim",  "pw", "pw", "동의함", "", "", "", "", "", "", "활성", ""],
    ]
    c = _client_with_fake(["설문지 응답 시트1"], get_result={"values": rows})
    assert c.update_form_status("kim", "비활성") is True
    # kim 은 3번째 행(헤더 포함) → 시트 행 번호 3 → P3
    updates = c._service._capture.get("updates", [])
    assert any(r == "설문지 응답 시트1!P3" and body["values"] == [["비활성"]]
               for r, body in updates)


def test_update_form_status_case_insensitive():
    rows = [
        ["타임스탬프", "이름", "전화번호", "이메일", "요금제", "희망아이디", "비밀번호", "비밀번호확인", "동의여부",
         "", "", "", "", "", "", "상태", "메모"],
        ["2026-06-01", "홍길동", "010", "h@a", "3000=1개월", "Hong", "pw", "pw", "동의함",
         "", "", "", "", "", "", "활성", ""],
    ]
    c = _client_with_fake(["설문지 응답 시트1"], get_result={"values": rows})
    assert c.update_form_status("HONG", "비활성") is True


def test_update_form_status_not_found_returns_false():
    rows = [
        ["타임스탬프", "이름", "전화번호", "이메일", "요금제", "희망아이디", "비밀번호", "비밀번호확인", "동의여부",
         "", "", "", "", "", "", "상태", "메모"],
        ["2026-06-01", "홍길동", "010", "h@a", "3000=1개월", "hong", "pw", "pw", "동의함",
         "", "", "", "", "", "", "활성", ""],
    ]
    c = _client_with_fake(["설문지 응답 시트1"], get_result={"values": rows})
    assert c.update_form_status("ghost", "비활성") is False
    # 갱신 호출 없어야 함
    assert not c._service._capture.get("updates")


def test_update_form_status_no_tab_returns_false():
    c = _client_with_fake(["다른탭"], get_result={"values": []})
    assert c.update_form_status("hong", "활성") is False


# ---------- update_form_activation (상태 + 시작일/만료일) ----------

_FORM_HEADER_17 = [
    "타임스탬프", "이름", "전화번호", "이메일", "요금제", "희망아이디",
    "비밀번호", "비밀번호확인", "동의여부", "시작일", "만료일",
    "결제안내발송", "환영메일발송", "만료알림발송", "비활성화처리", "상태", "메모",
]


def test_update_form_activation_writes_status_and_dates():
    rows = [
        list(_FORM_HEADER_17),
        ["2026-06-01", "홍길동", "010", "h@a", "3000=1개월", "hong", "pw", "pw", "동의함",
         "", "", "", "", "", "", "", ""],
    ]
    c = _client_with_fake(["설문지 응답 시트1"], get_result={"values": rows})
    ok = c.update_form_activation(
        "hong", status="활성",
        period_from=date(2026, 6, 1), period_to=date(2026, 6, 30),
    )
    assert ok is True
    # hong 은 2번째 행(헤더 포함) → 시트 행 번호 2
    updates = dict(c._service._capture.get("updates", []))  # {range: body}
    assert updates["설문지 응답 시트1!P2"]["values"] == [["활성"]]
    assert updates["설문지 응답 시트1!J2"]["values"] == [["2026-06-01"]]
    assert updates["설문지 응답 시트1!K2"]["values"] == [["2026-06-30"]]


def test_update_form_activation_dates_only_does_not_touch_status():
    rows = [
        list(_FORM_HEADER_17),
        ["2026-06-01", "홍길동", "010", "h@a", "3000=1개월", "hong", "pw", "pw", "동의함",
         "", "", "", "", "", "", "활성", ""],
    ]
    c = _client_with_fake(["설문지 응답 시트1"], get_result={"values": rows})
    ok = c.update_form_activation(
        "hong", period_from=date(2026, 7, 1), period_to=date(2026, 12, 31),
    )
    assert ok is True
    ranges = [r for r, _ in c._service._capture.get("updates", [])]
    assert "설문지 응답 시트1!J2" in ranges
    assert "설문지 응답 시트1!K2" in ranges
    assert all(not r.endswith("!P2") for r in ranges)  # status 안 줬으니 P 안 건드림


def test_update_form_activation_not_found_returns_false():
    rows = [list(_FORM_HEADER_17),
            ["2026-06-01", "홍길동", "010", "h@a", "3000=1개월", "hong", "pw", "pw", "동의함",
             "", "", "", "", "", "", "활성", ""]]
    c = _client_with_fake(["설문지 응답 시트1"], get_result={"values": rows})
    assert c.update_form_activation(
        "ghost", status="활성", period_from=date(2026, 6, 1), period_to=date(2026, 6, 30),
    ) is False
    assert not c._service._capture.get("updates")


def test_update_form_status_still_writes_only_P_column():
    """리팩터 회귀 — update_form_status 는 여전히 상태(P) 한 칸만 쓴다 (J/K 안 건드림)."""
    rows = [
        list(_FORM_HEADER_17),
        ["2026-06-01", "홍길동", "010", "h@a", "3000=1개월", "hong", "pw", "pw", "동의함",
         "2026-06-01", "2026-06-30", "", "", "", "", "활성", ""],
    ]
    c = _client_with_fake(["설문지 응답 시트1"], get_result={"values": rows})
    assert c.update_form_status("hong", "비활성") is True
    ranges = [r for r, _ in c._service._capture.get("updates", [])]
    assert ranges == ["설문지 응답 시트1!P2"]


# ---------- push_form_status (module-level, 토큰 가드) ----------

def test_push_form_status_skips_without_token(monkeypatch, tmp_path):
    # 토큰 파일이 없으면 OAuth 브라우저가 뜨지 않게 조용히 스킵.
    monkeypatch.setattr(sheets_sync, "GOOGLE_TOKEN_FILE", tmp_path / "no_token.json")
    assert sheets_sync.push_form_status("hong", "활성") == ""


def test_push_form_status_skips_without_spreadsheet_id(monkeypatch, tmp_path):
    tok = tmp_path / "google_token.json"
    tok.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sheets_sync, "GOOGLE_TOKEN_FILE", tok)
    monkeypatch.setattr(
        sheets_sync.SheetsConfig, "load",
        classmethod(lambda cls, path=None: sheets_sync.SheetsConfig()),
    )
    assert sheets_sync.push_form_status("hong", "활성") == ""
