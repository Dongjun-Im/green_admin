"""소리샘 admin.member.php?cl=green 회원 목록 파서.

실제 사이트 구조 기반:
  - 회원 목록 form: <form name="fmemberlist" id="fmemberlist" method="post">
  - 회원 행: form 안의 <table> > <tbody> > <tr class="bg0|bg1 text-center">
  - 컬럼 순서 (0-based):
      0: 아이디 (a 태그 안의 텍스트)
      1: 이름 (본명)
      2: 닉네임
      3: 등급 (select 안에 selected 된 option)
      4: 최종접속 (YY-MM-DD)
      5: 가입일 (YY-MM-DD)
      6: 접속수
  - 등급은 <select name='cl_level[mb_id]'> 안의 selected option value
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional

from bs4 import BeautifulSoup, Tag

from config import LEVEL_LABELS, LEVEL_TEXT_MAP
from core.models import Member


class EmptyParseError(Exception):
    """파서가 회원을 0건 추출했을 때."""


# 컬럼 인덱스 (0-based)
COL_USER_ID = 0
COL_NAME = 1
COL_NICKNAME = 2
COL_LEVEL = 3
COL_LAST_LOGIN = 4
COL_JOIN_DATE = 5
COL_LOGIN_COUNT = 6


class MemberListParser:
    LEVEL_TEXT_MAP = LEVEL_TEXT_MAP
    LEVEL_LABELS = LEVEL_LABELS

    def parse(self, html: str) -> list[Member]:
        soup = BeautifulSoup(html, "lxml")
        rows = self._find_rows(soup)
        members: list[Member] = []
        for row in rows:
            m = self.parse_row(row)
            if m is not None:
                members.append(m)
        return members

    def _find_rows(self, soup: BeautifulSoup) -> list[Tag]:
        # 1순위: 회원 목록 폼 안의 tbody > tr
        form = soup.find("form", id="fmemberlist") or soup.find("form", attrs={"name": "fmemberlist"})
        if form is not None:
            tbody = form.find("tbody")
            if tbody is not None:
                return [tr for tr in tbody.find_all("tr") if tr.find("td") is not None]
            # tbody 없으면 form 내부의 모든 데이터 행
            return [tr for tr in form.find_all("tr") if tr.find("td") is not None]

        # 폴백: 가장 큰 데이터 테이블의 tbody
        best = None
        best_count = 0
        for table in soup.find_all("table"):
            data_rows = [tr for tr in table.find_all("tr") if tr.find("td") is not None]
            if len(data_rows) > best_count:
                best_count = len(data_rows)
                best = data_rows
        return best or []

    def parse_row(self, row: Tag) -> Optional[Member]:
        cells = row.find_all("td", recursive=False)
        if not cells:
            cells = row.find_all("td")
        if len(cells) < 4:
            return None

        user_id = self._extract_user_id(cells[COL_USER_ID]) if len(cells) > COL_USER_ID else ""
        if not user_id:
            return None

        name = cells[COL_NAME].get_text(" ", strip=True) if len(cells) > COL_NAME else ""
        nickname = cells[COL_NICKNAME].get_text(" ", strip=True) if len(cells) > COL_NICKNAME else ""

        # 등급 추출 — COL_LEVEL 셀에서 시도하되, 폼 구조가 바뀌어 select 가
        # 다른 칸으로 이동했을 수 있으니 행 전체에서 cl_level[...] select 도 탐색.
        # 그렇지 않으면 회원이 다른 칸의 텍스트(예: "동호회관리자" 표기)와
        # 우연히 매칭되어 잘못된 레벨로 분류될 수 있다.
        level, level_label = (0, "")
        cl_select = self._find_cl_level_select(row)
        if cl_select is not None:
            level, level_label = self._extract_level_from_select(cl_select)
        elif len(cells) > COL_LEVEL:
            level, level_label = self._extract_level(cells[COL_LEVEL])

        last_login = (
            self._parse_date(cells[COL_LAST_LOGIN].get_text(" ", strip=True))
            if len(cells) > COL_LAST_LOGIN else None
        )
        join_date = (
            self._parse_date(cells[COL_JOIN_DATE].get_text(" ", strip=True))
            if len(cells) > COL_JOIN_DATE else None
        )

        login_count: Optional[int] = None
        if len(cells) > COL_LOGIN_COUNT:
            txt = cells[COL_LOGIN_COUNT].get_text(" ", strip=True).replace(",", "")
            if txt.isdigit():
                try:
                    login_count = int(txt)
                except ValueError:
                    pass

        # v1.0.2: 동호회관리자 마커 추출 — cl_level 과 별개로 사이트가 표시하는
        # 관리자 표기/체크박스를 감지한다. cl_level=9(명예회원) 인 운영진을
        # 일반 명예회원과 구분하기 위함.
        is_admin = self._detect_admin_flag(row, user_id)

        return Member(
            user_id=user_id,
            name=name,
            nickname=nickname,
            level=level,
            level_label=level_label or self.LEVEL_LABELS.get(level, ""),
            last_login_date=last_login,
            join_date=join_date,
            login_count=login_count,
            raw_row_html=str(row)[:2000],
            is_admin=is_admin,
        )

    def _extract_user_id(self, td: Tag) -> str:
        # <a href="member.answer.php?cl=green&mb_id=zzz">zzz</a>
        a = td.find("a", href=True)
        if a is not None:
            m = re.search(r"mb_id=([^&]+)", a["href"])
            if m:
                return m.group(1)
            txt = a.get_text(strip=True)
            if txt:
                return txt
        return td.get_text(strip=True)

    def _detect_admin_flag(self, row: Tag, user_id: str) -> bool:
        """행 안에서 동호회관리자 표시를 찾는다.

        v1.0.3: 보수적으로 — **명시적인 admin 필드** 만 신뢰한다.
        텍스트 매칭(예: 셀에 "관리자" 가 들어감, 옵션 텍스트에 "관리자" 포함) 은
        오탐(190+ 회원이 잘못 잡히는 사례) 이 발생해 비활성화.

        지금 검사하는 패턴 (전부 explicit form 필드):
          1) `<input type="checkbox" name="cl_admin[…]"|"bb_admin[…]"|"mb_admin[…]" checked>`
          2) `<select name="cl_admin[…]"|"bb_admin[…]"|"mb_admin[…]">` 의 selected
             값이 비-0
          3) cl_level select 의 selected option **value** 가 ADMIN_LEVELS 에 포함
             (예: value="10" — 사이트가 admin 을 별도 cl_level 로 표현하는 경우)
        """
        # 1) admin 류 체크박스 / hidden 입력 — 이름 자체에 "admin" 이 들어가야 함.
        admin_name_pat = re.compile(
            r"^(cl_admin|bb_admin|mb_admin)\[", re.IGNORECASE
        )
        for inp in row.find_all("input"):
            name = inp.get("name", "") or ""
            if not admin_name_pat.match(name):
                continue
            t = (inp.get("type", "") or "").lower()
            if t == "checkbox" and inp.has_attr("checked"):
                return True
            if t == "hidden":
                v = (inp.get("value", "") or "").strip()
                if v and v != "0":
                    return True

        # 2) admin 류 select
        for sel in row.find_all("select"):
            name = sel.get("name", "") or ""
            if not admin_name_pat.match(name):
                continue
            opt = sel.find("option", selected=True) or next(
                (o for o in sel.find_all("option") if o.has_attr("selected")), None
            )
            if opt is not None:
                v = (opt.get("value", "") or "").strip()
                if v and v != "0":
                    return True

        # 3) cl_level value 가 ADMIN_LEVELS (예: 10) 에 포함될 때만 admin.
        # 옵션 텍스트의 "관리자" 문자열 매칭은 오탐이 많아 제거함.
        cl_sel = self._find_cl_level_select(row)
        if cl_sel is not None:
            opt = cl_sel.find("option", selected=True) or next(
                (o for o in cl_sel.find_all("option") if o.has_attr("selected")), None
            )
            if opt is not None:
                try:
                    from config import ADMIN_LEVELS
                    if int((opt.get("value") or "").strip()) in ADMIN_LEVELS:
                        return True
                except (ValueError, ImportError):
                    pass

        return False

    def _find_cl_level_select(self, row: Tag) -> Optional[Tag]:
        """행 안에서 name 이 'cl_level[...]' 인 select 를 찾는다.

        v1.0.1: COL_LEVEL 인덱스에 의존하지 않고 행 전체에서 탐색해
        컬럼 시프트 / 추가 컬럼(예: '동호회관리자' 표기 칸) 으로 인한
        오인식을 방지한다.
        """
        for sel in row.find_all("select"):
            name = sel.get("name", "")
            if name.startswith("cl_level["):
                return sel
        return None

    def _extract_level_from_select(self, select: Tag) -> tuple[int, str]:
        selected_opt = select.find("option", selected=True)
        if selected_opt is None:
            for opt in select.find_all("option"):
                if opt.has_attr("selected"):
                    selected_opt = opt
                    break
        if selected_opt is None:
            return 0, ""
        val = selected_opt.get("value", "").strip()
        label = selected_opt.get_text(" ", strip=True)
        try:
            return int(val), label
        except ValueError:
            pass
        # value 파싱 실패 시 라벨로 매핑
        for k, v in self.LEVEL_TEXT_MAP.items():
            if k.replace(" ", "") in label.replace(" ", ""):
                return v, label
        return 0, label

    def _extract_level(self, td: Tag) -> tuple[int, str]:
        # <select name='cl_level[mb_id]'>
        #   <option value='5' selected>준회원</option>  # v0.4 새 매핑
        select = td.find("select")
        if select is not None:
            return self._extract_level_from_select(select)
        # select 없으면 텍스트로 추론 — "동호회관리자" 같은 긴 라벨이 짧은 라벨에
        # 흡수되지 않도록 LEVEL_TEXT_MAP 입력 순서(긴 키 우선) 그대로 검사.
        text = td.get_text(" ", strip=True)
        compact = text.replace(" ", "")
        for k, v in self.LEVEL_TEXT_MAP.items():
            if k.replace(" ", "") in compact:
                return v, k
        return 0, text

    def _parse_date(self, text: str) -> Optional[date]:
        if not text:
            return None
        text = text.strip()
        # 사이트 포맷: YY-MM-DD (예: 26-04-07)
        m = re.match(r"^(\d{2})-(\d{1,2})-(\d{1,2})$", text)
        if m:
            yy = int(m.group(1))
            year = 2000 + yy if yy < 70 else 1900 + yy
            try:
                return date(year, int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
        # YYYY-MM-DD
        m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", text)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
        # 상대시간
        m = re.search(r"(\d+)\s*(일|개월|달|년)\s*전", text)
        if m:
            n = int(m.group(1))
            today = date.today()
            unit = m.group(2)
            if unit == "일":
                return today - timedelta(days=n)
            if unit in ("개월", "달"):
                return today - timedelta(days=n * 30)
            if unit == "년":
                return today - timedelta(days=n * 365)
        return None
