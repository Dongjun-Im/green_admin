"""게시판 관리 폼 파싱 + 공지 작성 페이로드 단위 테스트.

실제 소리샘 호출은 하지 않고, 가짜 세션(get/post 캡처)으로 검증.
"""
from __future__ import annotations

import pytest

from core import board_admin
from core.board_admin import (
    BoardAdminError,
    build_submit_payload,
    delete_posts,
    fetch_board_form,
    fetch_board_list_html,
    fetch_post_list,
    move_posts,
    post_notice_to_boards,
    submit_board_form,
    write_post,
    _parse_form,
    _parse_post_list,
)


# ---------- 가짜 세션 ----------

class _FakeResp:
    def __init__(self, status: int = 200, text: str = "") -> None:
        self.status_code = status
        self.ok = 200 <= status < 300
        self.text = text


class _FakeSession:
    def __init__(self, get_text: str = "", post_text: str = "", post_map: dict | None = None,
                 get_texts: list | None = None) -> None:
        self._get_text = get_text
        self._get_texts = list(get_texts) if get_texts else None  # 순서대로 반환 (마지막은 반복)
        self._get_i = 0
        self._post_text = post_text
        self._post_map = post_map or {}      # url 부분문자열 -> 응답 본문
        self.captured: dict = {}
        self.post_calls: list = []           # [(url, data), ...]

    def get(self, url, **kw):
        self.captured["get_url"] = url
        self.captured["get_params"] = kw.get("params")
        if self._get_texts:
            t = self._get_texts[min(self._get_i, len(self._get_texts) - 1)]
            self._get_i += 1
            return _FakeResp(200, t)
        return _FakeResp(200, self._get_text)

    def post(self, url, **kw):
        self.captured["post_url"] = url
        self.captured["post_data"] = kw.get("data")
        self.post_calls.append((url, kw.get("data")))
        text = self._post_text
        for sub, body in self._post_map.items():
            if sub in url:
                text = body
                break
        return _FakeResp(200, text)


# ---------- 샘플 HTML ----------

_BOARD_FORM_HTML = """
<html><body>
<form name="fboardform" method="post" action="adm.board_form_update.php">
  <input type="hidden" name="w" value="u">
  <input type="hidden" name="bo_table" value="green3">
  <input type="hidden" name="token" value="abc123">
  <table>
    <tr><th>게시판 제목</th><td><input type="text" name="bo_subject" value="우리들의 이야기" maxlength="255"></td></tr>
    <tr><th>분류 사용</th><td><input type="checkbox" name="bo_use_category" value="1" checked></td></tr>
    <tr><th>스킨</th><td>
      <select name="bo_skin">
        <option value="basic">basic</option>
        <option value="gallery" selected>gallery</option>
      </select></td></tr>
    <tr><th>댓글 권한</th><td>
      <input type="radio" name="bo_comment_level" value="1" id="cl1"><label for="cl1">전체</label>
      <input type="radio" name="bo_comment_level" value="2" id="cl2" checked><label for="cl2">회원</label>
    </td></tr>
    <tr><th>상단 내용</th><td><textarea name="bo_content_head">머리말</textarea></td></tr>
  </table>
  <input type="submit" value="확인">
</form>
</body></html>
"""

_WRITE_FORM_HTML = """
<html><body>
<form id="fwrite" name="fwrite" method="post" action="/bbs/write_update.php" enctype="multipart/form-data">
  <input type="hidden" name="w" value="">
  <input type="hidden" name="bo_table" value="green3">
  <input type="hidden" name="wr_id" value="0">
  <input type="hidden" name="token" value="wtok">
  <input type="hidden" name="uid" value="uid123">
  <input type="text" name="wr_subject" value="">
  <textarea name="wr_content"></textarea>
  <input type="checkbox" name="notice" value="1">
  <input type="checkbox" name="secret" value="secret">
  <input type="checkbox" name="mail" value="mail">
  <input type="submit" value="작성완료">
</form>
</body></html>
"""


# ---------- _parse_form ----------

