"""회원 목록 HTML 파서 — 합성 픽스처로 회귀 보호.

사이트 cl_level select 실제 값 (data/dumps/ 로 확정):
    0 손님 / 1 탈퇴 / 2 거부 / 3 대기 / 4 준회원
    5 일반회원 / 6 우수회원 / 7 최우수회원 / 8 명예회원 / 9 동호회관리자
"""
from __future__ import annotations


SAMPLE_HTML = """
<form id="fmemberlist" name="fmemberlist" method="post">
  <table>
    <tbody>
      <tr class="bg0 text-center">
        <td class="td_num"><a href="member.answer.php?cl=green&mb_id=hong">hong</a></td>
        <td>홍길동</td>
        <td>홍이</td>
        <td>
          <select name="cl_level[hong]">
            <option value="4">준회원</option>
            <option value="5" selected>일반 회원</option>
            <option value="6">우수 회원</option>
          </select>
        </td>
        <td>26-04-07</td>
        <td>20-01-15</td>
        <td>123</td>
      </tr>
      <tr class="bg1 text-center">
        <td class="td_num"><a href="member.answer.php?cl=green&mb_id=lee">lee</a></td>
        <td>이영희</td>
        <td>이쁨이</td>
        <td>
          <select name="cl_level[lee]">
            <option value="4" selected>준회원</option>
            <option value="5">일반 회원</option>
          </select>
        </td>
        <td>26-05-01</td>
        <td>26-04-20</td>
        <td>5</td>
      </tr>
    </tbody>
  </table>
</form>
"""


def test_parser_extracts_two_members():
    from core.member_parser import MemberListParser
    parser = MemberListParser()
    members = parser.parse(SAMPLE_HTML)
    assert len(members) == 2
    assert members[0].user_id == "hong"
    assert members[1].user_id == "lee"


def test_parser_extracts_levels():
    from core.member_parser import MemberListParser
    members = MemberListParser().parse(SAMPLE_HTML)
    by_id = {m.user_id: m for m in members}
    assert by_id["hong"].level == 5  # 일반회원
    assert by_id["lee"].level == 4   # 준회원


def test_parser_extracts_names_and_nicknames():
    from core.member_parser import MemberListParser
    members = MemberListParser().parse(SAMPLE_HTML)
    by_id = {m.user_id: m for m in members}
    assert by_id["hong"].name == "홍길동"
    assert by_id["hong"].nickname == "홍이"
    assert by_id["lee"].name == "이영희"


def test_parser_extracts_dates():
    from core.member_parser import MemberListParser
    members = MemberListParser().parse(SAMPLE_HTML)
    by_id = {m.user_id: m for m in members}
    h = by_id["hong"]
    assert h.last_login_date is not None
    assert h.last_login_date.year == 2026
    assert h.last_login_date.month == 4
    assert h.last_login_date.day == 7


def test_parser_handles_empty_html():
    from core.member_parser import MemberListParser
    assert MemberListParser().parse("") == []
    assert MemberListParser().parse("<html></html>") == []


# ---- 동호회관리자(레벨 9) — 사이트 cl_level select 실제 옵션 ----

ADMIN_ROW_HTML = """
<form id="fmemberlist" name="fmemberlist" method="post">
  <table>
    <tbody>
      <tr class="bg0 text-center">
        <td class="td_num"><a href="member.answer.php?cl=green&mb_id=admin1">admin1</a></td>
        <td>운영자</td>
        <td>매니저</td>
        <td>
          <select name="cl_level[admin1]">
            <option value="4">준회원</option>
            <option value="5">일반 회원</option>
            <option value="6">우수 회원</option>
            <option value="7">최우수 회원</option>
            <option value="8">명예 회원</option>
            <option value="9" selected>동호회관리자</option>
          </select>
        </td>
        <td>26-05-08</td>
        <td>23-01-01</td>
        <td>9999</td>
      </tr>
    </tbody>
  </table>
</form>
"""


def test_parser_recognizes_admin_level_9():
    """사이트 cl_level select 의 동호회관리자(9) 옵션을 정확히 추출."""
    from core.member_parser import MemberListParser
    members = MemberListParser().parse(ADMIN_ROW_HTML)
    assert len(members) == 1
    m = members[0]
    assert m.user_id == "admin1"
    assert m.level == 9
    assert "동호회관리자" in m.level_label


