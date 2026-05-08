"""pytest 공통 설정 — sys.path 에 프로젝트 루트 추가."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# DATA_DIR 를 임시 디렉토리로 강제 — 테스트가 사용자 데이터를 건드리지 않도록.
# config import 시점에 디렉토리 생성이 일어나므로 환경변수 대신 직접 patching.
import tempfile

_TEST_TMP = Path(tempfile.mkdtemp(prefix="green_admin_test_"))
os.environ["GREEN_ADMIN_TEST_DATA_DIR"] = str(_TEST_TMP)