def test_parse_form_extracts_fields():
    form = _parse_form(_BOARD_FORM_HTML, base_url="https://x/skin/board/ar.common/adm.board_form.php?bo_table=green3", bo_table="green3")
    assert form.bo_table == "green3"
    assert form.method == "POST"
    assert form.action_url.endswith("/skin/board/ar.common/adm.board_form_update.php")
    by_name = {f.name: f for f in form.fields}
    # hidden
    assert by_name["w"].kind == "hidden" and by_name["w"].value == "u"
    assert by_name["bo_table"].value == "green3"
    assert by_name["token"].value == "abc123"
    # text
    f = by_name["bo_subject"]
    assert f.kind == "text" and f.value == "우리들의 이야기" and f.label == "게시판 제목" and f.maxlength == 255
    # checkbox
    f = by_name["bo_use_category"]
    assert f.kind == "checkbox" and f.checked is True and f.value == "1" and f.label == "분류 사용"
    # select
    f = by_name["bo_skin"]
    assert f.kind == "select" and f.value == "gallery"
    assert ("basic", "basic") in f.options and ("gallery", "gallery") in f.options
    # radio (그룹으로 하나)
    f = by_name["bo_comment_level"]
    assert f.kind == "radio" and f.value == "2"
    assert [v for v, _t in f.options] == ["1", "2"]
    assert [t for _v, t in f.options] == ["전체", "회원"]
    # textarea
    f = by_name["bo_content_head"]
    assert f.kind == "textarea" and f.value == "머리말"


def test_parse_form_no_form_raises():
    with pytest.raises(BoardAdminError):
        _parse_form("<html><body>no form here</body></html>", base_url="https://x/", bo_table="green3")


# ---------- build_submit_payload ----------

def test_build_submit_payload_applies_overrides_and_checkbox_rules():
    form = _parse_form(_BOARD_FORM_HTML, base_url="https://x/a/adm.board_form.php?bo_table=green3", bo_table="green3")
    payload = build_submit_payload(form, {"bo_subject": "새 제목", "bo_use_category": False})
    d = dict(payload)
    # hidden 보존
    assert d["w"] == "u" and d["bo_table"] == "green3" and d["token"] == "abc123"
    # 텍스트 override
    assert d["bo_subject"] == "새 제목"
    # 체크박스 해제 → payload 에 없음
    assert "bo_use_category" not in d
    # select / radio 현재값
    assert d["bo_skin"] == "gallery"
    assert d["bo_comment_level"] == "2"
    # textarea 현재값
    assert d["bo_content_head"] == "머리말"


def test_build_submit_payload_checkbox_checked_included():
    form = _parse_form(_BOARD_FORM_HTML, base_url="https://x/a/adm.board_form.php", bo_table="green3")
    d = dict(build_submit_payload(form, {"bo_use_category": True}))
    assert d["bo_use_category"] == "1"


# ---------- fetch_board_form / submit_board_form ----------

def test_fetch_board_form_uses_session_get():
    sess = _FakeSession(get_text=_BOARD_FORM_HTML)
    form = fetch_board_form(sess, "green3")
    assert "adm.board_form.php?bo_table=green3" in sess.captured["get_url"]
    assert form.bo_table == "green3"


def test_fetch_board_form_empty_id_raises():
    sess = _FakeSession(get_text=_BOARD_FORM_HTML)
    with pytest.raises(BoardAdminError):
        fetch_board_form(sess, "  ")


def test_submit_board_form_posts_payload_and_reports_success():
    form = _parse_form(_BOARD_FORM_HTML, base_url="https://x/a/adm.board_form.php?bo_table=green3", bo_table="green3")
    sess = _FakeSession(post_text="<html>정상적으로 처리되었습니다</html>")
    res = submit_board_form(sess, form, {"bo_subject": "바뀐 제목"})
    assert res.ok is True
    # post 로 보낸 데이터에 override 가 들어갔는지 (data 는 [(k,v),...])
    d = dict(sess.captured["post_data"])
    assert d["bo_subject"] == "바뀐 제목"
    assert sess.captured["post_url"].endswith("adm.board_form_update.php")


