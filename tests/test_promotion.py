"""활동점수 기반 자동 승급 결정 로직."""
from __future__ import annotations

import pytest


@pytest.fixture
def determine():
    from core.promotion_service import determine_target_level
    return determine_target_level


@pytest.mark.parametrize(
    "level,score,expected",
    [
        # 대기(3) → 준회원(4) (활동점수 3)
        (3, 0, None),
        (3, 2.9, None),
        (3, 3, 4),
        # 준회원(4) → 일반회원(5) (활동점수 5)
        (4, 4.9, None),
        (4, 5, 5),
        (4, 50, 5),
        # 일반회원(5) → 우수(6)/최우수(7)/명예(8) (30/60/300)
        (5, 29, None),
        (5, 30, 6),
        (5, 59.9, 6),
        (5, 60, 7),
        (5, 119, 7),
        (5, 120, 7),
        (5, 299, 7),
        (5, 300, 8),
        (5, 9999, 8),
        # 6 이상은 변경 없음 (최고 활동 등급에서 추가 승급 없음)
        (6, 10000, None),
        (7, 10000, None),
        (8, 10000, None),
        (9, 10000, None),  # 동호회관리자
        # 0~2 (손님/탈퇴/거부)는 대상 아님
        (0, 100, None),
        (1, 100, None),
        (2, 100, None),
    ],
)
def test_determine_target_level(determine, level, score, expected):
    assert determine(level, score) == expected


def test_determine_with_legacy_int_signature():
    """구버전 호환 — post_count 정수만 받던 시그니처."""
    from core.promotion_service import determine_target_level_by_posts
    assert determine_target_level_by_posts(5, 30) == 6  # 일반회원 → 우수
    assert determine_target_level_by_posts(3, 3) == 4   # 대기 → 준회원
