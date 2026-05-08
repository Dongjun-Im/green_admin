"""메일 첨부파일 multipart 인코딩."""
from __future__ import annotations

import pytest


def test_empty_attachments_returns_one_dummy_part():
    """첨부 없으면 빈 ms_file[] 더미 1개 (g5 multipart 보장용)."""
    from core.mail_sender import _build_files_payload
    files = _build_files_payload(None)
    assert len(files) == 1
    name, (filename, content, mime) = files[0]
    assert name == "ms_file[]"
    assert filename == "" and content == b""


def test_real_attachment_included(tmp_path):
    from core.mail_sender import _build_files_payload
    p = tmp_path / "hello.txt"
    p.write_text("hello world", encoding="utf-8")
    files = _build_files_payload([p])
    assert len(files) == 1
    name, (filename, content, mime) = files[0]
    assert name == "ms_file[]"
    assert filename == "hello.txt"
    assert content == b"hello world"
    assert mime.startswith("text/")


def test_multiple_attachments_all_included(tmp_path):
    from core.mail_sender import _build_files_payload
    a = tmp_path / "a.txt"
    a.write_text("A")
    b = tmp_path / "b.bin"
    b.write_bytes(b"\x00\x01\x02")
    files = _build_files_payload([a, b])
    assert len(files) == 2
    names = [f[1][0] for f in files]
    assert "a.txt" in names and "b.bin" in names


def test_missing_file_skipped(tmp_path):
    from core.mail_sender import _build_files_payload
    real = tmp_path / "ok.txt"
    real.write_text("ok")
    files = _build_files_payload([real, tmp_path / "missing.txt"])
    # 존재하는 파일만 포함
    assert len(files) == 1
    assert files[0][1][0] == "ok.txt"


def test_directory_skipped_only_files_included(tmp_path):
    from core.mail_sender import _build_files_payload
    d = tmp_path / "subdir"
    d.mkdir()
    f = tmp_path / "file.txt"
    f.write_text("hi")
    files = _build_files_payload([d, f])
    # 디렉토리는 스킵, 파일만 포함
    assert len(files) == 1
    assert files[0][1][0] == "file.txt"