# 컬럼 순서가 달라져 cl_level select 가 다른 칸에 들어간 경우 — 그래도 회수.
SHIFTED_COL_HTML = """
<form id="fmemberlist" name="fmemberlist" method="post">
  <table>
    <tbody>
      <tr class="bg0 text-center">
        <td><a href="member.answer.php?cl=green&mb_id=park">park</a></td>
        <td>박씨</td>
        <td>박이</td>
        <td>관리자 표시 칸</td>
        <td>
          <select name="cl_level[park]">
            <option value="5">일반 회원</option>
            <option value="6" selected>우수 회원</option>
          </select>
        </td>
        <td>26-04-07</td>
        <td>22-12-01</td>
        <td>50</td>
      </tr>
    </tbody>
  </table>
</form>
"""


def test_parser_finds_cl_level_select_anywhere_in_row():
    """COL_LEVEL(=3) 위치에 다른 텍스트가 들어가도 cl_level[mb_id] select 를 행 전체에서 찾아 정확히 6 으로 추출."""
    from core.member_parser import MemberListParser
    members = MemberListParser().parse(SHIFTED_COL_HTML)
    assert len(members) == 1
    assert members[0].user_id == "park"
    assert members[0].level == 6
    assert "우수회원" in members[0].level_label.replace(" ", "")


def test_level_text_map_contains_admin():
    from config import LEVEL_LABELS, LEVEL_TEXT_MAP
    assert LEVEL_LABELS[9] == "동호회관리자"
    assert LEVEL_TEXT_MAP["동호회관리자"] == 9


def test_admin_excluded_from_mvp():
    from config import MVP_EXCLUDED_LEVELS
    # 동호회관리자(9) + 명예회원(8) — 둘 다 MVP 제외
    assert 9 in MVP_EXCLUDED_LEVELS
    assert 8 in MVP_EXCLUDED_LEVELS


# ---- 명시적 cl_admin 마커 감지 (cl_level 과 별개) ----
# 사이트가 동호회관리자를 cl_admin 체크박스/셀렉트로 별도 표기하는 경우의 회귀.

ADMIN_VIA_CHECKBOX_HTML = """
<form id="fmemberlist" name="fmemberlist" method="post">
  <table>
    <tbody>
      <tr class="bg0 text-center">
        <td><a href="member.answer.php?cl=green&mb_id=mgr1">mgr1</a></td>
        <td>운영자A</td>
        <td>매니저A</td>
        <td>
          <select name="cl_level[mgr1]">
            <option value="7">최우수 회원</option>
            <option value="8" selected>명예 회원</option>
          </select>
        </td>
        <td>
          <input type="checkbox" name="cl_admin[mgr1]" value="1" checked>
        </td>
        <td>26-05-08</td>
        <td>23-01-01</td>
        <td>500</td>
      </tr>
      <tr class="bg1 text-center">
        <td><a href="member.answer.php?cl=green&mb_id=hong2">hong2</a></td>
        <td>홍길동</td>
        <td>홍이</td>
        <td>
          <select name="cl_level[hong2]">
            <option value="7">최우수 회원</option>
            <option value="8" selected>명예 회원</option>
          </select>
        </td>
        <td>
          <input type="checkbox" name="cl_admin[hong2]" value="1">
        </td>
        <td>26-05-08</td>
        <td>23-01-01</td>
        <td>500</td>
      </tr>
    </tbody>
  </table>
</form>
"""


def test_parser_detects_admin_via_checkbox():
    """cl_admin 체크박스가 checked 인 경우 is_admin=True. 같은 cl_level 8 라도 일반 명예회원과 구분."""
    from core.member_parser import MemberListParser
    members = MemberListParser().parse(ADMIN_VIA_CHECKBOX_HTML)
    by_id = {m.user_id: m for m in members}
    assert by_id["mgr1"].is_admin is True
    assert by_id["mgr1"].level == 8   # cl_level 자체는 그대로
    assert by_id["hong2"].is_admin is False
    assert by_id["hong2"].level == 8


# 사이트가 cl_admin select 로 표현하는 경우
ADMIN_VIA_SELECT_HTML = """
<form id="fmemberlist" name="fmemberlist" method="post">
  <table>
    <tbody>
      <tr>
        <td><a href="member.answer.php?cl=green&mb_id=mgr2">mgr2</a></td>
        <td>운영자B</td>
        <td>매니저B</td>
        <td>
          <select name="cl_level[mgr2]">
            <option value="8" selected>명예 회원</option>
          </select>
        </td>
        <td>
          <select name="cl_admin[mgr2]">
            <option value="0">일반</option>
            <option value="1" selected>관리자</option>
          </select>
        </td>
        <td>26-05-08</td>
        <td>23-01-01</td>
        <td>500</td>
      </tr>
    </tbody>
  </table>
</form>
"""