def test_submit_board_form_detects_permission_error():
    form = _parse_form(_BOARD_FORM_HTML, base_url="https://x/a/adm.board_form.php", bo_table="green3")
    sess = _FakeSession(post_text="<html>권한이 없습니다</html>")
    res = submit_board_form(sess, form, {})
    assert res.ok is False


# ---------- write_post / post_notice_to_boards ----------

def test_write_post_scrapes_then_posts_with_notice():
    sess = _FakeSession(get_text=_WRITE_FORM_HTML, post_text="<html>글이 등록되었습니다</html>")
    res = write_post(sess, "green3", "공지 제목", "공지 본문", as_notice=True)
    assert res.ok is True
    assert "공지" in res.message
    d = sess.captured["post_data"]
    assert d["bo_table"] == "green3"
    assert d["wr_subject"] == "공지 제목"
    assert d["wr_content"] == "공지 본문"
    assert d["notice"] == "1"          # 공지 체크
    assert d["w"] == "" and d["wr_id"] == "0"
    # 스크랩한 숨김 토큰 보존
    assert d["token"] == "wtok" and d["uid"] == "uid123"
    # write_update 로 POST
    assert sess.captured["post_url"].endswith("/bbs/write_update.php")


def test_write_post_without_notice_omits_notice_field():
    sess = _FakeSession(get_text=_WRITE_FORM_HTML, post_text="<html>ok</html>")
    res = write_post(sess, "green3", "일반 글", "본문", as_notice=False)
    assert res.ok is True
    assert "notice" not in sess.captured["post_data"]


def test_write_post_empty_subject_fails_without_network():
    sess = _FakeSession()
    res = write_post(sess, "green3", "   ", "본문")
    assert res.ok is False
    assert "get_url" not in sess.captured  # 네트워크 호출 안 함


def test_write_post_detects_site_rejection():
    sess = _FakeSession(get_text=_WRITE_FORM_HTML, post_text="<html>제목을 입력하세요</html>")
    res = write_post(sess, "green3", "제목", "본문")
    assert res.ok is False


def test_post_notice_to_boards_calls_each(monkeypatch):
    calls: list[str] = []

    def fake_write_post(session, bo_table, subject, content, **kw):
        calls.append(bo_table)
        from core.board_admin import PostResult
        return PostResult(bo_table=bo_table, ok=True, message="작성 완료")

    monkeypatch.setattr(board_admin, "write_post", fake_write_post)
    progress: list[tuple[int, int]] = []
    res = post_notice_to_boards(
        object(), ["green3", "green9", "green3"],  # 중복은 그대로? — 함수는 strip만, dedup 안 함
        "제목", "본문", progress_cb=lambda c, t: progress.append((c, t)),
    )
    # 중복 제거 안 함 — 입력 순서대로
    assert calls == ["green3", "green9", "green3"]
    assert len(res) == 3
    assert progress[-1] == (3, 3)


# ---------- 게시물 목록 파싱 ----------

_BOARD_LIST_HTML = """
<html><body>
<form name="fboardlist" id="fboardlist" action="./board_list_update.php" onsubmit="return fboardlist_submit(this);" method="post">
  <input type="hidden" name="bo_table" value="green3">
  <input type="hidden" name="sca" value="">
  <input type="hidden" name="sfl" value="">
  <input type="hidden" name="stx" value="">
  <input type="hidden" name="spt" value="">
  <input type="hidden" name="page" value="2">
  <input type="hidden" name="sw" value="">
  <table>
    <tr class="bo_notice">
      <td class="td_chk"><input type="checkbox" name="chk_wr_id[]" value="100" id="chk_wr_id_0"></td>
      <td>공지</td>
      <td class="td_subject"><a href="/bbs/board.php?bo_table=green3&amp;wr_id=100&amp;page=2" title="긴 미리보기">[공지] 게시판 이용 안내</a></td>
      <td class="td_name">관리자</td><td class="td_date">2026-01-01</td>
    </tr>
    <tr class="">
      <td class="td_chk"><input type="checkbox" name="chk_wr_id[]" value="55" id="chk_wr_id_1"></td>
      <td>55</td>
      <td class="td_subject"><a href="/bbs/board.php?bo_table=green3&amp;wr_id=55&amp;page=2">안녕하세요 가입인사</a>
        <span class="sv_writer">홍길동</span><span class="sv_date">26-05-09</span></td>
      <td class="td_name">홍길동</td><td class="td_date">26-05-09</td>
    </tr>
    <tr class="">
      <td class="td_chk"><input type="checkbox" name="chk_wr_id[]" value="54" id="chk_wr_id_2"></td>
      <td>54</td>
      <td class="td_subject"><a href="/bbs/board.php?bo_table=green3&amp;wr_id=54&amp;page=2">질문 있습니다 <span class="sound_only">댓글</span><span class="cnt_cmt">2</span><span class="sound_only">개</span></a></td>
      <td class="td_name">김철수</td><td class="td_date">26-05-08</td>
    </tr>
  </table>
</form>
</body></html>
"""

