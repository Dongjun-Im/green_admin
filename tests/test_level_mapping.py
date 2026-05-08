"""등급 매핑 일관성 — config 의 모든 매핑이 서로 어긋나지 않는지."""
from __future__ import annotations


def test_level_labels_complete():
    """LEVEL_LABELS 가 0~9 모두 포함."""
    from config import LEVEL_LABELS
    for i in range(10):
        assert i in LEVEL_LABELS, f"레벨 {i} 가 LEVEL_LABELS 에 없음"


def test_new_mapping_5_to_9():
    """v0.4 새 매핑 (5=준회원 ~ 9=명예회원) 확정."""
    from config import LEVEL_LABELS
    assert LEVEL_LABELS[5] == "준회원"
    assert LEVEL_LABELS[6] == "일반회원"
    assert LEVEL_LABELS[7] == "우수회원"
    assert LEVEL_LABELS[8] == "최우수회원"
    assert LEVEL_LABELS[9] == "명예회원"


def test_outstanding_levels_are_excellent_tier():
    """백업 대상 OUTSTANDING_LEVELS = (7, 8) — 우수·최우수."""
    from config import LEVEL_LABELS, OUTSTANDING_LEVELS
    assert OUTSTANDING_LEVELS == (7, 8)
    assert all(LEVEL_LABELS[lv] in {"우수회원", "최우수회원"} for lv in OUTSTANDING_LEVELS)


def test_level_transitions_no_admin_no_honor():
    """장기미접속 조정에서 명예회원(9)는 제외, 5~8 만 처리."""
    from config import LEVEL_TRANSITIONS, WITHDRAW_LEVEL
    assert 9 not in LEVEL_TRANSITIONS
    assert set(LEVEL_TRANSITIONS.keys()) == {5, 6, 7, 8}
    # 준회원은 탈퇴
    assert LEVEL_TRANSITIONS[5] == ("delete", WITHDRAW_LEVEL)
    # 나머지는 한 단계 강등
    assert LEVEL_TRANSITIONS[6][0] == "demote" and LEVEL_TRANSITIONS[6][1] == 5
    assert LEVEL_TRANSITIONS[7][0] == "demote" and LEVEL_TRANSITIONS[7][1] == 6
    assert LEVEL_TRANSITIONS[8][0] == "demote" and LEVEL_TRANSITIONS[8][1] == 7


def test_promotion_table_ordered_desc():
    """ACTIVITY_PROMOTION_TABLE 는 임계값 desc 정렬 — 로직이 의존."""
    from config import ACTIVITY_PROMOTION_TABLE
    thresholds = [t for t, _ in ACTIVITY_PROMOTION_TABLE]
    assert thresholds == sorted(thresholds, reverse=True)


def test_selectable_levels_match_labels():
    """SELECTABLE_LEVELS = 5~9 — UI 콤보에 노출."""
    from config import SELECTABLE_LEVELS
    assert SELECTABLE_LEVELS == (5, 6, 7, 8, 9)


def test_pending_levels_are_signup_phase():
    """PENDING_LEVELS = (3, 4) — 가입 대기·신청."""
    from config import APPROVE_TO_LEVEL, PENDING_LEVELS, REJECT_TO_LEVEL
    assert PENDING_LEVELS == (3, 4)
    # 승인 = 준회원, 거부는 가입 단계 안에서만 (1=탈퇴 또는 2=거부)
    assert APPROVE_TO_LEVEL == 5
    assert REJECT_TO_LEVEL in (1, 2)


def test_text_map_inverse_consistent():
    """LEVEL_TEXT_MAP 의 라벨이 LEVEL_LABELS 의 값에 모두 들어 있다."""
    from config import LEVEL_LABELS, LEVEL_TEXT_MAP
    label_set = set(LEVEL_LABELS.values())
    for label, lv in LEVEL_TEXT_MAP.items():
        # 띄어쓰기 변형(예: "명예 회원" / "명예회원") 둘 다 허용
        normalized = label.replace(" ", "")
        assert any(
            normalized == v.replace(" ", "")
            for v in label_set
        ) or lv in (0, 1, 2, 3, 4), f"{label!r} 가 LEVEL_LABELS 와 매칭되지 않음"
