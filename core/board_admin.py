"""소리샘 동호회 게시판 관리 + 공지 작성 어댑터.

소리샘(그누보드5 + ar.club 플러그인)의 게시판 관리 페이지는
    https://www.sorisem.net/skin/board/ar.common/adm.board_form.php?bo_table=<게시판>
형태이고, 게시판마다 같은 구조다.

이 모듈은 그 페이지의 폼을 "있는 그대로 긁어서" 표현한다 — 필드 이름·종류·현재
값·옵션을 추출해 GUI 가 동적으로 렌더하게 하고, 수정값을 다시 그 폼의 action 으로
POST 한다. 특정 필드를 하드코딩하지 않으므로 스킨이 바뀌어도 따라간다.

공지 작성은 그누보드 표준 글쓰기 흐름:
    1) GET /bbs/write.php?bo_table=<게시판>  → 폼의 숨김 토큰·기본값 스크랩
    2) POST /bbs/write_update.php  (wr_subject / wr_content / notice=1 등 덮어쓰기)
    3) 게시판으로 리다이렉트되면 성공.
"단일 공지" = 한 게시판에 1회, "일괄 공지" = 게시판 목록만큼 반복.

HTTP 세션(로그인된 requests.Session)은 호출자가 넘긴다 (green_auth 산물).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from config import (
    GREEN3_BOARD,
    HTTP_TIMEOUT,
    QNA_BOARD,
    SORISEM_BASE_URL,
    USER_AGENT,
)


# 게시물 관리(복사/이동/삭제) 요청에는 브라우저처럼 보이는 헤더를 보낸다.
# 일부 사이트(특히 권한 점검을 강화한 후의 소리샘) 는 Mozilla 류가 아닌
# User-Agent 의 요청을 다르게 처리해서 대상 게시판 목록을 빈 채로 내려보내는 사례가
# 있다. 폼·검증 GET 도 동일한 헤더로 통일해 서버 응답 일관성을 확보.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
_BROWSER_BASE_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    # 모던 크롬은 same-origin 폼 POST 에도 Sec-Fetch-* 를 함께 보낸다.
    # 일부 서버/WAF 가 이걸 보고 "스크립트가 아닌 폼 네비게이션" 으로 인식하는데,
    # 빠지면 다른 응답을 주는 사례가 있어 같이 보낸다.
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-User": "?1",
}


def _browser_headers(referer: str = "", *, post: bool = False,
                     impersonated: bool = False) -> dict:
    """게시판 관리 호출용 헤더.

    impersonated=False (일반 requests): UA·Accept·Sec-Fetch-* 까지 직접 세팅.
    impersonated=True  (curl_cffi 가 진짜 크롬으로 위장 중): UA/Accept 등은 curl_cffi 가
        제공하므로 건드리지 않고, 요청별로 달라지는 Referer·Origin·Content-Type 과
        same-origin 폼 네비게이션을 알리는 Sec-Fetch-* 만 명시.
    """
    if impersonated:
        h: dict = {
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-User": "?1",
        }
        if referer:
            h["Referer"] = referer
        if post:
            h["Origin"] = SORISEM_BASE_URL.rstrip("/")
            h["Content-Type"] = "application/x-www-form-urlencoded"
        return h
    h = dict(_BROWSER_BASE_HEADERS)
    if referer:
        h["Referer"] = referer
    if post:
        # Origin 은 same-origin POST 일 때 브라우저가 자동으로 붙임.
        h["Origin"] = SORISEM_BASE_URL.rstrip("/")
        h["Content-Type"] = "application/x-www-form-urlencoded"
    return h


# curl_cffi 가 설치돼 있으면 게시물 복사/이동/삭제 요청을 "진짜 크롬"의 TLS·HTTP/2
# 지문으로 위장해 보낸다. 사이트 앞단 WAF 가 Python 클라이언트를 식별해 다른 응답을
# 주는 경우(대상 게시판 목록 빈 채로 등) 를 우회하기 위함.
try:  # pragma: no cover - 환경 의존
    from curl_cffi import requests as _cffi_requests  # type: ignore
except Exception:  # pragma: no cover
    _cffi_requests = None

_IMPERSONATE_PROFILE = "chrome"  # curl_cffi 가 지원하는 최신 크롬 프로파일


class _HttpClient:
    """게시판 관리용 HTTP 클라이언트.

    curl_cffi 가 있으면 크롬 TLS/HTTP 지문으로 위장한 세션(impersonated=True)을,
    없으면 넘어온 일반 requests.Session 을 그대로 쓴다. 위장 세션엔 현재 로그인
    세션의 쿠키를 복사해 넣고, 처리가 끝나면 merge_back() 으로 새로 받은 쿠키
    (새 PHPSESSID 등)를 원래 세션에 되돌려 복사한다.
    """

    def __init__(self, base_session) -> None:
        self._base = base_session
        self._cffi = None
        if _cffi_requests is not None:
            for prof in (_IMPERSONATE_PROFILE, "chrome131", "chrome124", "chrome120"):
                try:
                    self._cffi = _cffi_requests.Session(impersonate=prof)
                    break
                except Exception:
                    self._cffi = None
        if self._cffi is not None:
            try:
                for c in base_session.cookies:
                    try:
                        self._cffi.cookies.set(
                            c.name, c.value,
                            domain=c.domain or "www.sorisem.net",
                            path=c.path or "/",
                        )
                    except Exception:
                        pass
            except Exception:
                # 쿠키 복사 자체가 실패하면 위장 세션을 쓰지 않는다
                try:
                    self._cffi.close()
                except Exception:
                    pass
                self._cffi = None
        self.impersonated = self._cffi is not None
        self._client = self._cffi if self._cffi is not None else base_session

    def headers(self, referer: str = "", *, post: bool = False) -> dict:
        return _browser_headers(referer=referer, post=post, impersonated=self.impersonated)

    def get(self, url, **kw):
        return self._client.get(url, **kw)

    def post(self, url, **kw):
        return self._client.post(url, **kw)

    def merge_back(self) -> None:
        cffi = self._cffi
        self._cffi = None  # 멱등 — 두 번 불려도 안전
        if cffi is None:
            return
        try:
            for c in cffi.cookies.jar:
                try:
                    self._base.cookies.set(
                        c.name, c.value,
                        domain=c.domain or "www.sorisem.net",
                        path=c.path or "/",
                    )
                except Exception:
                    pass
        except Exception:
            pass
        try:
            cffi.close()
        except Exception:
            pass


def _dump_dir() -> str:
    """덤프 저장 디렉토리 — config 의 DUMPS_DIR 우선, 없으면 data/dumps."""
    try:
        from config import DUMPS_DIR
        return DUMPS_DIR
    except Exception:
        return "data/dumps"


# 게시판 관리(설정) 폼 URL 베이스 — bo_table 만 바꿔 끼운다.
BOARD_FORM_BASE = f"{SORISEM_BASE_URL.rstrip('/')}/skin/board/ar.common/adm.board_form.php"
# 표준 그누보드 글쓰기 페이지/처리 URL
WRITE_PAGE_BASE = f"{SORISEM_BASE_URL.rstrip('/')}/bbs/write.php"
WRITE_UPDATE_URL = f"{SORISEM_BASE_URL.rstrip('/')}/bbs/write_update.php"

# 미리 알고 있는 게시판 - GUI 목록 기본 항목용. (bo_table, 표시 이름)
# 표시 이름이 실제와 다르면 사용자가 직접 게시판 아이디로 작업하면 된다.
KNOWN_BOARDS: list[tuple[str, str]] = [
    ("green1", "공지사항"),
    ("green2", "나눔장터"),
    (GREEN3_BOARD, "우리들의 이야기"),
    ("green7", "시리즈 및 정보 게시판"),
    (QNA_BOARD, "질문게시판"),
]

# 폼에서 우리가 렌더·전송하지 않는 input type
_SKIP_INPUT_TYPES = {"submit", "button", "image", "reset", "file"}


# ---------- 폼 모델 ----------

@dataclass
class FormField:
    name: str
    label: str                       # 추출한 한글 라벨 (없으면 name)
    kind: str                        # "text" | "number" | "password" | "textarea" | "checkbox" | "radio" | "select" | "hidden"
    value: str = ""                  # 현재 값 (checkbox/radio 면 그 항목/그룹의 선택 값)
    checked: bool = False             # checkbox 의 현재 체크 여부
    options: list[tuple[str, str]] = field(default_factory=list)  # select/radio: [(value, 표시텍스트)]
    maxlength: int = 0               # text 류 maxlength (0=제한 없음)


@dataclass
class BoardForm:
    bo_table: str
    action_url: str                  # POST 대상 (절대 URL)
    method: str                      # "POST" | "GET"
    fields: list[FormField]          # hidden 포함 전부
    raw_html: str = ""

    def visible_fields(self) -> list[FormField]:
        return [f for f in self.fields if f.kind != "hidden"]

    def field_by_name(self, name: str) -> Optional[FormField]:
        for f in self.fields:
            if f.name == name:
                return f
        return None


@dataclass
class SubmitResult:
    ok: bool
    message: str
    status_code: int = 0
    response_snippet: str = ""


@dataclass
class PostResult:
    bo_table: str
    ok: bool
    message: str
    status_code: int = 0


# ---------- 라벨 추출 ----------

def _text(node) -> str:
    if node is None:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


def _extract_label(tag, soup) -> str:
    """input/select/textarea 한 개에 붙는 사람 읽는 라벨 추정.

    우선순위:
      1) <label for="<id>">  텍스트
      2) 같은 행(tr)의 <th> 텍스트  (그누보드 admin 폼은 보통 th/td 테이블)
      3) 부모 <td> 의 바로 앞 <td> 텍스트  (2열 레이아웃)
      4) placeholder / title 속성
      5) name 속성 그대로
    """
    el_id = tag.get("id")
    if el_id:
        lab = soup.find("label", attrs={"for": el_id})
        if lab is not None:
            t = _text(lab)
            if t:
                return t
    # 행/셀 기반
    tr = tag.find_parent("tr")
    if tr is not None:
        th = tr.find("th")
        if th is not None:
            t = _text(th)
            if t:
                return t
        # 2열: 첫 td 가 라벨, 둘째 td 가 입력
        tds = tr.find_all("td", recursive=False)
        if len(tds) >= 2:
            t = _text(tds[0])
            if t and tag not in tds[0].descendants:
                return t
    # dl/dt
    dt = None
    parent = tag
    for _ in range(4):
        parent = parent.find_parent(["dd", "li", "div", "p"]) if parent else None
        if parent is None:
            break
        prev = parent.find_previous_sibling(["dt", "label", "strong", "b"])
        if prev is not None:
            t = _text(prev)
            if t:
                return t
    # 속성 폴백
    for attr in ("placeholder", "title", "aria-label"):
        v = (tag.get(attr) or "").strip()
        if v:
            return v
    return tag.get("name") or ""


def _form_score(form) -> int:
    """여러 form 중 '게시판 관리 폼' 일 가능성 점수."""
    score = 0
    action = (form.get("action") or "").lower()
    if "update" in action or "board_form" in action:
        score += 50
    if form.find("input", attrs={"name": "bo_table"}) is not None:
        score += 30
    # bo_ 로 시작하는 필드가 많을수록
    bo_fields = sum(
        1 for el in form.find_all(["input", "select", "textarea"])
        if (el.get("name") or "").startswith("bo_")
    )
    score += min(bo_fields, 40)
    score += min(len(form.find_all(["input", "select", "textarea"])), 20)
    return score


def _parse_form(html: str, base_url: str, bo_table: str) -> BoardForm:
    soup = BeautifulSoup(html, "lxml")
    forms = soup.find_all("form")
    if not forms:
        raise BoardAdminError("게시판 관리 페이지에서 폼을 찾지 못했습니다.")
    form = max(forms, key=_form_score)

    action = (form.get("action") or "").strip()
    if not action or action.startswith("#") or action.lower().startswith("javascript:"):
        # action 비면 같은 디렉토리의 *_update 로 추정
        action = urljoin(base_url, "adm.board_form_update.php")
    else:
        action = urljoin(base_url, action)
    method = (form.get("method") or "post").upper()
    if method not in ("POST", "GET"):
        method = "POST"

    fields: list[FormField] = []
    seen_radio_groups: dict[str, FormField] = {}

    for el in form.find_all(["input", "select", "textarea"]):
        name = (el.get("name") or "").strip()
        if not name:
            continue
        if el.has_attr("disabled"):
            continue
        tagname = el.name

        if tagname == "textarea":
            fields.append(FormField(
                name=name, label=_extract_label(el, soup),
                kind="textarea", value=el.get_text(),
            ))
            continue

        if tagname == "select":
            opts: list[tuple[str, str]] = []
            current = ""
            for opt in el.find_all("option"):
                ov = opt.get("value")
                if ov is None:
                    ov = opt.get_text(strip=True)
                ot = opt.get_text(" ", strip=True) or ov
                opts.append((ov, ot))
                if opt.has_attr("selected"):
                    current = ov
            if not current and opts:
                current = opts[0][0]
            fields.append(FormField(
                name=name, label=_extract_label(el, soup),
                kind="select", value=current, options=opts,
            ))
            continue

        # input
        itype = (el.get("type") or "text").lower()
        if itype in _SKIP_INPUT_TYPES:
            continue
        val = el.get("value", "")
        try:
            maxlen = int(el.get("maxlength") or 0)
        except (TypeError, ValueError):
            maxlen = 0

        if itype == "hidden":
            fields.append(FormField(name=name, label=name, kind="hidden", value=val))
        elif itype == "checkbox":
            fields.append(FormField(
                name=name, label=_extract_label(el, soup),
                kind="checkbox", value=val or "1", checked=el.has_attr("checked"),
            ))
        elif itype == "radio":
            grp = seen_radio_groups.get(name)
            opt_label = _radio_option_label(el, soup) or val
            if grp is None:
                grp = FormField(
                    name=name, label=_extract_label(el, soup),
                    kind="radio", value=(val if el.has_attr("checked") else ""),
                    options=[(val, opt_label)],
                )
                seen_radio_groups[name] = grp
                fields.append(grp)
            else:
                grp.options.append((val, opt_label))
                if el.has_attr("checked"):
                    grp.value = val
        elif itype in ("password",):
            fields.append(FormField(
                name=name, label=_extract_label(el, soup),
                kind="password", value=val, maxlength=maxlen,
            ))
        elif itype in ("number",):
            fields.append(FormField(
                name=name, label=_extract_label(el, soup),
                kind="number", value=val, maxlength=maxlen,
            ))
        else:  # text, email, url, tel, search, ...
            fields.append(FormField(
                name=name, label=_extract_label(el, soup),
                kind="text", value=val, maxlength=maxlen,
            ))

    # radio 그룹에 선택값이 없으면 첫 옵션을 기본으로
    for f in fields:
        if f.kind == "radio" and not f.value and f.options:
            f.value = f.options[0][0]

    return BoardForm(
        bo_table=bo_table, action_url=action, method=method,
        fields=fields, raw_html=html,
    )


def _radio_option_label(el, soup) -> str:
    """라디오 한 개 옆에 붙는 옵션 텍스트."""
    el_id = el.get("id")
    if el_id:
        lab = soup.find("label", attrs={"for": el_id})
        if lab is not None:
            t = _text(lab)
            if t:
                return t
    # 바로 뒤 텍스트 노드/형제
    nxt = el.next_sibling
    hops = 0
    while nxt is not None and hops < 3:
        if isinstance(nxt, str):
            t = nxt.strip()
            if t:
                return t
        else:
            t = _text(nxt)
            if t:
                return t
        nxt = nxt.next_sibling
        hops += 1
    return ""


# ---------- 예외 ----------

class BoardAdminError(Exception):
    """게시판 관리·공지 작성 관련 오류."""


# ---------- 게시판 관리 폼: 조회/저장 ----------

def board_form_url(bo_table: str) -> str:
    return f"{BOARD_FORM_BASE}?bo_table={bo_table}"


def fetch_board_form(session: requests.Session, bo_table: str) -> BoardForm:
    """게시판 관리 페이지를 GET 해서 폼 필드를 파싱."""
    bo = (bo_table or "").strip()
    if not bo:
        raise BoardAdminError("게시판 아이디(bo_table)가 비어 있습니다.")
    url = board_form_url(bo)
    try:
        resp = session.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
    except requests.exceptions.RequestException as e:
        raise BoardAdminError(f"네트워크 오류: {e}") from e
    if not resp.ok:
        raise BoardAdminError(f"HTTP {resp.status_code} - {url}")
    text = resp.text or ""
    if "권한이 없" in text or "로그인 후 이용" in text or "최고관리자" in text and "<form" not in text:
        raise BoardAdminError(
            "게시판 관리 페이지 접근 권한이 없는 것 같습니다 "
            "(동호회관리자 계정으로 로그인했는지 확인하세요)."
        )
    return _parse_form(text, base_url=url, bo_table=bo)


def build_submit_payload(
    form: BoardForm, overrides: dict[str, object] | None = None,
) -> list[tuple[str, str]]:
    """폼 현재값 + overrides 를 합쳐 POST 페이로드(키-값 튜플 리스트) 생성.

    overrides: {field_name: 새 값}. 체크박스는 bool 로(True=체크, False=해제),
    그 외는 문자열로 준다.

    HTML 폼 규칙 그대로:
      - 체크박스: 체크면 (name, value) 한 번 포함, 해제면 아예 안 보냄.
      - 라디오: 선택된 값 1개만 포함.
      - hidden/text/textarea/select: (name, value) 그대로.
    중복 name(체크박스 배열 등)도 튜플 리스트라 보존됨.
    """
    ov = overrides or {}
    out: list[tuple[str, str]] = []
    for f in form.fields:
        if f.kind == "checkbox":
            if f.name in ov:
                checked = bool(ov[f.name])
            else:
                checked = f.checked
            if checked:
                out.append((f.name, str(f.value or "1")))
        elif f.kind == "radio":
            val = str(ov[f.name]) if f.name in ov else f.value
            if val:
                out.append((f.name, val))
        else:  # hidden / text / number / password / textarea / select
            val = ov[f.name] if f.name in ov else f.value
            out.append((f.name, "" if val is None else str(val)))
    return out


def submit_board_form(
    session: requests.Session, form: BoardForm, overrides: dict[str, object] | None = None,
) -> SubmitResult:
    """수정값을 폼 action 으로 POST. 명백한 실패 마커가 없으면 성공으로 본다."""
    payload = build_submit_payload(form, overrides)
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": board_form_url(form.bo_table),
    }
    try:
        if form.method == "GET":
            resp = session.get(form.action_url, params=payload, headers=headers,
                               timeout=HTTP_TIMEOUT * 2, allow_redirects=True)
        else:
            resp = session.post(form.action_url, data=payload, headers=headers,
                                timeout=HTTP_TIMEOUT * 2, allow_redirects=True)
    except requests.exceptions.RequestException as e:
        return SubmitResult(ok=False, message=f"네트워크 오류: {e}")
    body = resp.text or ""
    snippet = body[:600]
    if not resp.ok:
        return SubmitResult(ok=False, message=f"HTTP {resp.status_code}",
                            status_code=resp.status_code, response_snippet=snippet)
    for marker in ("권한이 없", "로그인 후 이용", "최고관리자만", "정확히 입력", "잘못된 접근"):
        if marker in body:
            return SubmitResult(ok=False, message=f"사이트가 거부했습니다 (응답에 '{marker}' 포함)",
                                status_code=resp.status_code, response_snippet=snippet)
    return SubmitResult(ok=True, message="게시판 설정을 저장했습니다.",
                        status_code=resp.status_code, response_snippet=snippet)


# ---------- 공지 작성 ----------

def write_page_url(bo_table: str) -> str:
    return f"{WRITE_PAGE_BASE}?bo_table={bo_table}&cl=green"


def _scrape_write_form(session: requests.Session, bo_table: str) -> dict[str, str]:
    """글쓰기 폼 페이지를 GET 해서 모든 input/select/textarea 의 기본값을 수집.

    숨김 토큰(token, uid, csrf_token, w, wr_id, bo_table 등)을 그대로 가져오기 위함.
    체크박스는 체크된 것만, 라디오는 선택된 것만. 호출자가 이 위에 덮어쓴다.
    """
    url = write_page_url(bo_table)
    try:
        resp = session.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
    except requests.exceptions.RequestException as e:
        raise BoardAdminError(f"네트워크 오류: {e}") from e
    if not resp.ok:
        raise BoardAdminError(f"글쓰기 폼 HTTP {resp.status_code} - {url}")
    soup = BeautifulSoup(resp.text or "", "lxml")
    # write_update 로 보내는 폼 찾기 (action 에 write_update 포함, 없으면 fwrite/가장 큰 폼)
    forms = soup.find_all("form")
    if not forms:
        raise BoardAdminError("글쓰기 폼을 찾지 못했습니다.")
    def wscore(f):
        a = (f.get("action") or "").lower()
        s = 0
        if "write_update" in a:
            s += 50
        if (f.get("id") or "") == "fwrite":
            s += 30
        s += len(f.find_all(["input", "select", "textarea"]))
        return s
    form = max(forms, key=wscore)
    data: dict[str, str] = {}
    for el in form.find_all(["input", "select", "textarea"]):
        name = (el.get("name") or "").strip()
        if not name or el.has_attr("disabled"):
            continue
        if el.name == "textarea":
            data[name] = el.get_text()
            continue
        if el.name == "select":
            sel = ""
            for opt in el.find_all("option"):
                if opt.has_attr("selected"):
                    sel = opt.get("value", opt.get_text(strip=True))
                    break
            data[name] = sel
            continue
        itype = (el.get("type") or "text").lower()
        if itype in _SKIP_INPUT_TYPES:
            continue
        if itype in ("checkbox", "radio"):
            if el.has_attr("checked"):
                data[name] = el.get("value", "1")
            # 체크 안 됐으면 안 넣음
        else:
            data[name] = el.get("value", "")
    return data


def write_post(
    session: requests.Session,
    bo_table: str,
    subject: str,
    content: str,
    *,
    as_notice: bool = False,
    use_html: bool = False,
    secret: bool = False,
    notify_mail: bool = False,
) -> PostResult:
    """게시판에 새 글(또는 공지)을 작성한다.

    as_notice=True 면 글쓰기 폼의 'notice' 체크박스를 켠 효과 (= 그 글이 게시판
    상단에 고정 공지로 등록됨).
    """
    bo = (bo_table or "").strip()
    if not bo:
        return PostResult(bo_table=bo, ok=False, message="게시판 아이디가 비어 있습니다.")
    if not (subject or "").strip():
        return PostResult(bo_table=bo, ok=False, message="제목이 비어 있습니다.")

    try:
        data = _scrape_write_form(session, bo)
    except BoardAdminError as e:
        return PostResult(bo_table=bo, ok=False, message=str(e))

    # 새 글 작성으로 덮어쓰기
    data["w"] = ""
    data["bo_table"] = bo
    data["wr_id"] = "0"
    data["wr_subject"] = subject
    data["wr_content"] = content
    data["html"] = "html1" if use_html else ""
    data["secret"] = "secret" if secret else ""
    data["mail"] = "mail" if notify_mail else ""
    if as_notice:
        data["notice"] = "1"
    else:
        data.pop("notice", None)
    # 그누보드는 빈 wr_name/wr_password 도 받을 수 있게 — 로그인 관리자면 무시됨.
    data.setdefault("wr_name", "")
    data.setdefault("wr_password", "")
    data.setdefault("wr_email", "")
    data.setdefault("wr_homepage", "")

    headers = {
        "User-Agent": USER_AGENT,
        "Referer": write_page_url(bo),
    }
    try:
        resp = session.post(WRITE_UPDATE_URL, data=data, headers=headers,
                            timeout=HTTP_TIMEOUT * 2, allow_redirects=True)
    except requests.exceptions.RequestException as e:
        return PostResult(bo_table=bo, ok=False, message=f"네트워크 오류: {e}")
    body = resp.text or ""
    if not resp.ok:
        return PostResult(bo_table=bo, ok=False, message=f"HTTP {resp.status_code}",
                          status_code=resp.status_code)
    for marker in ("권한이 없", "로그인 후 이용", "제목을 입력", "내용을 입력",
                   "자동등록방지", "차단된", "잘못된 접근"):
        if marker in body:
            return PostResult(bo_table=bo, ok=False,
                              message=f"사이트가 거부했습니다 (응답에 '{marker}' 포함)",
                              status_code=resp.status_code)
    note = " (공지로 등록)" if as_notice else ""
    return PostResult(bo_table=bo, ok=True, message=f"작성 완료{note}",
                      status_code=resp.status_code)


ProgressCB = Callable[[int, int], None]


def post_notice_to_boards(
    session: requests.Session,
    bo_tables: Iterable[str],
    subject: str,
    content: str,
    *,
    as_notice: bool = True,
    use_html: bool = False,
    progress_cb: Optional[ProgressCB] = None,
) -> list[PostResult]:
    """같은 제목·본문을 여러 게시판에 작성 (일괄 공지). 게시판마다 1회씩."""
    boards = [b.strip() for b in bo_tables if b and b.strip()]
    out: list[PostResult] = []
    total = len(boards)
    for i, bo in enumerate(boards, start=1):
        if progress_cb:
            try:
                progress_cb(i, total)
            except Exception:
                pass
        out.append(write_post(
            session, bo, subject, content,
            as_notice=as_notice, use_html=use_html,
        ))
    return out


# ==========================================================================
# 게시물 목록 + 복사/이동/삭제
#   소리샘(ar.basic 스킨)의 글 목록 폼 fboardlist:
#     - action="./board_list_update.php"  (선택삭제: btn_submit=선택삭제 로 POST)
#     - 선택복사/이동: JS 가 action 을 ./move.php 로 바꾸고 sw=copy|move 로 POST →
#       대상 게시판 선택 팝업 폼 → move_update.php 로 POST.
#     - 체크박스 name="chk_wr_id[]" value=<wr_id>, hidden: bo_table/sfl/stx/spt/sca/page/sw
# ==========================================================================

BOARD_LIST_BASE = f"{SORISEM_BASE_URL.rstrip('/')}/bbs/board.php"
# 선택삭제 등 목록 일괄처리 (ar 스킨). 실제로는 페이지에서 긁은 폼 action 을 우선 사용.
BOARD_LIST_UPDATE_URL = f"{SORISEM_BASE_URL.rstrip('/')}/bbs/board_list_update.php"
DELETE_ALL_URL = f"{SORISEM_BASE_URL.rstrip('/')}/bbs/delete_all.php"  # 구형/대체 경로
MOVE_URL = f"{SORISEM_BASE_URL.rstrip('/')}/bbs/move.php"
MOVE_UPDATE_URL = f"{SORISEM_BASE_URL.rstrip('/')}/bbs/move_update.php"

# 목록 폼의 '선택삭제' 버튼 값 (board_list_update.php 가 이 값으로 동작을 분기)
DELETE_BTN_VALUE = "선택삭제"

_CHK_FIELD = "chk_wr_id[]"
_DATE_RE = re.compile(r"\d{2,4}[-./]\d{1,2}[-./]\d{1,2}")
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")
_MD_RE = re.compile(r"\b\d{1,2}[-.]\d{1,2}\b")

_POST_ACTION_FAIL_MARKERS = (
    "권한이 없", "최고관리자만", "관리자만", "로그인 후 이용",
    "선택된 게시물이 없", "게시물을 선택", "게시물을 하나 이상", "잘못된 접근", "자동등록방지",
    "이동할 수 없", "복사할 수 없",
    # ar 스킨 오류 페이지 / 대상 게시판 미선택 등
    "오류안내 페이지", "다음 항목에 오류가 있", "게시판을 한개 이상", "게시판을 하나 이상",
    "게시판을 한 개 이상",
)


@dataclass
class PostItem:
    wr_id: str
    subject: str
    author: str = ""
    date: str = ""
    is_notice: bool = False


@dataclass
class PostListResult:
    bo_table: str
    page: int
    items: list[PostItem]
    list_form: dict[str, str]   # 목록 폼(fboardlist)의 hidden 필드들 (chk_wr_id[] 제외)
    list_action_url: str = ""   # 그 폼의 action (선택삭제 POST 대상). 비면 기본값 사용.


@dataclass
class PostActionResult:
    ok: bool
    action: str            # "delete" | "move" | "copy"
    bo_table: str
    count: int = 0
    target_bo_table: str = ""
    message: str = ""
    status_code: int = 0
    response_snippet: str = ""   # ok=False / 불확실할 때 진단용 사이트 응답 일부
    debug: dict = field(default_factory=dict)   # {라벨: 원본 HTML} — 이동/복사 팝업 응답 등 진단용


# ---------- 게시물 목록: 조회 ----------

def board_list_url(bo_table: str, page: int = 1) -> str:
    p = max(1, int(page or 1))
    return f"{BOARD_LIST_BASE}?bo_table={bo_table}&page={p}"


def fetch_post_list(session: requests.Session, bo_table: str, page: int = 1) -> PostListResult:
    """게시판 목록 페이지를 GET 해서 글 목록 + 목록 폼 hidden 필드를 파싱."""
    bo = (bo_table or "").strip()
    if not bo:
        raise BoardAdminError("게시판 아이디(bo_table)가 비어 있습니다.")
    pg = max(1, int(page or 1))
    url = board_list_url(bo, pg)
    try:
        resp = session.get(
            url, timeout=HTTP_TIMEOUT,
            headers=_browser_headers(referer=f"{SORISEM_BASE_URL.rstrip('/')}/"),
        )
    except requests.exceptions.RequestException as e:
        raise BoardAdminError(f"네트워크 오류: {e}") from e
    if not resp.ok:
        raise BoardAdminError(f"게시판 목록 HTTP {resp.status_code} - {url}")
    return _parse_post_list(resp.text or "", bo, pg)


def _parse_post_list(html: str, bo_table: str, page: int) -> PostListResult:
    soup = BeautifulSoup(html, "lxml")
    checks = soup.find_all("input", attrs={"name": _CHK_FIELD})
    if not checks:
        checks = [c for c in soup.find_all("input", attrs={"type": "checkbox"})
                  if (c.get("name") or "").startswith("chk_wr_id")]
    if not checks:
        raise BoardAdminError(
            "게시물 목록에서 선택용 체크박스를 찾지 못했습니다 "
            "(동호회관리자로 로그인했는지, 게시판 아이디가 맞는지 확인하세요)."
        )
    list_form_tag = checks[0].find_parent("form")
    list_form: dict[str, str] = {}
    list_action_url = ""
    if list_form_tag is not None:
        a = (list_form_tag.get("action") or "").strip()
        if a and not a.startswith("#") and not a.lower().startswith("javascript:"):
            list_action_url = urljoin(board_list_url(bo_table, page), a)
        for el in list_form_tag.find_all("input", attrs={"type": "hidden"}):
            n = (el.get("name") or "").strip()
            if n and not n.startswith("chk_wr_id"):
                list_form[n] = el.get("value", "")
        for sel in list_form_tag.find_all("select"):
            n = (sel.get("name") or "").strip()
            if not n or n.startswith("chk_wr_id"):
                continue
            chosen = ""
            for o in sel.find_all("option"):
                if o.has_attr("selected"):
                    chosen = o.get("value", o.get_text(strip=True))
            list_form.setdefault(n, chosen)
    list_form.setdefault("bo_table", bo_table)

    items: list[PostItem] = []
    seen: set[str] = set()
    for chk in checks:
        wr_id = (chk.get("value") or "").strip()
        if not wr_id or wr_id in seen:
            continue
        seen.add(wr_id)
        row = chk.find_parent("tr") or chk.find_parent("li")
        if row is None:
            row = chk.parent
            for _ in range(5):
                if row is None or row.name in ("ul", "ol", "table", "tbody", "form", "body"):
                    break
                if row.name in ("tr", "li"):
                    break
                row = row.parent
        subject = _post_subject(row, wr_id) if row is not None else ""
        if not subject:
            subject = f"(글번호 {wr_id})"
        author, date = _post_meta(row) if row is not None else ("", "")
        is_notice = False
        if row is not None:
            cls = " ".join(row.get("class") or []).lower()
            is_notice = "notice" in cls
        items.append(PostItem(wr_id=wr_id, subject=subject, author=author,
                              date=date, is_notice=is_notice))
    return PostListResult(bo_table=bo_table, page=page, items=items,
                          list_form=list_form, list_action_url=list_action_url)


def _link_text(a) -> str:
    """<a> 의 제목 텍스트 (안에 있는 댓글수 span 등 부가표시는 제외)."""
    try:
        parts = a.find_all(string=True, recursive=False)
        txt = " ".join("".join(str(p) for p in parts).split())
        if txt:
            return txt
    except Exception:
        pass
    return _text(a)


def _post_subject(row, wr_id: str) -> str:
    best = ""
    pat = re.compile(rf"wr_id={re.escape(wr_id)}(?:\D|$)")
    for a in row.find_all("a"):
        href = a.get("href") or ""
        txt = _link_text(a)
        if not txt:
            continue
        if pat.search(href):
            return txt
        if "wr_id=" in href and len(txt) > len(best):
            best = txt
    return best


def _post_meta(row) -> tuple[str, str]:
    author = ""
    for el in row.find_all(attrs={"class": True}):
        cls = " ".join(el.get("class") or []).lower()
        if any(k in cls for k in ("sv_name", "name", "writer", "member", "nick", "td_name")):
            t = _text(el)
            if t and len(t) <= 40:
                author = t
                break
    text = row.get_text(" ", strip=True)
    m = _DATE_RE.search(text) or _TIME_RE.search(text) or _MD_RE.search(text)
    return author, (m.group(0) if m else "")


def fetch_board_list_html(session: requests.Session, bo_table: str, page: int = 1) -> str:
    """진단용: 게시판 목록 페이지 원본 HTML 을 그대로 반환 (저장해서 분석)."""
    bo = (bo_table or "").strip()
    if not bo:
        raise BoardAdminError("게시판 아이디(bo_table)가 비어 있습니다.")
    url = board_list_url(bo, max(1, int(page or 1)))
    try:
        resp = session.get(
            url, timeout=HTTP_TIMEOUT,
            headers=_browser_headers(referer=f"{SORISEM_BASE_URL.rstrip('/')}/"),
        )
    except requests.exceptions.RequestException as e:
        raise BoardAdminError(f"네트워크 오류: {e}") from e
    return resp.text or ""


# ---------- 게시물 복사/이동/삭제 ----------

def _looks_failed(body: str) -> str:
    for m in _POST_ACTION_FAIL_MARKERS:
        if m in body:
            return m
    return ""


def _is_real_error_page(body: str) -> str:
    """'명백한' 에러 페이지인지 판정 — 정상 페이지에 박힌 JS alert 문자열이나
    팝업 캡션("게시판을 한개 이상 선택해 주십시오") 같은 오탐을 피한다.

    에러 표식: <div id="validation_check"> (그누보드 에러 페이지의 noscript 폴백) /
    <title>오류안내 페이지</title> / 로그인 페이지 리다이렉트.
    반환: 사람이 읽을 수 있는 사유 문자열, 또는 "" (에러 아님).
    """
    b = body or ""
    if 'id="validation_check"' in b:
        m = re.search(r'id="validation_check".*?<(?:p|h1)[^>]*>(.*?)</(?:p|h1)>',
                      b, re.S | re.I)
        if m:
            msg = re.sub(r"<[^>]+>", " ", m.group(1))
            msg = " ".join(msg.split())
            return msg[:140] if msg else "검증 오류"
        return "검증 오류"
    if "<title>오류안내" in b:
        return "오류안내 페이지"
    head = b[:6000]
    if re.search(r"/bbs/login\.php", head) and 'name="mb_password"' in head:
        return "로그인 페이지로 리다이렉트됨 — 세션 만료 가능"
    return ""


def _looks_like_move_popup(body: str) -> bool:
    """move.php 응답이 '대상 게시판 선택 팝업'인지 (목록이 비었더라도)."""
    b = body or ""
    return ("fboardmoveall" in b) or ('id="copymove"' in b) or ("move_update.php" in b)


def _norm_ids(wr_ids) -> list[str]:
    return [str(w).strip() for w in (wr_ids or []) if str(w).strip()]


def _collapse(text: str, limit: int = 900) -> str:
    return " ".join((text or "").split())[:limit]


# 처리 완료를 알리는 흔한 응답 조각 (그누보드 alert/goto_url 메시지)
_ACTION_OK_HINTS = ("삭제", "이동", "복사", "옮기")
_ACTION_OK_TAILS = ("하였습니다", "되었습니다", "했습니다", "됐습니다", "하셨습니다", "완료")


def _response_says_success(resp, body: str) -> bool:
    """사이트 응답이 '처리 완료'로 보이면 True. (검증용 재조회가 안 될 때의 보조 판단)"""
    body = body or ""
    final_url = getattr(resp, "url", "") or ""
    if "board.php" in final_url:
        return True
    if re.search(r'(?:opener\.|parent\.)?location\.(?:href|replace)\s*[=(]\s*["\'][^"\']*board\.php', body):
        return True
    for h in _ACTION_OK_HINTS:
        for t in _ACTION_OK_TAILS:
            if (h + t) in body:
                return True
    for combo in ("삭제 되었습니다", "이동 되었습니다", "복사 되었습니다",
                  "정상적으로 처리", "처리되었습니다", "처리하였습니다"):
        if combo in body:
            return True
    return False


def _verify_gone(session, bo_table: str, page, wr_ids) -> tuple:
    """삭제/이동 후 그 글들이 목록에서 사라졌는지 재조회로 확인.

    반환: (True, 메시지)  = 모두 사라짐 (성공 확정)
          (False, 메시지) = 일부/전부 그대로 남음 (실패 확정)
          (None, 메시지)  = 재조회 불가 등으로 확인 못 함
    """
    ids = list(wr_ids)
    try:
        after = fetch_post_list(session, bo_table, page)
    except BoardAdminError:
        return None, "확인용 목록을 다시 불러오지 못했습니다 (빈 페이지일 수 있음)"
    except Exception:
        return None, "확인 중 오류"
    remaining = {it.wr_id for it in after.items}
    still = [w for w in ids if w in remaining]
    if not still:
        return True, f"{len(ids)}개 처리 확인됨 (목록에서 사라짐)"
    if len(still) == len(ids):
        return False, f"처리되지 않았습니다 - 선택한 {len(ids)}개가 목록에 그대로 남아 있습니다"
    return False, f"일부만 처리됨 - {len(ids) - len(still)}개 처리, {len(still)}개는 그대로 남음"


def _list_first_page_ids(session, bo_table: str):
    """대상 게시판 1쪽 글번호 집합 (확인 불가면 None)."""
    try:
        res = fetch_post_list(session, bo_table, 1)
    except Exception:
        return None
    return {it.wr_id for it in res.items}


def _verify_appeared(session, to_bo_table: str, expected_count: int, before_ids) -> tuple:
    """이동/복사 후 대상 게시판 1쪽에 새 글이 생겼는지 확인.

    반환: (True, 메시지) = 기대한 만큼(이상) 새 글 생김
          (False, 메시지) = 새 글 없음 / 부족
          (None, 메시지) = 확인 불가
    """
    if before_ids is None:
        return None, "처리 전 대상 게시판 목록을 받지 못해 확인 불가"
    after = _list_first_page_ids(session, to_bo_table)
    if after is None:
        return None, "처리 후 대상 게시판 목록을 받지 못해 확인 불가"
    new_ids = after - before_ids
    if len(new_ids) >= max(1, expected_count):
        return True, f"{expected_count}개가 대상 게시판('{to_bo_table}')에 추가된 것을 확인했습니다"
    if not new_ids:
        return False, f"대상 게시판('{to_bo_table}')에 새 글이 추가되지 않았습니다"
    return False, f"대상 게시판('{to_bo_table}')에 {len(new_ids)}개만 추가됨 (기대 {expected_count}개)"


def _strict_success(body: str, action: str) -> bool:
    """이동/복사 응답에 '복사/이동 …되었습니다' 류 명시적 완료 문구가 있으면 True.
    (재조회 검증이 안 될 때만 사용 — 단순 board.php 링크 같은 약한 신호는 인정하지 않음.)"""
    body = body or ""
    verb = "복사" if action == "copy" else "이동"
    tails = ("하였습니다", "되었습니다", "했습니다", "됐습니다", "하셨습니다", "완료")
    for t in tails:
        if (verb + t) in body or (verb + " " + t) in body or (verb + "가 " + t) in body:
            return True
    for combo in ("처리되었습니다", "처리하였습니다", "정상적으로 처리", "정상 처리되었"):
        if combo in body:
            return True
    return False


def _list_form_page(list_form: dict | None) -> str:
    v = str((list_form or {}).get("page", "") or "").strip()
    return v if v else "1"


def delete_posts(
    session: requests.Session, bo_table: str, wr_ids,
    *, list_form: dict | None = None, list_action_url: str | None = None,
) -> PostActionResult:
    """선택한 글들을 목록 폼(action=board_list_update.php)에 btn_submit=선택삭제 로 POST 해서
    일괄 삭제하고, 목록을 재조회해 실제로 사라졌는지 확인."""
    bo = (bo_table or "").strip()
    ids = _norm_ids(wr_ids)
    if not bo:
        return PostActionResult(ok=False, action="delete", bo_table=bo, message="게시판 아이디가 비어 있습니다.")
    if not ids:
        return PostActionResult(ok=False, action="delete", bo_table=bo, message="삭제할 게시물을 선택하세요.")
    page = _list_form_page(list_form)
    url = (list_action_url or "").strip() or BOARD_LIST_UPDATE_URL
    data: list[tuple[str, str]] = []
    for k, v in (list_form or {}).items():
        if k and not k.startswith("chk_wr_id") and not k.startswith("_"):
            data.append((k, "" if v is None else str(v)))
    if not any(k == "bo_table" for k, _ in data):
        data.append(("bo_table", bo))
    if not any(k == "sw" for k, _ in data):
        data.append(("sw", ""))            # 삭제는 sw 빈 값
    for w in ids:
        data.append((_CHK_FIELD, w))
    data.append(("btn_submit", DELETE_BTN_VALUE))   # board_list_update.php 동작 분기 키
    http = _HttpClient(session)
    # 워밍업 — 브라우저처럼 게시판 목록 페이지를 먼저 한 번 GET
    list_referer = board_list_url(bo, page)
    try:
        http.get(list_referer, headers=http.headers(referer=f"{SORISEM_BASE_URL.rstrip('/')}/"),
                 timeout=HTTP_TIMEOUT)
    except Exception:
        pass
    headers = http.headers(referer=list_referer, post=True)
    try:
        resp = http.post(url, data=data, headers=headers,
                         timeout=HTTP_TIMEOUT * 2, allow_redirects=True)
    except Exception as e:
        http.merge_back()
        return PostActionResult(ok=False, action="delete", bo_table=bo, message=f"네트워크 오류: {e}")
    body = resp.text or ""
    http.merge_back()  # 새 쿠키를 원래 세션으로 — 이후 검증 GET 은 일반 requests
    snip = _collapse(body)
    if not resp.ok:
        return PostActionResult(ok=False, action="delete", bo_table=bo, status_code=resp.status_code,
                                message=f"HTTP {resp.status_code}", response_snippet=snip)
    bad = _is_real_error_page(body)
    if bad:
        return PostActionResult(ok=False, action="delete", bo_table=bo, status_code=resp.status_code,
                                message=f"사이트가 '{bad}' 라며 거부했습니다", response_snippet=snip)
    # 실제로 사라졌는지 목록 재조회로 확인
    verified, vmsg = _verify_gone(session, bo, page, ids)
    if verified is True:
        return PostActionResult(ok=True, action="delete", bo_table=bo, count=len(ids),
                                status_code=resp.status_code, message=vmsg)
    if verified is False:
        return PostActionResult(ok=False, action="delete", bo_table=bo, count=0,
                                status_code=resp.status_code, message=vmsg, response_snippet=snip)
    # 확인 불가 → 응답으로 추정
    if _response_says_success(resp, body):
        return PostActionResult(ok=True, action="delete", bo_table=bo, count=len(ids),
                                status_code=resp.status_code,
                                message=f"{len(ids)}개 삭제 요청을 보냈습니다 (목록에서 직접 확인해 주세요)")
    return PostActionResult(ok=False, action="delete", bo_table=bo, status_code=resp.status_code,
                            response_snippet=snip,
                            message=("삭제가 처리되지 않은 것 같습니다 - 사이트 응답에서 처리 결과를 확인하지 못했습니다. "
                                     "'목록 페이지 원본 HTML 저장' 으로 진단 파일을 만들어 주세요."))


def move_posts(
    session: requests.Session, bo_table: str, wr_ids, to_bo_table: str,
    *, copy: bool = False, list_form: dict | None = None,
) -> PostActionResult:
    """선택한 글들을 다른 게시판으로 이동(copy=False) 또는 복사(copy=True).

    그누보드 흐름: 1) move.php 로 POST → 대상 게시판 선택 폼 받음
                   2) 그 폼에 to_bo_table 채워 move_update.php 로 POST.
    move.php 가 돌려준 대상 후보 목록에 to_bo_table 이 없으면 거부로 처리.
    """
    bo = (bo_table or "").strip()
    to_bo = (to_bo_table or "").strip()
    sw = "copy" if copy else "move"
    act = "copy" if copy else "move"
    verb = "복사" if copy else "이동"
    ids = _norm_ids(wr_ids)
    if not bo:
        return PostActionResult(ok=False, action=act, bo_table=bo, message="원본 게시판 아이디가 비어 있습니다.")
    if not to_bo:
        return PostActionResult(ok=False, action=act, bo_table=bo, message="대상 게시판 아이디를 입력하세요.")
    if to_bo == bo:
        return PostActionResult(ok=False, action=act, bo_table=bo, message="원본과 대상 게시판이 같습니다.")
    if not ids:
        return PostActionResult(ok=False, action=act, bo_table=bo, message=f"{verb}할 게시물을 선택하세요.")
    page = _list_form_page(list_form)
    dbg: dict = {}

    # 처리 전 대상 게시판 1쪽 글번호 — 나중에 새 글이 생겼는지로 성공 판정
    target_before = _list_first_page_ids(session, to_bo)

    # 1단계: move.php — 대상 게시판 선택 팝업 폼 받기.
    #
    # 실제 ar.basic 스킨의 목록 폼(fboardlist) HTML 필드 순서를 그대로 따라가야
    # 서버가 브라우저와 같은 응답을 준다. 폼 순서:
    #   bo_table → sfl → stx → spt → sca → page → sw → chk_wr_id[]×N → btn_submit
    # JS 가 클릭한 submit 버튼만 폼 전송에 포함되므로 act 같은 임의 필드는 보내지 않는다.
    btn_value = "선택복사" if copy else "선택이동"
    lf = list_form or {}

    def _lf(name: str, default: str = "") -> str:
        v = lf.get(name, default)
        return "" if v is None else str(v)

    step1: list[tuple[str, str]] = [
        ("bo_table", bo),
        ("sfl", _lf("sfl")),
        ("stx", _lf("stx")),
        ("spt", _lf("spt")),
        ("sca", _lf("sca")),
        ("page", _lf("page", str(page) if page else "1")),
        ("sw", sw),
    ]
    # 위에서 보낸 표준 필드 외에 list_form 에서 추가로 들어있는 hidden 만 덧붙임
    # (token 등 스킨이 새로 도입한 필드 대비). 이미 보낸 것·예약 키는 제외.
    _already = {k for k, _ in step1} | {"chk_wr_id[]", "btn_submit"}
    for k, v in lf.items():
        if not k or k.startswith("_") or k.startswith("chk_wr_id") or k in _already:
            continue
        step1.append((k, "" if v is None else str(v)))
    for w in ids:
        step1.append((_CHK_FIELD, w))
    step1.append(("btn_submit", btn_value))
    # HTTP 클라이언트 — curl_cffi 가 있으면 크롬 TLS/HTTP 지문으로 위장.
    http = _HttpClient(session)

    # 1단계 워밍업 — 브라우저 관리자가 보통 거치는 순서대로 페이지를 방문.
    # gnuboard 류는 가끔 admin 페이지 방문 직후에만 일부 admin 폼·쿼리가 풀린다.
    base = SORISEM_BASE_URL.rstrip("/")
    sys_url = f"{base}/_sys/"
    try:
        adm = http.get(sys_url, headers=http.headers(referer=f"{base}/"),
                       timeout=HTTP_TIMEOUT)
        if adm is not None:
            dbg["_sys/ (admin warmup)"] = (adm.text or "")[:30000]
    except Exception:
        pass

    list_referer = board_list_url(bo, page)
    try:
        warm = http.get(list_referer, headers=http.headers(referer=sys_url),
                        timeout=HTTP_TIMEOUT)
        if warm is not None:
            dbg["board.php (warmup)"] = (warm.text or "")[:60000]
    except Exception:
        pass

    # 진단용: 도구가 보내는 헤더·쿠키 자체를 텍스트로 한 장 남겨 둠.
    try:
        cookie_lines = "\n".join(f"  {c.name} = {c.value}" for c in session.cookies)
        headers_preview = http.headers(referer=list_referer, post=True)
        impers = ("ON (curl_cffi, 크롬 TLS/HTTP 지문 위장)" if http.impersonated
                  else "OFF (일반 requests — curl_cffi 미설치)")
        diag = (
            f"[Impersonation] {impers}\n\n"
            "[Extra request headers (move.php POST)]\n"
            + "\n".join(f"  {k}: {v}" for k, v in headers_preview.items())
            + ("\n  (그 외 UA/Accept/sec-ch-ua 등은 curl_cffi 가 크롬 것으로 자동 세팅)"
               if http.impersonated else "")
            + "\n\n[Session cookies]\n"
            + (cookie_lines or "  (none)")
            + "\n\n[Form POST body keys]\n"
            + "\n".join(f"  {k} = {v!r}" for k, v in step1)
        )
        dbg["request_diag"] = (
            "<html><body><pre>" + diag.replace("<", "&lt;") + "</pre></body></html>"
        )
    except Exception:
        pass

    headers = http.headers(referer=list_referer, post=True)
    try:
        r1 = http.post(MOVE_URL, data=step1, headers=headers,
                       timeout=HTTP_TIMEOUT * 2, allow_redirects=True)
    except Exception as e:
        http.merge_back()
        return PostActionResult(ok=False, action=act, bo_table=bo, message=f"네트워크 오류: {e}", debug=dbg)
    b1 = r1.text or ""
    dbg["move.php"] = b1[:60000]
    if not r1.ok:
        http.merge_back()
        return PostActionResult(ok=False, action=act, bo_table=bo, status_code=r1.status_code,
                                message=f"HTTP {r1.status_code} (move.php)", response_snippet=_collapse(b1), debug=dbg)
    # move.php 응답이 '대상 게시판 선택 팝업'이면 (목록이 비었더라도) 정상 — 그대로 진행.
    # 명백한 에러 페이지일 때만 중단 (팝업 캡션 "게시판을 한개 이상 선택" 같은 오탐 회피).
    if not _looks_like_move_popup(b1):
        bad = _is_real_error_page(b1)
        if bad:
            http.merge_back()
            return PostActionResult(ok=False, action=act, bo_table=bo, status_code=r1.status_code,
                                    message=f"{verb} 단계1(move.php) 거부됨: {bad}",
                                    response_snippet=_collapse(b1), debug=dbg)

    soup = BeautifulSoup(b1, "lxml")
    forms = soup.find_all("form")
    move_form = None
    for f in forms:
        if "move_update" in (f.get("action") or "").lower():
            move_form = f
            break
    if move_form is None and forms:
        move_form = max(forms, key=lambda f: len(f.find_all(["input", "select"])))

    data: dict[str, str] = {"sw": sw, "bo_table": bo}
    update_url = MOVE_UPDATE_URL
    allowed: list[str] = []          # move.php 가 보여 준 '이동/복사 가능한' 게시판 후보
    target_field_names: list[str] = []   # select 형태의 대상 필드 이름들
    chk_target_field = ""            # 체크박스 형태의 대상 필드 이름 (보통 chk_bo_table[])
    if move_form is not None:
        a = (move_form.get("action") or "").strip()
        if a and not a.startswith("#") and not a.lower().startswith("javascript:"):
            update_url = urljoin(MOVE_URL, a)
        for el in move_form.find_all("input"):
            n = (el.get("name") or "").strip()
            if not n:
                continue
            itype = (el.get("type") or "text").lower()
            if itype in _SKIP_INPUT_TYPES:
                continue
            if itype in ("checkbox", "radio"):
                # ar.basic: 대상 게시판은 <input type=checkbox name="chk_bo_table[]" value="<게시판>">
                # tbody 안에 한 줄씩 들어 있다. 후보 목록(allowed) 으로 수집.
                if "bo_table" in n:
                    chk_target_field = n
                    cv = (el.get("value") or "").strip()
                    if cv:
                        allowed.append(cv)
                if el.has_attr("checked"):
                    data[n] = el.get("value", "1")
            else:
                data[n] = el.get("value", "")
        for sel in move_form.find_all("select"):
            n = (sel.get("name") or "").strip()
            if not n:
                continue
            opts = [(o.get("value") if o.get("value") is not None else o.get_text(strip=True))
                    for o in sel.find_all("option")]
            opts = [o for o in opts if o]
            if "bo_table" in n or n in ("to_table", "to_bo", "tbo"):
                allowed.extend(opts)
                target_field_names.append(n)
            chosen = ""
            for o in sel.find_all("option"):
                if o.has_attr("selected"):
                    chosen = o.get("value", o.get_text(strip=True))
            data[n] = chosen or (opts[0] if opts else "")
    # 중복 제거 (순서 유지)
    allowed = list(dict.fromkeys(a for a in allowed if a))
    joined = ",".join(ids)
    for fname in ("wr_id_list", "wr_ids", "wr_id"):   # 스킨마다 글번호 필드명이 다름
        if not data.get(fname):
            data[fname] = joined
    data.setdefault("act", "복사" if copy else "이동")  # ar 스킨이 메시지/분기에 쓰는 한글 verb

    if allowed and to_bo not in allowed:
        # 사이트가 후보를 줬는데 그 안에 대상이 없음 → 명백한 거부.
        http.merge_back()
        return PostActionResult(
            ok=False, action=act, bo_table=bo, target_bo_table=to_bo,
            message=(f"대상 게시판 '{to_bo}' 으로는 {verb}할 수 없습니다. "
                     f"이 계정이 {verb}할 수 있는 게시판: {', '.join(allowed)}"),
            status_code=r1.status_code, response_snippet=_collapse(b1), debug=dbg,
        )
    target_list_was_empty = not allowed
    # move.php 가 대상 게시판 후보를 하나도 보여 주지 않은 경우. 브라우저 흐름에서는
    # 채워져 오는 것을 같은 계정·같은 세션으로 우리 도구가 받으면 비어 오는 사례 있음.
    # move_update.php 가 chk_bo_table[] 를 직접 처리하는 스킨도 있으므로 일단 시도는
    # 해 보되, 실패하면 메시지에 진단·후속 조치 안내를 덧붙인다.
    empty_hint = (
        ""
        if not target_list_was_empty
        else (
            " ※ 진단: move.php 가 대상 게시판 목록을 빈 채로 내려줘서 chk_bo_table[] 로"
            " 직접 요청했는데도 처리가 안 됐습니다. 이 계정이 대상 게시판"
            f" '{to_bo}' (또는 그 게시판이 속한 그룹)의 게시판관리자·그룹회원으로"
            " 등록돼 있는지 소리샘 담당자에게 확인해 주세요. data/dumps 의"
            " move_update_php.html 을 함께 보내 주시면 더 정확히 짚을 수 있습니다."
        )
    )
    # 대상 게시판 지정 — 소리샘(ar) 은 chk_bo_table[] 체크박스, 구형 그누보드는 to_bo_table select.
    data["to_bo_table"] = to_bo
    data[chk_target_field or "chk_bo_table[]"] = to_bo
    for n in target_field_names:
        data[n] = to_bo

    # 2단계: move_update.php — 실제 처리 (위장 클라이언트로)
    try:
        r2 = http.post(
            update_url, data=data,
            headers=http.headers(referer=MOVE_URL, post=True),
            timeout=HTTP_TIMEOUT * 2, allow_redirects=True,
        )
    except Exception as e:
        http.merge_back()
        return PostActionResult(ok=False, action=act, bo_table=bo, target_bo_table=to_bo,
                                message=f"네트워크 오류: {e}", debug=dbg)
    b2 = r2.text or ""
    # 위장 세션이 받은 새 쿠키를 원래 세션으로 되돌림 — 이후 검증 GET 은 일반 requests 로.
    http.merge_back()
    dbg["move_update.php"] = b2[:60000]
    # 결과 검증을 위해 대상 게시판 1쪽 목록 HTML 도 그대로 덤프.
    try:
        target_html = fetch_board_list_html(session, to_bo, 1)
        if target_html:
            dbg[f"대상 게시판({to_bo}) 목록 GET"] = target_html[:60000]
    except Exception:
        pass
    snip = f"[move.php] {_collapse(b1, 350)}  ||  [move_update.php] {_collapse(b2, 350)}"
    if not r2.ok:
        return PostActionResult(ok=False, action=act, bo_table=bo, target_bo_table=to_bo, status_code=r2.status_code,
                                message=f"HTTP {r2.status_code} (move_update.php)", response_snippet=snip, debug=dbg)
    bad = _is_real_error_page(b2)
    if bad:
        return PostActionResult(ok=False, action=act, bo_table=bo, target_bo_table=to_bo, status_code=r2.status_code,
                                message=f"사이트가 '{bad}' 라며 거부했습니다.{empty_hint}", response_snippet=snip, debug=dbg)

    # --- 처리 결과 검증 ---
    if not copy:
        gone, gmsg = _verify_gone(session, bo, page, ids)
        if gone is True:
            return PostActionResult(ok=True, action=act, bo_table=bo, target_bo_table=to_bo, count=len(ids),
                                    status_code=r2.status_code, debug=dbg,
                                    message=f"{len(ids)}개 이동 확인됨 (원본에서 사라짐, 대상: {to_bo})")
        if gone is False:
            return PostActionResult(ok=False, action=act, bo_table=bo, target_bo_table=to_bo,
                                    status_code=r2.status_code, response_snippet=snip, debug=dbg,
                                    message=f"이동되지 않았습니다 - {gmsg}{empty_hint}")
        # gone is None → 대상 게시판 확인으로
    appeared, amsg = _verify_appeared(session, to_bo, len(ids), target_before)
    if appeared is True:
        return PostActionResult(ok=True, action=act, bo_table=bo, target_bo_table=to_bo, count=len(ids),
                                status_code=r2.status_code, message=amsg, debug=dbg)
    if appeared is False:
        return PostActionResult(ok=False, action=act, bo_table=bo, target_bo_table=to_bo,
                                status_code=r2.status_code, response_snippet=snip, debug=dbg,
                                message=(f"{verb}가 안 된 것 같습니다 - {amsg}.{empty_hint or ' 결과창에 적힌 사이트 응답을 개발자에게 보내 주세요.'}"))
    # appeared is None → 마지막으로 응답 문구로만 추정 (엄격: '복사/이동 …되었습니다' 류만 인정)
    if _strict_success(b2, act):
        return PostActionResult(ok=True, action=act, bo_table=bo, target_bo_table=to_bo, count=len(ids),
                                status_code=r2.status_code, debug=dbg,
                                message=f"{len(ids)}개 {verb} 완료로 응답받음 (대상: {to_bo})")
    return PostActionResult(ok=False, action=act, bo_table=bo, target_bo_table=to_bo, status_code=r2.status_code,
                            response_snippet=snip, debug=dbg,
                            message=(f"{verb} 결과를 확인하지 못했습니다 - 대상 게시판에 새 글이 안 보이고 "
                                     f"응답에도 완료 표시가 없습니다.{empty_hint or ' 결과창의 사이트 응답을 개발자에게 보내 주세요.'}"))
