"""Headless generation sends the user agent headed Chrome sends.

Real Chrome headless advertises ``HeadlessChrome/<v>`` in ``navigator.userAgent`` while
its UA-CH brands still read "Google Chrome" (docs/superpowers/spikes/
2026-10-28-headless-generation.md). gflow overrides the string with the headed form,
built from the installed Chrome's own version, so a hidden run carries no headless
token.
"""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.browser_manager import chrome_version, headed_user_agent
from gflow_cli.config import Settings


def _mac_chrome(tmp_path: Path, version: str) -> Path:
    contents = tmp_path / "Google Chrome.app" / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    binary = contents / "MacOS" / "Google Chrome"
    binary.write_text("")
    (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": version}))
    return binary


def test_chrome_version_reads_the_mac_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    assert chrome_version(_mac_chrome(tmp_path, "156.0.8078.74")) == "156.0.8078.74"


def test_chrome_version_reads_the_windows_version_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.platform", "win32")
    (tmp_path / "156.0.8078.74").mkdir()
    (tmp_path / "SetupMetrics").mkdir()
    binary = tmp_path / "chrome.exe"
    binary.write_text("")
    assert chrome_version(binary) == "156.0.8078.74"


def test_chrome_version_is_none_when_unreadable(tmp_path: Path) -> None:
    assert chrome_version(tmp_path / "missing") is None


@pytest.mark.parametrize(
    ("platform", "token"),
    [
        ("darwin", "Macintosh; Intel Mac OS X 10_15_7"),
        ("win32", "Windows NT 10.0; Win64; x64"),
        ("linux", "X11; Linux x86_64"),
    ],
)
def test_headed_user_agent_matches_chrome_reduced_form(
    monkeypatch: pytest.MonkeyPatch, platform: str, token: str
) -> None:
    monkeypatch.setattr("sys.platform", platform)
    assert headed_user_agent("156.0.8078.74") == (
        f"Mozilla/5.0 ({token}) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/156.0.0.0 Safari/537.36"
    )


def test_headless_client_overrides_the_user_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    binary = _mac_chrome(tmp_path, "156.0.8078.74")
    monkeypatch.setattr("gflow_cli.api.client.resolved_chrome_binary", lambda: str(binary))
    client = FlowApiClient(profile_dir=tmp_path, settings=Settings(), headless=True)

    ua = client._persistent_context_kwargs()["user_agent"]  # noqa: SLF001

    assert "HeadlessChrome" not in ua
    assert "Chrome/156.0.0.0" in ua


def test_headed_client_keeps_chrome_own_user_agent(tmp_path: Path) -> None:
    client = FlowApiClient(profile_dir=tmp_path, settings=Settings(), headless=False)
    assert "user_agent" not in client._persistent_context_kwargs()  # noqa: SLF001


def test_headless_client_without_a_readable_version_keeps_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("gflow_cli.api.client.resolved_chrome_binary", lambda: None)
    client = FlowApiClient(profile_dir=tmp_path, settings=Settings(), headless=True)
    assert "user_agent" not in client._persistent_context_kwargs()  # noqa: SLF001
