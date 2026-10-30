"""The headed generation window is minimized, not just placed off-screen.

Chrome pulls a window placed at ``--window-position=-30000,-30000`` back onto the
nearest display, so on a desk with a second monitor to the left the window landed
fully visible on it (measured 2026-10-30, macOS, two 2560x1440 displays: requested
-30000,-30000, got -2560,103). Re-placing it via CDP still leaves a ~40 px corner on
screen. Minimizing hides it completely while the tab keeps reporting
``visibilityState == "visible"``. Opening a tab or ``bring_to_front`` restores the
window, so both re-minimize.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gflow_cli.browser_manager import (
    bring_to_front_keep_hidden,
    hides_generation_window,
    keep_window_hidden,
    minimize_window,
)


class _FakeCdp:
    def __init__(self, page: _FakePage) -> None:
        self._page = page
        self.detached = False

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._page.calls.append((method, params))
        if self._page.cdp_error is not None:
            raise self._page.cdp_error
        if method == "Browser.getWindowForTarget":
            return {"windowId": 7, "bounds": {"windowState": self._page.window_state}}
        if method == "Browser.setWindowBounds":
            assert params is not None
            bounds = params["bounds"]
            if "windowState" in bounds:
                self._page.window_state = bounds["windowState"]
            else:
                # Chrome refuses a position on a minimized window.
                assert self._page.window_state == "normal"
        return {}

    async def detach(self) -> None:
        self.detached = True


class _FakeContext:
    def __init__(self) -> None:
        self.pages: list[_FakePage] = []
        self.handlers: dict[str, Callable[[Any], Any]] = {}
        self.sessions: list[_FakeCdp] = []

    async def new_cdp_session(self, page: _FakePage) -> _FakeCdp:
        cdp = _FakeCdp(page)
        self.sessions.append(cdp)
        return cdp

    def on(self, event: str, handler: Callable[[Any], Any]) -> None:
        self.handlers[event] = handler


class _FakePage:
    def __init__(self, context: _FakeContext, *, hidden_when_minimized: bool = False) -> None:
        self.context = context
        context.pages.append(self)
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.window_state = "normal"
        self.hidden_when_minimized = hidden_when_minimized
        self.cdp_error: Exception | None = None

    async def evaluate(self, script: str) -> str:
        assert script == "document.visibilityState"
        if self.hidden_when_minimized and self.window_state == "minimized":
            return "hidden"
        return "visible"

    async def bring_to_front(self) -> None:
        # Measured: Chrome restores a minimized window when a tab is fronted.
        self.window_state = "normal"


@pytest.mark.parametrize(
    ("mode", "headless", "expected"),
    [
        ("offscreen", False, True),
        ("background", False, True),
        ("normal", False, False),
        ("offscreen", True, False),
        ("OFFSCREEN", False, True),
    ],
)
def test_hides_generation_window(mode: str, headless: bool, expected: bool) -> None:
    assert hides_generation_window(mode, headless=headless) is expected


async def test_minimize_window_minimizes_the_page_window() -> None:
    page = _FakePage(_FakeContext())

    assert await minimize_window(page) is True  # type: ignore[arg-type]

    assert page.window_state == "minimized"
    assert ("Browser.setWindowBounds", {"windowId": 7, "bounds": {"windowState": "minimized"}}) in (
        page.calls
    )
    assert all(s.detached for s in page.context.sessions)


async def test_minimize_window_parks_the_window_in_a_corner_first() -> None:
    """If a new tab restores the window before it re-minimizes, only a corner shows."""
    page = _FakePage(_FakeContext())

    await minimize_window(page)  # type: ignore[arg-type]

    bounds = [p["bounds"] for m, p in page.calls if m == "Browser.setWindowBounds" and p]
    assert bounds == [{"left": -30000, "top": 30000}, {"windowState": "minimized"}]


async def test_minimize_window_restores_when_the_page_would_go_hidden() -> None:
    """A platform that hides a minimized tab would stall Flow; keep the window up."""
    page = _FakePage(_FakeContext(), hidden_when_minimized=True)

    assert await minimize_window(page) is False  # type: ignore[arg-type]

    assert page.window_state == "normal"


async def test_minimize_window_never_raises() -> None:
    page = _FakePage(_FakeContext())
    page.cdp_error = RuntimeError("Browser domain unavailable")

    assert await minimize_window(page) is False  # type: ignore[arg-type]


async def test_keep_window_hidden_minimizes_now_and_every_new_tab() -> None:
    ctx = _FakeContext()
    first = _FakePage(ctx)

    await keep_window_hidden(ctx)  # type: ignore[arg-type]
    assert first.window_state == "minimized"

    # Measured: opening a tab restores the window; the "page" handler re-minimizes.
    second = _FakePage(ctx)
    ctx.handlers["page"](second)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert second.window_state == "minimized"


async def test_keep_window_hidden_gives_up_when_minimizing_hides_the_page() -> None:
    ctx = _FakeContext()
    page = _FakePage(ctx, hidden_when_minimized=True)

    await keep_window_hidden(ctx)  # type: ignore[arg-type]

    assert page.window_state == "normal"
    assert "page" not in ctx.handlers


async def test_bring_to_front_keep_hidden_re_minimizes_a_hidden_window() -> None:
    page = _FakePage(_FakeContext())
    page.window_state = "minimized"

    await bring_to_front_keep_hidden(page)  # type: ignore[arg-type]

    assert page.window_state == "minimized"


async def test_bring_to_front_keep_hidden_leaves_a_shown_window_shown() -> None:
    page = _FakePage(_FakeContext())

    await bring_to_front_keep_hidden(page)  # type: ignore[arg-type]

    assert page.window_state == "normal"


def test_every_generation_launch_site_hides_its_window() -> None:
    """Both generation launch sites must minimize; a third must opt in or be listed."""
    import gflow_cli

    src = Path(gflow_cli.__file__).parent
    offenders = [
        py.relative_to(src).as_posix()
        for py in src.rglob("*.py")
        if "generation_window_config(" in py.read_text(encoding="utf-8")
        and py.name != "browser_manager.py"
        and "keep_window_hidden(" not in py.read_text(encoding="utf-8")
    ]
    assert not offenders, f"generation launch sites that never minimize: {offenders}"


def test_no_raw_bring_to_front_in_generation_paths() -> None:
    """``page.bring_to_front()`` restores a minimized window; use the keep-hidden twin."""
    import gflow_cli

    src = Path(gflow_cli.__file__).parent
    offenders = [
        py.relative_to(src).as_posix()
        for py in src.rglob("*.py")
        if py.name != "browser_manager.py" and ".bring_to_front()" in py.read_text(encoding="utf-8")
    ]
    assert not offenders, f"raw bring_to_front() restores the hidden window: {offenders}"