_MOVE_FORM_HTML = """
<html><body>
<form name="fmove" id="fmove" action="./move_update.php" method="post">
  <input type="hidden" name="sw" value="move">
  <input type="hidden" name="bo_table" value="green3">
  <input type="hidden" name="wr_ids" value="55,54">
  <select name="to_bo_table">
    <option value="green1">공지사항</option>
    <option value="green9">질문게시판</option>
  </select>
  <input type="submit" value="확인">
</form>
</body></html>
"""

# 소리샘(ar.basic) 의 실제 move.php 팝업 폼 형식: 대상 게시판은 chk_bo_table[] 체크박스,
# wr_id_list(콤마결합)·act(한글 verb)·url 등 hidden 보존, <select> 없음.
_AR_MOVE_FORM_HTML = """
<html><head><title>게시물 복사</title></head><body>
<div id="copymove" class="new_win">
<h2 id="win_title">게시물 복사</h2>
<form name="fboardmoveall" method="post" action="./move_update.php" onsubmit="return fboardmoveall_submit(this);">
  <input type="hidden" name="sw" value="copy">
  <input type="hidden" name="bo_table" value="green3">
  <input type="hidden" name="wr_id_list" value="633870,633726">
  <input type="hidden" name="sfl" value="">
  <input type="hidden" name="stx" value="">
  <input type="hidden" name="spt" value="0">
  <input type="hidden" name="sst" value="">
  <input type="hidden" name="sod" value="">
  <input type="hidden" name="page" value="1">
  <input type="hidden" name="act" value="복사">
  <input type="hidden" name="url" value="https://www.sorisem.net/bbs/board.php?bo_table=green3&amp;page=1">
  <table id="board_table"><thead><tr><th><input type="checkbox" id="chkall"></th><th>게시판</th></tr></thead><tbody></tbody></table>
  <input type="submit" value="복사" id="btn_submit" class="btn_submit">
</form>
</div>
</body></html>
"""


def test_parse_post_list_extracts_items():
    res = _parse_post_list(_BOARD_LIST_HTML, "green3", 2)
    assert res.bo_table == "green3" and res.page == 2
    assert [i.wr_id for i in res.items] == ["100", "55", "54"]
    assert res.items[0].subject == "[공지] 게시판 이용 안내" and res.items[0].is_notice is True
    assert res.items[1].subject == "안녕하세요 가입인사" and res.items[1].author == "홍길동"
    assert res.items[1].is_notice is False
    # 제목 옆 댓글수 표시(span.sound_only/cnt_cmt)는 제외하고 제목만
    assert res.items[2].subject == "질문 있습니다" and res.items[2].author == "김철수"
    # 목록 폼 hidden 필드 보존 (chk_wr_id[] 제외)
    assert res.list_form["bo_table"] == "green3"
    assert res.list_form["page"] == "2"
    assert "chk_wr_id[]" not in res.list_form
    # 폼 action (선택삭제 POST 대상) 해석됨
    assert res.list_action_url.endswith("/bbs/board_list_update.php")


def test_parse_post_list_no_checkbox_raises():
    html = "<html><body><table><tr><td>글 제목</td></tr></table></body></html>"
    with pytest.raises(BoardAdminError):
        _parse_post_list(html, "green3", 1)


