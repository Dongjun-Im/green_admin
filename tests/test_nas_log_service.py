"""NasLogService — DSM 응답 파싱·회원 매칭·증분 수집 단위 테스트.

DSM 호출은 mock — DsmClient 의 list_audit_logs / collect_audit_log_diagnostics
를 가짜로 대체해 흐름만 검증.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from core.dsm_client import DsmAuthError
from core.models import Member
from core.nas_log_service import (
    ACTION_COPY,
    ACTION_DELETE,
    ACTION_DOWNLOAD,
    ACTION_LOGIN,
    ACTION_LOGOUT,
    ACTION_MKDIR,
    ACTION_MOVE,
    ACTION_OTHER,
    ACTION_RENAME,
    ACTION_UPLOAD,
    EnrichedEntry,
    enrich_with_members,
    fetch_and_store_logs,
    _clean_dsm_username,
    _parse_entry,
    _split_path,
    _structured_action,
)
from core.nas_log_store import NasLogEntry, NasLogStore


# ---------- _parse_entry: 동작 정규화 ----------

def test_parse_english_webdav_download():
    e = _parse_entry({
        "time": 1715500000,
        "descr": "User [anycall] from [121.129.43.13] via [WebDAV] downloaded file [/photo/엔터테인먼트/a.mp3]",
    })
    assert e.dsm_user_id == "anycall"
    assert e.ip == "121.129.43.13"
    assert e.protocol == "WebDAV"
    assert e.action == ACTION_DOWNLOAD
    assert e.category == "photo"
    assert e.file_name == "a.mp3"
    assert e.file_path == "/photo/엔터테인먼트/a.mp3"


def test_parse_english_smb_delete():
    e = _parse_entry({
        "time": 1715500100,
        "descr": "User [imdj] from [10.0.0.5] via [SMB] deleted file [/photo/foo.zip]",
    })
    assert e.dsm_user_id == "imdj" and e.action == ACTION_DELETE
    assert e.protocol == "SMB" and e.category == "photo"


def test_parse_login():
    e = _parse_entry({
        "time": 1715500200,
        "descr": "User [anycall] logged in from [121.129.43.13] via [DSM]",
    })
    assert e.action == ACTION_LOGIN and e.dsm_user_id == "anycall"


def test_parse_logout():
    e = _parse_entry({
        "time": 1715500300,
        "descr": "User [anycall] logged out from [121.129.43.13]",
    })
    assert e.action == ACTION_LOGOUT


def test_parse_korean_delete():
    e = _parse_entry({
        "time": 1715500400,
        "descr": "사용자 [holder] 가 [10.0.0.6] 에서 [SMB] 를 통해 [/share/bar.txt] 을 삭제했습니다",
    })
    assert e.action == ACTION_DELETE
    assert e.dsm_user_id == "holder"
    assert e.protocol == "SMB"


def test_parse_korean_upload():
    e = _parse_entry({
        "time": 1715500500,
        "descr": "사용자 [hong] 가 [WebDAV] 를 통해 [/data/한글파일.zip] 을 업로드했습니다",
    })
    assert e.action == ACTION_UPLOAD


def test_parse_rename():
    e = _parse_entry({
        "time": 1715500600,
        "descr": "User [hong] renamed [/old.txt] to [/new.txt]",
    })
    assert e.action == ACTION_RENAME


def test_parse_mkdir():
    e = _parse_entry({
        "time": 1715500700,
        "descr": "User [hong] created folder [/photo/new_dir]",
    })
    assert e.action == ACTION_MKDIR


def test_parse_unknown_falls_back_to_other():
    e = _parse_entry({
        "time": 1715500800,
        "descr": "Some unknown activity",
    })
    assert e.action == ACTION_OTHER
    assert e.raw_message == "Some unknown activity"


def test_parse_structured_fields_preferred():
    """구조화 필드가 있으면 descr 보다 우선 — 다양한 빌드 호환."""
    e = _parse_entry({
        "time": 1715500900,
        "user": "alice",
        "ip": "10.0.0.99",
        "protocol": "FileStation",
        "file": "/test/x.bin",
        "descr": "User [bob] from [1.1.1.1] downloaded file [/wrong/path]",
        "action": "deleted",
    })
    assert e.dsm_user_id == "alice"
    assert e.ip == "10.0.0.99"
    assert e.protocol == "FileStation"
    assert e.file_path == "/test/x.bin"
    assert e.action == ACTION_DELETE


# ---------- _split_path ----------

def test_split_path_typical():
    assert _split_path("/엔터테인먼트/foo.zip") == ("엔터테인먼트", "foo.zip")


def test_split_path_no_subdir():
    # 루트 바로 아래 파일은 카테고리 분류 없음.
    assert _split_path("/foo.zip") == ("", "foo.zip")


def test_split_path_nested():
    assert _split_path("/a/b/c/file.txt") == ("a", "file.txt")


def test_split_path_empty():
    assert _split_path("") == ("", "")
    assert _split_path("/") == ("", "")


def test_split_path_backslash():
    # Windows 스타일도 받아낸다
    assert _split_path("\\series\\info\\manual.pdf") == ("series", "manual.pdf")


# ---------- enrich_with_members ----------

def test_enrich_matches_case_insensitive():
    entries = [
        NasLogEntry(logged_at="t", dsm_user_id="anycall", raw_hash="x"),
        NasLogEntry(logged_at="t", dsm_user_id="unknown", raw_hash="y"),
    ]
    members = [Member(user_id="AnyCall", name="임동준", nickname="dj")]
    rows = enrich_with_members(entries, members)
    assert rows[0].member is not None
    assert rows[0].display_name == "임동준(anycall)"
    assert rows[1].member is None
    assert "미등록" in rows[1].display_name


def test_enrich_handles_empty_user_as_system():
    entries = [NasLogEntry(logged_at="t", dsm_user_id="", raw_hash="z")]
    rows = enrich_with_members(entries, [])
    assert rows[0].display_name == "(시스템)"


# ---------- _clean_dsm_username ----------

def test_clean_dsm_username_lowercases_and_strips():
    assert _clean_dsm_username("  AnyCall  ") == "anycall"


def test_clean_dsm_username_strips_domain_backslash():
    assert _clean_dsm_username(r"DOMAIN\anycall") == "anycall"


def test_clean_dsm_username_strips_domain_forward_slash():
    assert _clean_dsm_username("CORP/anycall") == "anycall"


def test_clean_dsm_username_strips_email_suffix():
    assert _clean_dsm_username("anycall@gmail.com") == "anycall"


def test_clean_dsm_username_empty():
    assert _clean_dsm_username("") == ""
    assert _clean_dsm_username("   ") == ""


# ---------- 매칭 우선순위 (소리샘 → 자료실 그룹 → 미등록) ----------

def test_enrich_priority_sorisem_member_first():
    """DSM 로그의 user_id 가 소리샘 회원 user_id 와 일치하면 그 회원 정보로 표시."""
    entries = [NasLogEntry(logged_at="t", dsm_user_id="anycall", raw_hash="x")]
    members = [Member(user_id="anycall", name="임동준", nickname="dj")]
    rows = enrich_with_members(entries, members, dsm_group_member_ids=["anycall"])
    assert rows[0].member is members[0]
    assert "임동준" in rows[0].display_name and "anycall" in rows[0].display_name


def test_enrich_priority_dsm_group_when_no_sorisem_match():
    """소리샘에 없어도 DSM 자료실 그룹에 있으면 '(자료실 회원)' 으로 표시."""
    entries = [NasLogEntry(logged_at="t", dsm_user_id="bob", raw_hash="y")]
    rows = enrich_with_members(entries, [Member(user_id="alice")], dsm_group_member_ids=["bob"])
    assert rows[0].member is None
    assert rows[0].is_dsm_group is True
    assert "자료실 회원" in rows[0].display_name


def test_enrich_priority_neither_marks_unregistered():
    entries = [NasLogEntry(logged_at="t", dsm_user_id="stranger", raw_hash="z")]
    rows = enrich_with_members(entries, [], dsm_group_member_ids=["other"])
    assert rows[0].is_dsm_group is False
    assert "미등록" in rows[0].display_name


def test_enrich_domain_prefixed_uid_matches_sorisem():
    """parse_entry 가 도메인 접두사를 떼고 저장하지만, 매칭 단계에서도 추가 정리.
    저장 시점에 이미 정리됐다는 가정 + 만약 직접 NasLogEntry 를 만들 때도 통과."""
    entries = [NasLogEntry(logged_at="t", dsm_user_id="anycall", raw_hash="x")]
    members = [Member(user_id=r"DOMAIN\AnyCall", name="임동준")]
    rows = enrich_with_members(entries, members)
    assert rows[0].member is members[0]


# ---------- _structured_action ----------

def test_structured_action_event_type_download():
    assert _structured_action({"event_type": "Download"}) == ACTION_DOWNLOAD


def test_structured_action_type_partial_match():
    """FILE_DELETE 같은 합성 값도 부분 일치로 잡힘."""
    assert _structured_action({"type": "FILE_DELETE"}) == ACTION_DELETE


def test_structured_action_op_short_codes():
    assert _structured_action({"op": "mv"}) == ACTION_MOVE
    assert _structured_action({"op": "cp"}) == ACTION_COPY


def test_structured_action_returns_empty_when_no_field():
    assert _structured_action({"foo": "bar"}) == ""


def test_parse_entry_prefers_structured_event_type_over_descr():
    """event_type='Download' + descr 가 'something' 이어도 download 로 분류돼야 함."""
    e = _parse_entry({
        "time": 1, "user": "anycall",
        "event_type": "Download",
        "descr": "Some other text without keywords",
        "filepath": "/photo/x.zip",
    })
    assert e.action == ACTION_DOWNLOAD


def test_parse_entry_cleans_domain_prefixed_user():
    """DOMAIN\\user 형태의 user_id 도 저장 시 정리되어 매칭 가능."""
    e = _parse_entry({
        "time": 1, "user": r"DOMAIN\anycall",
        "event_type": "Login",
    })
    assert e.dsm_user_id == "anycall"


# ---------- fetch_and_store_logs: 진행 콜백 + DSM 그룹 ----------

class _FakeClientWithGroup:
    def __init__(self):
        self.calls: list[str] = []

    def list_audit_logs(self, logtype, *, start_epoch=None, limit=1000):
        self.calls.append(f"log:{logtype}")
        if logtype == "file_transfer":
            return [{"time": 1, "user": "anycall", "descr": "User [anycall] from [10.0.0.1] via [WebDAV] downloaded file [/photo/a.zip]"}]
        return [{"time": 2, "descr": "User [anycall] logged in from [10.0.0.1]"}]

    def list_group_members(self, group_name):
        self.calls.append(f"group:{group_name}")
        return [{"name": "anycall"}, {"name": "kim"}]


def test_fetch_and_store_reports_progress_steps(tmp_path):
    store = NasLogStore(tmp_path / "n.db")
    client = _FakeClientWithGroup()
    steps: list[tuple[int, int, str]] = []
    res = fetch_and_store_logs(
        client, store,
        dsm_group_name="자료실 회원",
        progress_cb=lambda c, t, m: steps.append((c, t, m)),
    )
    assert res.ok is True
    # 진행 콜백이 5단계로 호출됨
    assert len(steps) >= 5
    assert all(t == 5 for _, t, _ in steps)
    # 자료실 그룹 멤버 ID 반환 + 메타에도 캐시
    assert set(res.dsm_group_member_ids) == {"anycall", "kim"}
    assert set(store.dsm_group_members()) == {"anycall", "kim"}


# ---------- 실제 DSM 응답 샘플 회귀 (소리샘 NAS 빌드) ----------

def test_parse_real_webdav_delete_sample():
    """실제 소리샘 NAS WebDAV 응답 — cmd=delete + descr=경로 + username 필드.
    이전엔 cmd 필드가 무시되어 OTHER 였음."""
    e = _parse_entry({
        "cmd": "delete",
        "descr": "/2. 엔터테인먼트 자료실/자료요청/애니메이션.hwp",
        "filesize": "8.50 KB",
        "ip": "192.168.0.1",
        "isdir": "false",
        "logtype": "WebDAV",
        "orginalLogType": "webdavxfer",
        "time": "2026/05/16 07:37:46",
        "username": "rtgreen",
    })
    assert e.action == "delete"
    assert e.dsm_user_id == "rtgreen"
    assert e.protocol == "WebDAV"
    # descr 자체가 경로인 빌드 — file_path 로 채택, 카테고리/파일명 추출.
    assert e.file_path.endswith("애니메이션.hwp")
    assert e.category == "2. 엔터테인먼트 자료실"
    assert e.file_name == "애니메이션.hwp"


def test_parse_real_webdav_download_sample():
    e = _parse_entry({
        "cmd": "download",
        "descr": "/2. 엔터테인먼트 자료실/일본/foo.mkv",
        "filesize": "300.25 MB",
        "ip": "211.117.114.114",
        "isdir": "false",
        "logtype": "WebDAV",
        "orginalLogType": "webdavxfer",
        "time": "2026/05/15 20:40:14",
        "username": "elite",
    })
    assert e.action == "download"
    assert e.dsm_user_id == "elite"
    assert e.protocol == "WebDAV"
    assert e.file_name == "foo.mkv"


def test_parse_real_sample_failed_signin_classifies_as_connect_fail():
    """실제 소리샘 DSM 응답 — 'failed to sign in ... authorization failure' 가
    이제 ACTION_FAIL 로 잡혀야 한다 (이전엔 OTHER 였음)."""
    e = _parse_entry({
        "descr": "User [bnradmin] from [93.152.221.14] failed to sign in to [DSM] via [password] due to authorization failure.",
        "level": "warn",
        "logtype": "Connection",
        "orginalLogType": "connection",
        "time": "2026/05/16 01:10:43",
        "who": "bnradmin",
    })
    assert e.action == "connect_fail"
    # 'who' 필드도 user_id 로 반영
    assert e.dsm_user_id == "bnradmin"
    assert e.ip == "93.152.221.14"
    assert e.protocol == "DSM"


def test_parse_who_field_alone_extracts_user():
    """user/username 이 없고 who 만 있어도 user_id 추출."""
    e = _parse_entry({"who": "anycall", "descr": "x"})
    assert e.dsm_user_id == "anycall"


def test_parse_connection_logtype_fallback_to_login():
    """descr 에 'connect' 같은 약한 신호만 있고 logtype 이 Connection 이면 폴백 로그인."""
    e = _parse_entry({
        "who": "anycall",
        "descr": "User [anycall] established connect session.",  # 정상 키워드 매치 안 됨
        "logtype": "Connection",
    })
    # 폴백 — body 에 'connect' 들어 있고 fail 단어 없음 → login
    assert e.action == "login"


def test_parse_connection_logtype_fallback_to_fail_when_failure_word():
    e = _parse_entry({
        "who": "x",
        "descr": "Unusual blocked attempt",
        "logtype": "Connection",
    })
    assert e.action == "connect_fail"


def test_enrich_external_attacker_label():
    """소리샘에도 DSM 그룹에도 없는 connect_fail → (외부 시도) 라벨."""
    e = NasLogEntry(
        logged_at="2026-05-16T01:10:43", dsm_user_id="bnradmin",
        action="connect_fail", raw_hash="x",
    )
    rows = enrich_with_members([e], [], dsm_group_member_ids=[])
    assert "외부 시도" in rows[0].display_name


def test_enrich_real_member_failed_login_keeps_name():
    """진짜 회원이 비밀번호 잘못 입력해도 이름이 그대로 보여야 한다 (외부 시도 X)."""
    e = NasLogEntry(
        logged_at="t", dsm_user_id="anycall", action="connect_fail", raw_hash="x",
    )
    rows = enrich_with_members([e], [Member(user_id="anycall", name="임동준")])
    assert "임동준" in rows[0].display_name
    assert "외부" not in rows[0].display_name


def test_fetch_and_store_marks_file_transfer_disabled_when_empty(tmp_path):
    """파일 전송 로그가 빈 응답으로 오고 연결 로그만 있으면 'disabled' 플래그 set."""
    store = NasLogStore(tmp_path / "n.db")
    client = _FakeDsmClient(by_logtype={
        # file_transfer 는 빈 응답만 옴 (DSM 에서 꺼져 있다는 가정)
        "connection": [{"time": 1, "who": "anycall",
                        "descr": "User [anycall] from [1.1.1.1] failed to sign in to [DSM] via [password] due to authorization failure."}],
    })
    res = fetch_and_store_logs(client, store)
    assert res.ok is True
    assert res.file_transfer_count == 0
    assert res.connection_count == 1
    assert res.file_transfer_seems_disabled is True
    assert "파일 전송" in res.message


def test_fetch_and_store_no_disabled_flag_when_both_have_data(tmp_path):
    store = NasLogStore(tmp_path / "n.db")
    client = _FakeDsmClient(by_logtype={
        "file_transfer": [{"time": 1, "who": "anycall",
                           "descr": "User [anycall] from [1.1.1.1] via [WebDAV] downloaded file [/x.zip]"}],
        "connection":    [{"time": 2, "who": "anycall",
                           "descr": "User [anycall] signed in to [DSM] from [1.1.1.1]"}],
    })
    res = fetch_and_store_logs(client, store)
    assert res.ok is True
    assert res.file_transfer_count == 1
    assert res.connection_count == 1
    assert res.file_transfer_seems_disabled is False


def test_parse_slash_separated_timestamp():
    """DSM 의 '2026/05/16 01:10:43' 형식이 KST 로 잘 변환되어 logged_at 에 들어간다."""
    e = _parse_entry({
        "who": "anycall", "descr": "ok",
        "logtype": "Connection",
        "time": "2026/05/16 01:10:43",
    })
    # 자정 직후 KST 라 날짜는 그대로
    assert e.logged_at.startswith("2026-05-16T")


def test_fetch_and_store_dumps_unknown_when_many_other(tmp_path):
    """전부 'other' 로 분류되면 샘플을 떠 둔다."""
    store = NasLogStore(tmp_path / "n.db")
    dump = tmp_path / "dumps"

    class _ManyUnknown:
        def list_audit_logs(self, logtype, *, start_epoch=None, limit=1000):
            # 10건 이상 'other' 가 되어야 덤프함
            return [{"time": i, "descr": f"weird-message-{i}"} for i in range(15)] if logtype == "file_transfer" else []
        def list_group_members(self, group_name):
            return []

    res = fetch_and_store_logs(
        _ManyUnknown(), store,
        dsm_group_name="g", dump_dir=str(dump),
    )
    assert res.ok is True
    assert res.other_count >= 10
    assert res.other_sample_path and Path(res.other_sample_path).exists()


# ---------- fetch_and_store_logs (mocked DSM) ----------

class _FakeDsmClient:
    """raise_on 의 값은 logtype 의 '카테고리' — fetch 코드의 변종(`file_transfer`,
    `FileTransfer`, `transfer`, `file` / `connection`, `Connection`, `conn`)을 모두
    포함하도록 자동 확장한다. 그래야 '전부 실패' 테스트가 변종 폴백을 우회한다."""

    _ALIASES = {
        "file_transfer": ("file_transfer", "FileTransfer", "transfer", "file"),
        "connection":    ("connection", "Connection", "conn"),
    }

    def __init__(self, by_logtype: dict, raise_on: list[str] | None = None):
        self.by_logtype = by_logtype
        self.raise_on: set[str] = set()
        for r in (raise_on or []):
            self.raise_on.update(self._ALIASES.get(r, (r,)))
        self.calls: list[tuple[str, int | None]] = []

    def list_audit_logs(self, logtype: str, *, start_epoch: int | None = None, limit: int = 1000):
        self.calls.append((logtype, start_epoch))
        if logtype in self.raise_on:
            raise DsmAuthError(f"{logtype} 거부", code=400)
        # 데이터도 별칭 단위로 묶어서 조회 (테스트 픽스처는 표준 이름만 쓰면 됨).
        if logtype in self.by_logtype:
            return list(self.by_logtype[logtype])
        for canonical, aliases in self._ALIASES.items():
            if logtype in aliases and canonical in self.by_logtype:
                return list(self.by_logtype[canonical])
        return []


def _ft_sample(time: int, user: str = "anycall", action: str = "downloaded",
               path: str = "/photo/a.mp3"):
    return {
        "time": time,
        "descr": f"User [{user}] from [10.0.0.1] via [WebDAV] {action} file [{path}]",
    }


def test_fetch_and_store_inserts_and_updates_meta(tmp_path):
    store = NasLogStore(tmp_path / "n.db")
    client = _FakeDsmClient({
        "file_transfer": [_ft_sample(1715500000), _ft_sample(1715500100, user="kim", action="deleted")],
        "connection": [{
            "time": 1715500200,
            "descr": "User [anycall] logged in from [10.0.0.1]",
        }],
    })
    res = fetch_and_store_logs(client, store)
    assert res.ok is True
    assert res.added == 3 and res.skipped == 0
    assert store.count() == 3
    assert store.latest_epoch() == 1715500200
    ok, msg, _ = store.last_status()
    assert ok is True and "3건" in msg


def test_fetch_and_store_partial_failure_keeps_other(tmp_path):
    """연결 로그가 실패해도 파일 전송 로그는 저장되어야 함."""
    store = NasLogStore(tmp_path / "n.db")
    client = _FakeDsmClient(
        by_logtype={"file_transfer": [_ft_sample(1715500000)]},
        raise_on=["connection"],
    )
    res = fetch_and_store_logs(client, store)
    assert res.ok is True
    assert store.count() == 1


def test_fetch_and_store_total_failure_marks_bad_status(tmp_path):
    store = NasLogStore(tmp_path / "n.db")
    client = _FakeDsmClient(
        by_logtype={},
        raise_on=["file_transfer", "connection"],
    )
    res = fetch_and_store_logs(client, store)
    assert res.ok is False
    ok, msg, _ = store.last_status()
    assert ok is False and "실패" in msg


def test_fetch_and_store_uses_latest_epoch_as_since(tmp_path):
    store = NasLogStore(tmp_path / "n.db")
    store.set_latest_epoch(1715500000)
    client = _FakeDsmClient({"file_transfer": [], "connection": []})
    fetch_and_store_logs(client, store)
    # since_epoch 가 두 호출에 전달돼야 함
    assert all(c[1] == 1715500000 for c in client.calls)


def test_fetch_and_store_dedups_on_second_run(tmp_path):
    store = NasLogStore(tmp_path / "n.db")
    client = _FakeDsmClient({
        "file_transfer": [_ft_sample(1715500000), _ft_sample(1715500100)],
        "connection": [],
    })
    fetch_and_store_logs(client, store)
    res2 = fetch_and_store_logs(client, store)
    assert res2.added == 0
    assert res2.skipped >= 2
    assert store.count() == 2
