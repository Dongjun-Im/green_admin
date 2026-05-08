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
        # 신청·대기 → 준회원 (활동점수 3)
        (3, 0, None),
        (3, 2.9, None),
        (3, 3, 5),
        (4, 100, 5),
        # 준회원 → 일반회원 (v0.5 신규, 활동점수 5)
        (5, 4.9, None),
        (5, 5, 6),
        (5, 50, 6),
        # 일반회원 → 우수/최우수/명예 (30/60/300, v1.0.1 명예 임계 상향)
        (6, 29, None),
        (6, 30, 7),
        (6, 59.9, 7),
        (6, 60, 8),
        (6, 119, 8),
        (6, 120, 8),
        (6, 299, 8),
        (6, 300, 9),
        (6, 9999, 9),
        # 7 이상은 변경 없음
        (7, 10000, None),
        (8, 10000, None),
        (9, 10000, None),
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
    assert determine_target_level_by_posts(6, 30) == 7
    assert determine_target_level_by_posts(3, 3) == 5
