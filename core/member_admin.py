"""소리샘 admin.member.php 일괄 폼 POST 어댑터.

⚠ dry_run=True 가 기본값. 명시적으로 False 설정 시에만 실제 POST.

사이트 폼 구조 (대략):
    <form name="fmemberlist" action="..." method="post">
      <input type="hidden" name="cl" value="green">
      <input type="hidden" name="sst" value="cl_datetime">
      <input type="hidden" name="sod" value="desc">
      <input type="hidden" name="sfl" value="">
      <input type="hidden" name="stx" value="">
      <input type="hidden" name="page" value="1">
      <input type="hidden" name="token" value="...">          ← 실제로는 값이 들어 있음
      ... 회원별 <select name='cl_level[mb_id]'> ...
      <input type="submit" name="act_button" value="등급변경" />
    </form>

탈퇴는 cl_level[mb_id]=1 (option value='1' = 탈퇴) 로 설정.
한 번의 POST 에 여러 회원의 cl_level 을 묶어 일괄 처리 가능.

⚠ 토큰: 폼의 hidden token 값을 그대로 안 보내면 사이트가 변경을 무시할 수 있다.
   그래서 POST 전에 폼 페이지를 GET 해서 token·기타 hidden·submit 버튼·action 을
   실제 값으로 긁어 쓰고, POST 후엔 회원을 다시 조회해 실제 반영 여부를 확인한다.
   각 단계 HTML 은 data/dumps/ 에 덤프해 진단에 쓴다.
"""
from __future__ import annotations

import os
import time
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from config import (
    ADMIN_MEMBER_URL,
    DUMPS_DIR,
    HTTP_TIMEOUT,
    LEVEL_LABELS,
    USER_AGENT,
    WITHDRAW_LEVEL,
)
from core.models import AdminActionResult, Member

# 회원 목록 파서 — 변경 후 재조회 검증에 재사용
try:
    from core.member_parser import EmptyParseError, MemberListParser
except Exception:  # pragma: no cover - 방어
    MemberListParser = None  # type: ignore
    class EmptyParseError(Exception):  # type: ignore
        pass


