"""백업 보관 정책 — 오래된 백업 폴더를 zip 으로 압축.

기본 정책:
  · `backups/YYYY-MM-DD/` 폴더 중 N개월(기본 12) 보다 오래된 것을
    `backups/archives/YYYY-MM-DD.zip` 로 압축하고 원본 폴더는 삭제.
  · zip 파일은 절대 자동 삭제하지 않는다 (개인정보 포함).

⚠ 자동 삭제 정책은 도입하지 않는다 — 운영자가 직접 zip 을 정리하도록.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from dateutil.relativedelta import relativedelta

from config import BACKUPS_DIR


ARCHIVE_SUBDIR = "archives"
DEFAULT_RETENTION_MONTHS = 12


@dataclass
class RetentionResult:
    archived: list[str] = field(default_factory=list)  # 압축된 폴더 이름들
    skipped: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    archive_dir: Optional[Path] = None

    @property
    def summary(self) -> str:
        return (
            f"압축 {len(self.archived)}건, "
            f"건너뜀 {len(self.skipped)}건, "
            f"실패 {len(self.errors)}건"
        )


def _is_iso_date_dir(name: str) -> Optional[date]:
    """폴더 이름이 YYYY-MM-DD 면 date 반환, 아니면 None."""
    try:
        return date.fromisoformat(name)
    except ValueError:
        return None


def find_old_backup_dirs(
    backups_dir: Path | None = None,
    cutoff: Optional[date] = None,
    months: int = DEFAULT_RETENTION_MONTHS,
) -> list[Path]:
    base = Path(backups_dir or BACKUPS_DIR)
    if not base.exists():
        return []
    if cutoff is None:
        cutoff = date.today() - relativedelta(months=months)

    out: list[Path] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        if child.name == ARCHIVE_SUBDIR:
            continue
        d = _is_iso_date_dir(child.name)
        if d is None:
            continue
        if d <= cutoff:
            out.append(child)
    return out


def archive_backup_dir(folder: Path, archive_dir: Path) -> Path:
    """folder 를 archive_dir/<name>.zip 으로 압축.

    이미 zip 이 있으면 OSError 발생.
    압축 성공 시 원본 폴더 삭제.
    """
    archive_dir.mkdir(parents=True, exist_ok=True)
    out = archive_dir / f"{folder.name}.zip"
    if out.exists():
        raise OSError(f"이미 압축본 존재: {out}")

    # shutil.make_archive 는 .zip 를 자동 부여하므로 base_name 은 확장자 빼고
    base_name = str(out.with_suffix(""))
    shutil.make_archive(
        base_name=base_name,
        format="zip",
        root_dir=str(folder.parent),
        base_dir=folder.name,
    )
    # 원본 폴더 삭제
    shutil.rmtree(str(folder))
    return out


def archive_old_backups(
    months: int = DEFAULT_RETENTION_MONTHS,
    backups_dir: Path | None = None,
) -> RetentionResult:
    base = Path(backups_dir or BACKUPS_DIR)
    archive_dir = base / ARCHIVE_SUBDIR
    targets = find_old_backup_dirs(backups_dir=base, months=months)

    result = RetentionResult(archive_dir=archive_dir)
    for folder in targets:
        try:
            archive_backup_dir(folder, archive_dir)
            result.archived.append(folder.name)
        except OSError as e:
            result.errors.append((folder.name, str(e)))
    return result