def test_fetch_post_list_uses_session_get():
    sess = _FakeSession(get_text=_BOARD_LIST_HTML)
    res = fetch_post_list(sess, "green3", 2)
    assert "board.php?bo_table=green3" in sess.captured["get_url"]
    assert "page=2" in sess.captured["get_url"]
    assert len(res.items) == 3


def test_fetch_post_list_empty_id_raises():
    with pytest.raises(BoardAdminError):
        fetch_post_list(_FakeSession(get_text=_BOARD_LIST_HTML), "  ", 1)


# ---------- 게시물 삭제 ----------

def test_delete_posts_posts_chk_array_and_succeeds():
    sess = _FakeSession(post_text="<html>1개의 자료를 삭제하였습니다.</html>")
    res = delete_posts(sess, "green3", ["55", "54"],
                       list_form={"bo_table": "green3", "page": "2", "sw": ""})
    assert res.ok is True and res.action == "delete" and res.count == 2
    # list_action_url 안 주면 기본 board_list_update.php 로
    assert sess.captured["post_url"].endswith("/bbs/board_list_update.php")
    data = sess.captured["post_data"]              # [(k, v), ...]
    assert ("chk_wr_id[]", "55") in data and ("chk_wr_id[]", "54") in data
    assert ("bo_table", "green3") in data
    assert ("page", "2") in data
    assert ("btn_submit", "선택삭제") in data       # board_list_update.php 동작 분기 키
    assert ("sw", "") in data


def test_delete_posts_uses_scraped_action_url():
    sess = _FakeSession(post_text="<html>처리되었습니다</html>")
    res = delete_posts(sess, "green3", ["55"],
                       list_form={"bo_table": "green3", "page": "1"},
                       list_action_url="https://www.sorisem.net/bbs/board_list_update.php")
    assert res.ok is True
    assert sess.captured["post_url"] == "https://www.sorisem.net/bbs/board_list_update.php"
    assert ("btn_submit", "선택삭제") in sess.captured["post_data"]


def test_delete_posts_detects_rejection():
    sess = _FakeSession(post_text="<html>권한이 없습니다.</html>")
    res = delete_posts(sess, "green3", ["55"])
    assert res.ok is False


def test_delete_posts_empty_selection_no_network():
    sess = _FakeSession()
    res = delete_posts(sess, "green3", [])
    assert res.ok is False
    assert not sess.post_calls and "post_url" not in sess.captured


# ---------- 게시물 이동/복사 ----------

def test_move_posts_two_step_move():
    sess = _FakeSession(post_map={
        "move_update.php": "<html>게시물을 이동하였습니다.</html>",
        "/move.php": _MOVE_FORM_HTML,
    })
    res = move_posts(sess, "green3", ["55", "54"], "green9", copy=False,
                     list_form={"bo_table": "green3"})
    assert res.ok is True and res.action == "move"
    assert res.target_bo_table == "green9" and res.count == 2
    # 1단계: move.php 로 chk_wr_id[] 배열 + sw=move
    first_url, first_data = sess.post_calls[0]
    assert "/move.php" in first_url and "move_update" not in first_url
    assert ("chk_wr_id[]", "55") in first_data and ("chk_wr_id[]", "54") in first_data
    assert ("sw", "move") in first_data
    # 2단계: move_update.php 로 to_bo_table 채워서
    last_url, last_data = sess.post_calls[-1]
    assert "move_update.php" in last_url
    d = dict(last_data) if isinstance(last_data, list) else last_data
    assert d["to_bo_table"] == "green9"
    assert d["sw"] == "move"
    assert d["wr_ids"] == "55,54"


def test_move_posts_copy_flag():
    sess = _FakeSession(post_map={
        "move_update.php": "<html>게시물을 복사하였습니다.</html>",
        "/move.php": _MOVE_FORM_HTML.replace('name="sw" value="move"', 'name="sw" value="copy"'),
    })
    res = move_posts(sess, "green3", ["55"], "green9", copy=True)
    assert res.ok is True and res.action == "copy"
    d = dict(sess.post_calls[-1][1]) if isinstance(sess.post_calls[-1][1], list) else sess.post_calls[-1][1]
    assert d["sw"] == "copy" and d["to_bo_table"] == "green9"


