"""사이트 구조 진단."""
from __future__ import annotations


def test_empty_html_is_error():
    from core.site_diagnostics import diagnose_admin_member_html
    r = diagnose_admin_member_html("")
    assert r.severity == "error"
    assert any("비어" in f for f in r.findings)


def test_too_short_html():
    from core.site_diagnostics import diagnose_admin_member_html
    r = diagnose_admin_member_html("hi")
    assert r.severity == "error"


def test_permission_denied_text():
    from core.site_diagnostics import diagnose_admin_member_html
    long_text = "x" * 1000 + " 권한이 없습니다"
    r = diagnose_admin_member_html(long_text)
    assert r.severity == "error"


def test_missing_form_is_error():
    from core.site_diagnostics import diagnose_admin_member_html
    html = "<html><body>" + ("hi" * 600) + "</body></html>"
    r = diagnose_admin_member_html(html)
    assert r.severity == "error"
    assert any("fmemberlist" in f for f in r.findings)


def test_well_formed_html_is_ok():
    from core.site_diagnostics import diagnose_admin_member_html
    html = """
    <html><body>
      <form id="fmemberlist" method="post">
        <table><tbody>
          <tr><td>1</td><td>홍길동</td><td>홍이</td>
            <td><select name="cl_level[hong]">
              <option value="5">준회원</option>
              <option value="6" selected>일반회원</option>
            </select></td>
            <td>26-04-07</td><td>20-01-15</td><td>10</td>
          </tr>
        </tbody></table>
      </form>
    </body></html>
    """ + ("padding " * 100)
    r = diagnose_admin_member_html(html)
    assert r.severity == "ok"


def test_unknown_select_value_is_warning():
    from core.site_diagnostics import diagnose_admin_member_html
    html = """
    <html><body>
      <form id="fmemberlist" method="post">
        <table><tbody>
          <tr><td>1</td><td>홍</td><td>닉</td>
            <td><select name="cl_level[hong]">
              <option value="99" selected>이상한등급</option>
            </select></td>
            <td>26-04-07</td><td>20-01-15</td><td>10</td>
          </tr>
        </tbody></table>
      </form>
    </body></html>
    """ + ("padding " * 100)
    r = diagnose_admin_member_html(html)
    assert r.severity in ("warning", "error")
    assert any("99" in f for f in r.findings)
