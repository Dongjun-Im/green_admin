"""업데이트 확인 + 자산 다운로드 (네트워크 호출은 monkeypatch 로 대체)."""
from __future__ import annotations

from pathlib import Path


def test_normalize_version():
    from core.update_check import _normalize
    assert _normalize("1.0.0") == (1, 0, 0, 0)
    assert _normalize("v1.0.0") == (1, 0, 0, 0)
    assert _normalize("V0.5.1") == (0, 5, 1, 0)
    assert _normalize("1.0") == (1, 0, 0, 0)
    assert _normalize("") == (0, 0, 0, 0)


def test_is_newer():
    from core.update_check import is_newer
    assert is_newer("v1.0.0", "0.5.0")
    assert is_newer("1.0.1", "1.0.0")
    assert is_newer("1.1.0", "1.0.99")
    assert not is_newer("1.0.0", "1.0.0")
    assert not is_newer("v0.4.9", "1.0.0")
    assert not is_newer("0.4.0", "0.5.0")


def test_normalize_handles_pre_release():
    from core.update_check import _normalize
    n = _normalize("1.0.0-rc1")
    assert n[:3] == (1, 0, 0)


# ---------------------------------------------------------------------------
# v1.2.6 — assets parsing + streaming download
# ---------------------------------------------------------------------------


def _asset(name: str, size: int = 100) -> dict:
    return {
        "name": name,
        "size": size,
        "browser_download_url": f"https://example.invalid/{name}",
    }


def test_pick_asset_prefers_installer_over_zip():
    from core.update_check import _pick_asset
    assets = [
        _asset("초록등대회원관리_v1.2.6_portable.zip"),
        _asset("초록등대회원관리_v1.2.6_setup.exe"),
    ]
    picked = _pick_asset(assets)
    assert picked is not None
    assert picked["name"].endswith("_setup.exe")


def test_pick_asset_falls_back_to_portable():
    from core.update_check import _pick_asset
    assets = [_asset("초록등대회원관리_v1.2.6_portable.zip")]
    picked = _pick_asset(assets)
    assert picked is not None
    assert picked["name"].endswith("_portable.zip")


def test_pick_asset_returns_none_when_no_known_suffix():
    from core.update_check import _pick_asset
    assets = [
        _asset("source-code.tar.gz"),
        _asset("notes.pdf"),
    ]
    assert _pick_asset(assets) is None
    assert _pick_asset([]) is None


class _FakeResponse:
    """`requests.get(stream=True)` 페이크 — iter_content 로 청크를 흘려보냄."""

    def __init__(self, chunks: list[bytes], *, content_length: int | None = None,
                 status: int = 200):
        self._chunks = chunks
        self.status_code = status
        total = sum(len(c) for c in chunks) if content_length is None else content_length
        self.headers = {"Content-Length": str(total)} if total else {}

    def raise_for_status(self) -> None:
        if not (200 <= self.status_code < 300):
            import requests
            raise requests.exceptions.HTTPError(f"status {self.status_code}")

    def iter_content(self, chunk_size: int = 0):
        # chunk_size 는 무시 — 테스트는 명시 청크 그대로 흘려보냄.
        for c in self._chunks:
            yield c

    def close(self) -> None:
        pass


def test_download_release_asset_writes_file_and_reports_progress(monkeypatch, tmp_path):
    from core import update_check

    chunks = [b"A" * 16, b"B" * 16, b"C" * 8]  # total 40 bytes

    def fake_get(url, **kwargs):
        # 스트리밍 모드 + UA 헤더가 전달되는지 확인.
        assert kwargs.get("stream") is True
        return _FakeResponse(chunks)

    monkeypatch.setattr(update_check.requests, "get", fake_get)

    progress: list[tuple[int, int]] = []
    dest = tmp_path / "asset.bin"
    out = update_check.download_release_asset(
        "https://example.invalid/asset.bin", dest,
        progress_cb=lambda c, t, label: progress.append((c, t)),
    )

    assert out == dest
    assert dest.read_bytes() == b"".join(chunks)
    assert progress[0] == (16, 40)
    assert progress[-1] == (40, 40)
    # 완료 후 .part 는 남아 있지 않아야 함
    assert not dest.with_suffix(dest.suffix + ".part").exists()