def _dump_html(html: str, tag: str) -> str:
    """HTML 을 data/dumps/member_admin_<ts>_<tag>.html 로 저장하고 경로 반환 (실패 시 "")."""
    try:
        os.makedirs(DUMPS_DIR, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in str(tag)) or "dump"
        path = os.path.join(DUMPS_DIR, f"member_admin_{ts}_{safe}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html or "")
        return path
    except OSError:
        return ""


# POST 에서 우리가 다시 보내지 않는 input type
_SKIP_INPUT_TYPES = {"button", "image", "reset", "file"}


class MemberAdminAdapter:
    POST_URL = ADMIN_MEMBER_URL  # 기본값 — 실제로는 폼 action 을 우선 사용
    REFERER = ADMIN_MEMBER_URL

    def __init__(
        self,
        session: requests.Session,
        cl: str = "green",
        dry_run: bool = True,
    ) -> None:
        self.session = session
        self.cl = cl
        self.dry_run = dry_run

    # ---------- 단건 ----------

    def change_level(self, member: Member, new_level: int) -> AdminActionResult:
        return self._submit({member.user_id: new_level}, action_label=f"레벨 변경 → {new_level}")

    def delete_member(self, member: Member) -> AdminActionResult:
        # 사이트에는 별도 탈퇴 엔드포인트가 없음. 등급을 1(탈퇴) 로 변경하면 됨.
        return self._submit({member.user_id: WITHDRAW_LEVEL}, action_label="탈퇴 처리")

    # ---------- 일괄 ----------

    def bulk_apply(
        self, level_map: dict[str, int], action_label: str = "일괄 등급 변경"
    ) -> AdminActionResult:
        """여러 회원을 한 번에 처리. {mb_id: new_level} 형태."""
        if not level_map:
            return AdminActionResult(success=True, message="처리할 회원 없음")
        return self._submit(level_map, action_label=action_label)

    # ---------- 내부: 폼 GET / 파싱 ----------

    def _admin_url(self, **extra) -> str:
        url = ADMIN_MEMBER_URL
        if extra:
            sep = "&" if "?" in url else "?"
            url = url + sep + "&".join(f"{k}={v}" for k, v in extra.items())
        return url

    def _scrape_form(
        self, html: str, base_url: str,
    ) -> tuple[str, list[tuple[str, str]], tuple[str, str] | None, dict[str, str]]:
        """폼 페이지 HTML 에서 폼 정보를 추출.

        반환: (action_url, hidden 필드들, (submit_name, submit_value), cl_level 옵션 매핑)
              · cl_level 옵션 매핑은 {라벨 텍스트 → 옵션 value 문자열}. 스킨마다 value 가
                LEVEL_LABELS 와 다를 수 있어 실제 폼에서 직접 긁는다.
        """
        soup = BeautifulSoup(html or "", "lxml")
        forms = soup.find_all("form")
        form = None
        for f in forms:
            if (f.get("name") or "") == "fmemberlist" or (f.get("id") or "") == "fmemberlist":
                form = f
                break
        if form is None:
            for f in forms:
                if f.find("select", attrs={"name": lambda v: v and v.startswith("cl_level[")}):
                    form = f
                    break
        if form is None and forms:
            form = forms[0]
        if form is None:
            return ADMIN_MEMBER_URL, [], None, {}

        action = (form.get("action") or "").strip()
        if not action or action.startswith("#") or action.lower().startswith("javascript:"):
            action_url = base_url
        else:
            action_url = urljoin(base_url, action)

        hiddens: list[tuple[str, str]] = []
        submit: tuple[str, str] | None = None
        for el in form.find_all("input"):
            name = (el.get("name") or "").strip()
            if not name:
                continue
            itype = (el.get("type") or "text").lower()
            if itype == "submit":
                # act_button=등급변경 같은 처리 트리거 버튼 — 첫 번째 또는
                # 값에 '등급'/'수정'/'변경' 이 들어간 것을 우선.
                val = el.get("value", "")
                if submit is None or any(k in val for k in ("등급", "수정", "변경")):
                    submit = (name, val)
                continue
            if itype in _SKIP_INPUT_TYPES:
                continue
            if itype in ("checkbox", "radio"):
                if el.has_attr("checked"):
                    hiddens.append((name, el.get("value", "1")))
                continue
            # hidden / text / number 등 — cl_level[...] 회원 셀렉트는 input 이 아니라 select 라 여기 안 들어옴
            if name.startswith("cl_level["):
                continue
            hiddens.append((name, el.get("value", "")))

        # cl_level 셀렉트의 옵션 매핑 추출 — 첫 cl_level[...] 셀렉트면 충분 (모든 행이 같은 옵션 목록).
        options_map: dict[str, str] = {}
        cl_sel = form.find("select", attrs={"name": lambda v: v and v.startswith("cl_level[")})
        if cl_sel is not None:
            for opt in cl_sel.find_all("option"):
                val = (opt.get("value") if opt.get("value") is not None else opt.get_text(strip=True)) or ""
                label = (opt.get_text(" ", strip=True) or "").strip()
                if val == "":
                    continue
                # 같은 라벨이 여러 value 에 매핑되는 경우는 첫 번째 유지.
                options_map.setdefault(label, val)
        return action_url, hiddens, submit, options_map

    def _translate_level_map(
        self, level_map: dict[str, int], options_map: dict[str, str],
    ) -> tuple[dict[str, str], dict[str, str], list[str]]:
        """요청한 정수 등급을, 사이트 폼의 실제 옵션 값으로 변환.

        반환:
          effective_values: {mb_id: 실제 사이트 옵션 value(str)}
          effective_labels: {mb_id: 매칭된 옵션 라벨 텍스트}
          notes: 사람이 읽을 수 있는 변환 메모(특이사항)

        매칭 규칙:
          1) LEVEL_LABELS[want_level] 라벨 그대로 옵션맵에서 찾기
          2) 라벨 부분 포함(예: '탈퇴' 가 ' 탈퇴 ' 옵션에 포함) 으로 찾기
          3) 그래도 못 찾으면 옛 동작 — str(want_level) 그대로 사용 + 경고
        """
        effective_values: dict[str, str] = {}
        effective_labels: dict[str, str] = {}
        notes: list[str] = []
        if not options_map:
            # 옵션 맵을 못 읽었으면 기존 동작 그대로 (값을 그대로 보냄)
            for mb_id, want_level in level_map.items():
                effective_values[mb_id] = str(want_level)
                effective_labels[mb_id] = LEVEL_LABELS.get(want_level, str(want_level))
            notes.append(
                "주의: 폼에서 cl_level 옵션 매핑을 읽지 못해 등급 값을 변환 없이 그대로 전송합니다."
            )
            return effective_values, effective_labels, notes

        for mb_id, want_level in level_map.items():
            want_label = LEVEL_LABELS.get(want_level, str(want_level))
            site_value: str | None = None
            matched_label: str = ""
            # 1) 완전 일치
            if want_label in options_map:
                site_value = options_map[want_label]
                matched_label = want_label
            else:
                # 2) 부분 포함 매칭 — 공백/괄호/숫자 prefix 같은 변형 흡수
                for opt_label, opt_val in options_map.items():
                    if want_label and (want_label in opt_label or opt_label in want_label):
                        site_value = opt_val
                        matched_label = opt_label
                        break
            if site_value is None:
                # 못 찾음 — 옛 동작 폴백 + 경고
                site_value = str(want_level)
                matched_label = want_label
                notes.append(
                    f"{mb_id}: '{want_label}' 옵션을 폼에서 찾지 못해 값을 그대로 전송 "
                    f"→ {site_value} (사이트가 다른 등급으로 해석할 수 있음)"
                )
            elif site_value != str(want_level):
                notes.append(
                    f"{mb_id}: '{want_label}' 의 사이트 실제 값은 {site_value} "
                    f"(우리 기준 {want_level} 과 다름 — 폼에서 읽어 보정함)"
                )
            effective_values[mb_id] = site_value
            effective_labels[mb_id] = matched_label
        return effective_values, effective_labels, notes

    # ---------- 내부: 검증 ----------

    def _fetch_member_level(self, user_id: str) -> tuple[Optional[int], str]:
        """회원 한 명을 검색해 현재 사이트 등급을 돌려준다. (level|None, dump_path)."""
        if MemberListParser is None:
            return None, ""
        url = self._admin_url(sfl="mb_id", stx=user_id, sop="and")
        try:
            resp = self.session.get(
                url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT,
            )
        except requests.exceptions.RequestException:
            return None, ""
        dump = _dump_html(resp.text or "", f"verify_{user_id}")
        if not resp.ok:
            return None, dump
        try:
            members = MemberListParser().parse(resp.text or "")
        except EmptyParseError:
            return None, dump
        except Exception:
            return None, dump
        uid = (user_id or "").lower()
        for m in members:
            if (m.user_id or "").lower() == uid:
                return m.level, dump
        return None, dump

    # ---------- 내부: 제출 ----------

    def _build_payload(
        self, effective_values: dict[str, str],
        base_hiddens: list[tuple[str, str]] | None = None,
        submit: tuple[str, str] | None = None,
    ) -> list[tuple[str, str]]:
        """폼 hidden + 회원별 cl_level 필드 묶음. list[tuple] 로 같은 키 반복 허용.

        effective_values: {mb_id: 사이트가 기대하는 cl_level 옵션 value(str)}.
        base_hiddens 가 주어지면 그것을 (token 포함) 그대로 쓰고, 없으면 종전 기본값.
        """
        payload: list[tuple[str, str]] = []
        if base_hiddens:
            # cl 이 없으면 보강
            names = {k for k, _ in base_hiddens}
            payload.extend(base_hiddens)
            if "cl" not in names:
                payload.append(("cl", self.cl))
        else:
            payload.extend([
                ("cl", self.cl),
                ("sst", "cl_datetime"),
                ("sod", "desc"),
                ("sfl", ""),
                ("stx", ""),
                ("page", "1"),
                ("token", ""),
            ])
        for mb_id, site_value in effective_values.items():
            payload.append((f"cl_level[{mb_id}]", str(site_value)))
        # 처리 트리거 submit 버튼
        if submit is not None and submit[0]:
            payload.append(submit)
        else:
            payload.append(("act_button", "등급변경"))
        return payload

    def _submit(self, level_map: dict[str, int], action_label: str) -> AdminActionResult:
        request_payload_dict = {f"cl_level[{k}]": str(v) for k, v in level_map.items()}

        if self.dry_run:
            preview = ", ".join(f"{k}→{v}" for k, v in level_map.items())
            return AdminActionResult(
                success=True,
                message=f"[DRY-RUN] {action_label}: {preview}",
                request_url=self.POST_URL,
                request_payload=request_payload_dict,
            )

        debug: dict[str, str] = {}
        headers = {"User-Agent": USER_AGENT, "Referer": self.REFERER}

        # 1) 폼 페이지 GET — token·hidden·submit·action·옵션매핑을 실제 값으로 확보 + 덤프
        action_url = ADMIN_MEMBER_URL
        base_hiddens: list[tuple[str, str]] = []
        submit: tuple[str, str] | None = None
        options_map: dict[str, str] = {}
        try:
            g = self.session.get(ADMIN_MEMBER_URL, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
            p = _dump_html(g.text or "", "form_get")
            if p:
                debug["폼 페이지(GET)"] = p
            if g.ok:
                action_url, base_hiddens, submit, options_map = self._scrape_form(
                    g.text or "", g.url or ADMIN_MEMBER_URL,
                )
        except requests.exceptions.RequestException:
            pass  # GET 실패해도 기본값으로 POST 시도

        # 등급 라벨 → 사이트 옵션 value 로 변환. 스킨이 LEVEL_LABELS 와 다른 값을 쓰면 보정됨.
        effective_values, effective_labels, notes = self._translate_level_map(level_map, options_map)
        payload = self._build_payload(effective_values, base_hiddens or None, submit)

        # 2) 실제 POST + 덤프
        try:
            resp = self.session.post(
                action_url, data=payload, headers=headers,
                timeout=HTTP_TIMEOUT, allow_redirects=True,
            )
        except requests.exceptions.RequestException as e:
            return AdminActionResult(
                success=False, message=f"네트워크 오류: {e}",
                request_url=action_url, request_payload=request_payload_dict, debug=debug,
            )
        body = resp.text or ""
        snippet = body[:500]
        p = _dump_html(body, "post_response")
        if p:
            debug["변경 요청 응답(POST)"] = p

        if not resp.ok:
            return AdminActionResult(
                success=False, message=f"HTTP {resp.status_code}",
                request_url=action_url, request_payload=request_payload_dict,
                response_snippet=snippet, debug=debug,
            )

        # 명시적 실패/오류 페이지
        if "권한이 없습니다" in body or "토큰" in body[:400] or "비정상" in body[:400] or "오류안내" in body[:400]:
            return AdminActionResult(
                success=False,
                message=action_label + " 실패 — 사이트가 거부함 (응답에 권한/토큰/오류 표시)",
                request_url=action_url, request_payload=request_payload_dict,
                response_snippet=snippet, debug=debug,
            )

        # 3) 재조회 검증 — 실제로 보낸 옵션 값(effective_values) 으로 비교
        items = list(effective_values.items())   # (mb_id, site_value_str)
        to_check = items if len(items) <= 8 else items[:8]
        mismatches: list[str] = []
        unknown: list[str] = []
        effective_levels_int: dict[str, int] = {}
        for mb_id, site_value in effective_values.items():
            try:
                effective_levels_int[mb_id] = int(site_value)
            except (TypeError, ValueError):
                pass

        verify_dump_set = False
        for mb_id, want_site_value in to_check:
            got, dump = self._fetch_member_level(mb_id)
            if dump and not verify_dump_set:
                debug["변경 후 회원 재조회(GET)"] = dump
                verify_dump_set = True
            if got is None:
                unknown.append(mb_id)
                continue
            try:
                want_int = int(want_site_value)
            except (TypeError, ValueError):
                want_int = None
            if want_int is not None and int(got) != want_int:
                lbl_got = LEVEL_LABELS.get(int(got), f"레벨 {got}")
                lbl_want = effective_labels.get(mb_id, LEVEL_LABELS.get(want_int, str(want_int)))
                mismatches.append(
                    f"{mb_id}: 사이트 등급 {got}({lbl_got}) (요청 {want_int} = '{lbl_want}')"
                )

        notes_str = (" · " + " · ".join(notes)) if notes else ""

        if mismatches:
            return AdminActionResult(
                success=False, verified=False,
                message=(action_label + " — 사이트에 반영되지 않았습니다. "
                         + "; ".join(mismatches)
                         + notes_str
                         + " · data/dumps 의 덤프 파일을 확인하세요."),
                request_url=action_url, request_payload=request_payload_dict,
                response_snippet=snippet, debug=debug,
                effective_levels=effective_levels_int, effective_labels=dict(effective_labels),
            )
        if unknown and len(unknown) == len(to_check):
            # 한 명도 검증 못 함 → 종전 키워드 추정으로 폴백
            looks_ok = (
                "동호회 회원 관리" in body or "fmemberlist" in body
                or "수정되었습니다" in body or "처리되었습니다" in body
            )
            return AdminActionResult(
                success=looks_ok, verified=None,
                message=(action_label + (" 성공(추정)" if looks_ok else " 실패(추정)")
                         + " — 재조회로 반영 여부를 확인하지 못했습니다."
                         + notes_str
                         + " data/dumps 의 덤프 파일을 확인하세요."),
                request_url=action_url, request_payload=request_payload_dict,
                response_snippet=snippet, debug=debug,
                effective_levels=effective_levels_int, effective_labels=dict(effective_labels),
            )

        note = ""
        if unknown:
            note = f" (일부 {len(unknown)}명 확인 불가)"
        if len(items) > len(to_check):
            note += f" ({len(to_check)}명만 표본 검증)"
        return AdminActionResult(
            success=True, verified=True,
            message=action_label + " 성공 (사이트 반영 확인)" + note + notes_str,
            request_url=action_url, request_payload=request_payload_dict,
            response_snippet=snippet, debug=debug,
            effective_levels=effective_levels_int, effective_labels=dict(effective_labels),
        )
