"""사이트 구조 변경 감지 (v1.0).

회원 목록 페이지·게시판 검색 페이지가 언제 우리 가정과 어긋났는지를
구체적 메시지로 진단한다. 크롤러/파서가 EmptyParseError 를 던질 때
이 모듈로 추가 진단을 만들어 사용자에게 알려준다.

진단 항목:
  · 페이지 길이가 비정상 (로그인 풀림 의심)
  · "권한이 없습니다" 같은 거부 메시지
  · fmemberlist form 부재
  · cl_level select 부재
  · select option value 가 우리 매핑(0~9) 범위를 벗어남
  · 회원 행은 있으나 컬럼 수가 다름
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup

from config import LEVEL_LABELS


@dataclass
class DiagnosticReport:
    severity: str = "ok"   # "ok" | "warning" | "error"
    findings: list[str] = field(default_factory=list)
    sample: str = ""

    def add(self, msg: str, severity: str = "warning") -> None:
        self.findings.append(msg)
        if severity == "error":
            self.severity = "error"
        elif severity == "warning" and self.severity == "ok":
            self.severity = "warning"

    def text(self) -> str:
        if not self.findings:
            return "사이트 구조 진단: 특이 사항 없음."
        head = (
            "사이트 구조 진단 — 다음 항목이 우리 가정과 다릅니다:"
            if self.severity == "error"
            else "사이트 구조 진단 — 주의가 필요한 항목:"
        )
        body = "\n".join(f"  · {f}" for f in self.findings)
        return f"{head}\n{body}"


def diagnose_admin_member_html(html: str) -> DiagnosticReport:
    """admin.member.php?cl=green 응답에 대한 진단."""
    report = DiagnosticReport()
    if not html:
        report.add("응답 본문이 비어 있습니다 — 네트워크 또는 세션 문제 가능.", "error")
        return report

    if len(html) < 500:
        report.add(
            f"응답 본문 길이가 매우 짧습니다 ({len(html)} 자). 로그인이 풀렸을 수 있습니다.",
            "error",
        )

    if "권한이 없습니다" in html or "로그인이 필요" in html:
        report.add(
            "사이트가 권한 거부/로그인 요구 메시지를 반환했습니다. "
            "관리자 권한 또는 세션 만료를 확인하세요.",
            "error",
        )

    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form", id="fmemberlist") or soup.find(
        "form", attrs={"name": "fmemberlist"}
    )
    if form is None:
        report.add(
            "fmemberlist form 을 찾지 못했습니다. "
            "관리자 페이지 URL/파라미터가 변경되었거나 권한이 없는 페이지를 받았습니다.",
            "error",
        )
        return report

    # cl_level select 확인
    selects = form.find_all("select", attrs={"name": re.compile(r"^cl_level\[")})
    if not selects:
        report.add(
            "cl_level select 가 보이지 않습니다 — 사이트 폼 필드 이름이 변경됐을 수 있습니다.",
            "error",
        )
        return report

    # option value 범위 검사 (5개 표본)
    bad_values: set[str] = set()
    for sel in selects[:5]:
        for opt in sel.find_all("option"):
            v = (opt.get("value") or "").strip()
            try:
                vi = int(v)
            except ValueError:
                if v:
                    bad_values.add(v)
                continue
            if vi not in LEVEL_LABELS:
                bad_values.add(v)
    if bad_values:
        report.add(
            f"select option value 중 매핑(0~9) 밖의 값 발견: "
            f"{sorted(bad_values)} — 사이트 등급 체계가 바뀌었을 수 있습니다.",
            "warning",
        )

    # 회원 행 개수 확인
    tbody = form.find("tbody")
    rows = []
    if tbody is not None:
        rows = [tr for tr in tbody.find_all("tr") if tr.find("td") is not None]
    if not rows:
        report.add(
            "tbody 안에 회원 행이 0건입니다. "
            "검색 필터가 적용된 상태이거나 회원이 정말 없는지 확인하세요.",
            "error",
        )
        return report

    # 컬럼 수 검사 (예상: 7개)
    col_counts = [len(tr.find_all("td", recursive=False)) for tr in rows[:5]]
    if col_counts and max(col_counts) < 4:
        report.add(
            f"회원 행의 컬럼 수가 비정상입니다 (최대 {max(col_counts)}). "
            f"테이블 구조가 변경된 것 같습니다.",
            "error",
        )

    # 가장 첫 행의 raw HTML 일부를 샘플로 보관
    if rows:
        report.sample = str(rows[0])[:1500]

    return report