def test_download_release_asset_cleans_part_on_failure(monkeypatch, tmp_path):
    from core import update_check

    class _Boom(_FakeResponse):
        def iter_content(self, chunk_size=0):
            yield b"X" * 8
            raise RuntimeError("network died mid-stream")

    def fake_get(url, **kwargs):
        return _Boom([])

    monkeypatch.setattr(update_check.requests, "get", fake_get)

    dest = tmp_path / "asset.bin"
    import pytest
    with pytest.raises(RuntimeError):
        update_check.download_release_asset(
            "https://example.invalid/asset.bin", dest,
        )
    assert not dest.exists()
    assert not dest.with_suffix(dest.suffix + ".part").exists()


def test_download_release_asset_uses_fallback_total_when_no_content_length(
    monkeypatch, tmp_path,
):
    from core import update_check

    def fake_get(url, **kwargs):
        return _FakeResponse([b"x" * 50], content_length=0)

    monkeypatch.setattr(update_check.requests, "get", fake_get)

    seen: list[tuple[int, int]] = []
    dest = tmp_path / "asset.bin"
    update_check.download_release_asset(
        "https://example.invalid/asset.bin", dest,
        progress_cb=lambda c, t, label: seen.append((c, t)),
        fallback_total=50,
    )
    assert seen[-1] == (50, 50)


def test_check_for_updates_populates_asset_fields(monkeypatch, tmp_path):
    """assets 가 있는 GitHub API 응답을 모킹해서 UpdateInfo 가 채워지는지."""
    from core import update_check

    monkeypatch.setattr(update_check, "CACHE_FILE", tmp_path / "cache.json")
    monkeypatch.setattr(update_check, "APP_VERSION", "1.0.0")
    # check_for_updates 내부에서도 APP_VERSION 을 참조하므로 from-import 갈음:
    monkeypatch.setitem(update_check.__dict__, "APP_VERSION", "1.0.0")

    api_payload = {
        "tag_name": "v1.2.6",
        "name": "v1.2.6 — auto update",
        "html_url": "https://github.com/x/y/releases/tag/v1.2.6",
        "body": "release notes",
        "assets": [
            _asset("초록등대회원관리_v1.2.6_portable.zip", size=12345),
            _asset("초록등대회원관리_v1.2.6_setup.exe", size=67890),
        ],
    }

    class _ApiResp:
        ok = True
        status_code = 200
        def json(self):
            return api_payload

    def fake_get(url, **kwargs):
        return _ApiResp()

    monkeypatch.setattr(update_check.requests, "get", fake_get)

    info = update_check.check_for_updates(force=True)
    assert info is not None
    assert info.latest == "v1.2.6"
    assert info.is_installer is True
    assert info.asset_name.endswith("_setup.exe")
    assert info.asset_size == 67890
    assert info.download_url.startswith("https://example.invalid/")


def test_check_for_updates_without_assets_still_returns_info(monkeypatch, tmp_path):
    from core import update_check

    monkeypatch.setattr(update_check, "CACHE_FILE", tmp_path / "cache.json")
    monkeypatch.setattr(update_check, "APP_VERSION", "1.0.0")
    monkeypatch.setitem(update_check.__dict__, "APP_VERSION", "1.0.0")

    class _ApiResp:
        ok = True
        status_code = 200
        def json(self):
            return {
                "tag_name": "v1.2.6",
                "name": "v1.2.6",
                "html_url": "https://github.com/x/y/releases/tag/v1.2.6",
                "body": "",
                "assets": [],
            }

    monkeypatch.setattr(update_check.requests, "get", lambda *a, **kw: _ApiResp())
    info = update_check.check_for_updates(force=True)
    assert info is not None
    assert info.download_url == ""
    assert info.asset_name == ""
    assert info.is_installer is False