def test_move_posts_rejects_disallowed_target():
    sess = _FakeSession(post_map={"/move.php": _MOVE_FORM_HTML})
    res = move_posts(sess, "green3", ["55"], "green2", copy=False)  # green2 는 후보에 없음
    assert res.ok is False
    assert "green2" in res.message
    # move_update.php 로는 POST 하지 않음
    assert all("move_update.php" not in u for u, _ in sess.post_calls)


def test_move_posts_same_board_rejected():
    sess = _FakeSession()
    res = move_posts(sess, "green3", ["55"], "green3")
    assert res.ok is False and not sess.post_calls


def test_move_posts_empty_target_rejected():
    sess = _FakeSession()
    res = move_posts(sess, "green3", ["55"], "  ")
    assert res.ok is False and not sess.post_calls


# ---------- 처리 검증(재조회) + 진단 ----------

def test_fetch_board_list_html_returns_raw():
    sess = _FakeSession(get_text="<html>RAW LIST</html>")
    html = fetch_board_list_html(sess, "green3", 1)
    assert html == "<html>RAW LIST</html>"
    assert "board.php?bo_table=green3" in sess.captured["get_url"]


def test_delete_posts_verified_gone_reports_confirmed():
    # 삭제 후 목록 재조회 시 그 글번호가 없으면 '확인됨' 으로 성공.
    sess = _FakeSession(get_text=_BOARD_LIST_HTML, post_text="<html>처리</html>")
    res = delete_posts(sess, "green3", ["999"], list_form={"bo_table": "green3", "page": "1"})
    assert res.ok is True
    assert "확인" in res.message


def test_delete_posts_verified_still_present_reports_failure():
    # 삭제했다는데 재조회하면 그대로 있으면 → 실패로 보고 (가짜 성공 방지).
    sess = _FakeSession(get_text=_BOARD_LIST_HTML, post_text="<html>처리되었습니다</html>")
    res = delete_posts(sess, "green3", ["55", "54"], list_form={"bo_table": "green3", "page": "1"})
    assert res.ok is False
    assert "남아" in res.message or "처리되지" in res.message


def test_delete_posts_uncertain_includes_snippet():
    # 응답에 성공/실패 신호가 전혀 없고 재조회도 안 되면 → ok=False + 응답 스니펫 포함.
    sess = _FakeSession(post_text="<html><body>hello world</body></html>")  # get_text="" → 재조회 실패
    res = delete_posts(sess, "green3", ["55"])
    assert res.ok is False
    assert res.response_snippet and "hello" in res.response_snippet


def test_move_posts_verified_still_present_reports_failure():
    sess = _FakeSession(get_text=_BOARD_LIST_HTML, post_map={
        "move_update.php": "<html>이동하였습니다</html>",
        "/move.php": _MOVE_FORM_HTML,
    })
    res = move_posts(sess, "green3", ["55"], "green9", copy=False,
                     list_form={"bo_table": "green3", "page": "1"})
    # 55 가 원본 목록(_BOARD_LIST_HTML)에 그대로 → 실패
    assert res.ok is False
    assert "남아" in res.message or "이동되지" in res.message


def test_move_posts_copy_unconfirmed_reports_failure():
    # 대상 게시판 1쪽이 처리 전후 동일(새 글 없음) + 응답에도 '복사 …되었습니다' 없음 → 실패로 보고
    sess = _FakeSession(get_text=_BOARD_LIST_HTML, post_map={
        "move_update.php": "<html><body><script>opener.location.reload();window.close();</script></body></html>",
        "/move.php": _MOVE_FORM_HTML,
    })
    res = move_posts(sess, "green3", ["55"], "green9", copy=True,
                     list_form={"bo_table": "green3", "page": "1"})
    assert res.ok is False
    assert "새 글" in res.message or "확인하지 못" in res.message
    # 진단용 응답 원본(move.php / move_update.php)이 들어 있다
    assert res.debug.get("move.php") and res.debug.get("move_update.php")