def test_parser_detects_admin_via_select():
    from core.member_parser import MemberListParser
    members = MemberListParser().parse(ADMIN_VIA_SELECT_HTML)
    assert len(members) == 1
    assert members[0].is_admin is True


# cl_level 옵션의 'selected' 가 동호회관리자(9) 인 경우
ADMIN_VIA_LEVEL_LABEL_HTML = """
<form id="fmemberlist" name="fmemberlist" method="post">
  <table>
    <tbody>
      <tr>
        <td><a href="member.answer.php?cl=green&mb_id=mgr3">mgr3</a></td>
        <td>운영자C</td>
        <td>매니저C</td>
        <td>
          <select name="cl_level[mgr3]">
            <option value="8">명예 회원</option>
            <option value="9" selected>동호회관리자</option>
          </select>
        </td>
        <td>26-05-08</td>
        <td>23-01-01</td>
        <td>900</td>
      </tr>
    </tbody>
  </table>
</form>
"""


def test_parser_detects_admin_via_level_text():
    from core.member_parser import MemberListParser
    members = MemberListParser().parse(ADMIN_VIA_LEVEL_LABEL_HTML)
    assert len(members) == 1
    assert members[0].is_admin is True
    assert members[0].level == 9


def test_admin_excluded_from_promotion_candidates():
    """is_admin=True 회원은 자동 승급 후보에서 제외 (Member 에 attribute 존재 확인)."""
    from core.models import Member
    m = Member(user_id="x", level=5, is_admin=True)
    assert m.is_admin is True
    assert hasattr(m, "is_admin")


# ---- 보수적 admin 감지 회귀 — 텍스트 매칭 오탐 방지 ----

NOT_ADMIN_HONEY_HTML = """
<form id="fmemberlist" name="fmemberlist" method="post">
  <table>
    <tbody>
      <tr>
        <td><a href="member.answer.php?cl=green&mb_id=hey">hey</a></td>
        <td>홍길동</td>
        <td>홍이</td>
        <td>
          <select name="cl_level[hey]">
            <option value="8" selected>명예 회원</option>
            <option value="9">동호회관리자</option>
          </select>
        </td>
        <td><span>관리자</span></td>
        <td>26-05-08</td>
        <td>23-01-01</td>
        <td>500</td>
      </tr>
    </tbody>
  </table>
</form>
"""


def test_no_admin_false_positive_from_text():
    """선택된 cl_level=8(명예회원) + 다른 셀에 '관리자' 텍스트가 있어도
    명시적 cl_admin 필드가 없으면 is_admin=False (텍스트 매칭 오탐 방지)."""
    from core.member_parser import MemberListParser
    members = MemberListParser().parse(NOT_ADMIN_HONEY_HTML)
    assert len(members) == 1
    m = members[0]
    assert m.level == 8
    assert m.is_admin is False


NOT_ADMIN_OPTION_TEXT_HTML = """
<form id="fmemberlist" name="fmemberlist" method="post">
  <table>
    <tbody>
      <tr>
        <td><a href="member.answer.php?cl=green&mb_id=foo">foo</a></td>
        <td>이름</td>
        <td>닉</td>
        <td>
          <select name="cl_level[foo]">
            <option value="4">준회원</option>
            <option value="5" selected>일반 회원</option>
            <option value="6">우수 회원</option>
            <option value="7">최우수 회원</option>
            <option value="8">명예 회원</option>
            <option value="9">동호회관리자</option>
          </select>
        </td>
        <td>26-05-08</td>
        <td>23-01-01</td>
        <td>10</td>
      </tr>
    </tbody>
  </table>
</form>
"""


def test_no_admin_when_dropdown_lists_admin_option_but_not_selected():
    """cl_level 드롭다운에 동호회관리자 옵션이 있어도 selected 가 다른 값이면
    is_admin=False — 옵션 텍스트만으로 admin 판정하지 않음."""
    from core.member_parser import MemberListParser
    members = MemberListParser().parse(NOT_ADMIN_OPTION_TEXT_HTML)
    assert len(members) == 1
    m = members[0]
    assert m.level == 5  # 일반회원
    assert m.is_admin is False