def test_move_posts_copy_confirmed_via_target_board():
    # 처리 후 대상 게시판 1쪽에 새 글번호(9001)가 보이면 → 복사 확인됨
    after_html = _BOARD_LIST_HTML.replace(
        "<table>",
        '<table>\n<tr class=""><td class="td_chk">'
        '<input type="checkbox" name="chk_wr_id[]" value="9001" id="chk_new"></td>'
        '<td>9001</td><td class="td_subject">'
        '<a href="/bbs/board.php?bo_table=green9&amp;wr_id=9001">복사된 글</a></td>'
        '<td class="td_name">관리자</td><td class="td_date">26-05-11</td></tr>',
        1,
    )
    sess = _FakeSession(get_texts=[_BOARD_LIST_HTML, after_html], post_map={
        "move_update.php": "<html>처리</html>",
        "/move.php": _MOVE_FORM_HTML,
    })
    res = move_posts(sess, "green3", ["55"], "green9", copy=True,
                     list_form={"bo_table": "green3", "page": "1"})
    assert res.ok is True
    assert "추가" in res.message


def test_move_posts_ar_skin_sends_chk_bo_table():
    # 소리샘(ar) move.php 폼: 대상 게시판은 chk_bo_table[] 로 보내고, act/wr_id_list/url 등 hidden 보존
    after_html = _BOARD_LIST_HTML.replace(
        "<table>",
        '<table>\n'
        '<tr class=""><td class="td_chk"><input type="checkbox" name="chk_wr_id[]" value="9002" id="chk_a"></td>'
        '<td>9002</td><td class="td_subject"><a href="/bbs/board.php?bo_table=green1&amp;wr_id=9002">복사됨1</a></td>'
        '<td class="td_name">관리자</td><td class="td_date">26-05-11</td></tr>\n'
        '<tr class=""><td class="td_chk"><input type="checkbox" name="chk_wr_id[]" value="9003" id="chk_b"></td>'
        '<td>9003</td><td class="td_subject"><a href="/bbs/board.php?bo_table=green1&amp;wr_id=9003">복사됨2</a></td>'
        '<td class="td_name">관리자</td><td class="td_date">26-05-11</td></tr>',
        1,
    )
    sess = _FakeSession(get_texts=[_BOARD_LIST_HTML, after_html], post_map={
        "move_update.php": "<html><script>document.location.replace('https://x/bbs/board.php?bo_table=green3&page=1');</script></html>",
        "/move.php": _AR_MOVE_FORM_HTML,
    })
    res = move_posts(sess, "green3", ["633870", "633726"], "green1", copy=True,
                     list_form={"bo_table": "green3", "page": "1", "sfl": "", "stx": ""})
    assert res.ok is True   # 대상(green1)에 새 글 2개 → 확인됨
    last_url, last_data = sess.post_calls[-1]
    assert "move_update.php" in last_url
    d = dict(last_data) if isinstance(last_data, list) else last_data
    assert d["chk_bo_table[]"] == "green1"     # ★ 핵심: 대상 게시판을 이 필드로 보냄
    assert d["act"] == "복사"
    assert d["sw"] == "copy" and d["bo_table"] == "green3"
    assert d["wr_id_list"] == "633870,633726"  # move.php 폼이 준 값 그대로 전달
    assert d["url"].endswith("bo_table=green3&page=1")


def test_move_posts_ar_skin_no_target_selected_error_detected():
    # move_update.php 가 'ar 오류안내 페이지'(대상 게시판 미선택)를 돌려주면 실패로 보고
    sess = _FakeSession(get_text=_BOARD_LIST_HTML, post_map={
        "move_update.php": ("<html><head><title>오류안내 페이지</title></head><body>"
                            "<script>alert('게시물을 복사할 게시판을 한개 이상 선택해 주십시오.');</script>"
                            "<noscript><h1>다음 항목에 오류가 있습니다.</h1></noscript></body></html>"),
        "/move.php": _AR_MOVE_FORM_HTML,
    })
    res = move_posts(sess, "green3", ["633870"], "green1", copy=True,
                     list_form={"bo_table": "green3", "page": "1"})
    assert res.ok is False
    assert res.debug.get("move.php") and res.debug.get("move_update.php")
