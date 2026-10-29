"""GHSA-hqfj-4jww-76f2: MCP ``output`` / ``out_dir`` stay inside the configured output dir."""

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from gflow_cli.config import reset_settings
from gflow_cli.mcp import tools as tools_mod
from gflow_cli.mcp.tools import confine_to_output_dir


def _link_dir(link: Path, target: Path) -> None:
    # A junction needs no privilege on Windows; a symlink does.
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("bad", ["../x.png", "a/../../x.png", ".."])
def test_rejects_relative_escape(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        confine_to_output_dir(bad, root=tmp_path / "out")


def test_rejects_absolute_path_elsewhere(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        confine_to_output_dir(str(tmp_path / "elsewhere" / "x.png"), root=tmp_path / "out")


def test_rejects_a_link_that_leads_out(tmp_path: Path) -> None:
    root, outside = tmp_path / "out", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    _link_dir(root / "ln", outside)
    with pytest.raises(ValueError):
        confine_to_output_dir("ln/x.png", root=root)


@pytest.mark.parametrize("ok", ["x.png", "sub/dir/x.png", "."])
def test_accepts_inside(tmp_path: Path, ok: str) -> None:
    root = tmp_path / "out"
    assert confine_to_output_dir(ok, root=root).is_relative_to(root.resolve())


def test_accepts_an_absolute_path_inside(tmp_path: Path) -> None:
    root = tmp_path / "out"
    assert confine_to_output_dir(str(root / "x.png"), root=root) == (root / "x.png").resolve()


@pytest.fixture()
def output_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(out))
    reset_settings()
    yield out
    reset_settings()


async def _call(tool: str, output: str) -> tuple[dict[str, Any], AsyncMock]:
    run = AsyncMock(return_value={"status": "completed"})
    with (
        patch.object(tools_mod, "_run_generation_task", run),
        patch.object(tools_mod, "_resolve_and_validate_profile", return_value="default"),
    ):
        result = await getattr(tools_mod, tool)(prompt="a dog", output=output)
    return result, run


_TOOLS = ["gflow_generate_image", "gflow_generate_video"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TOOLS)
@pytest.mark.parametrize("bad", ["../escaped/x.bin", "a/../../x.bin"])
async def test_tool_refuses_an_escaping_output_before_queueing(
    output_dir: Path, tool: str, bad: str
) -> None:
    result, run = await _call(tool, bad)
    assert result["status"] == "error"
    assert result["error"]["title"] == "Invalid Output"
    run.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TOOLS)
async def test_tool_refuses_an_absolute_output_outside(
    output_dir: Path, tmp_path: Path, tool: str
) -> None:
    result, run = await _call(tool, str(tmp_path / "elsewhere" / "x.bin"))
    assert result["error"]["title"] == "Invalid Output"
    run.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TOOLS)
@pytest.mark.parametrize("names_a_dir", ["", ".", "clips"])
async def test_tool_refuses_an_output_naming_a_directory_in_the_output_dir(
    output_dir: Path, tool: str, names_a_dir: str
) -> None:
    # Resolved against the output dir, not the process CWD (#1114's check used the CWD).
    (output_dir / "clips").mkdir()
    result, run = await _call(tool, names_a_dir)
    assert result["error"]["title"] == "Invalid Output"
    run.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TOOLS)
async def test_tool_queues_a_relative_output_resolved_inside(output_dir: Path, tool: str) -> None:
    _, run = await _call(tool, "sub/x.bin")
    run.assert_called_once()
    assert run.call_args.kwargs["payload"]["output_file"] == str(
        (output_dir / "sub" / "x.bin").resolve()
    )


_MEDIA = "11111111-2222-3333-4444-555555555555"


@pytest.mark.asyncio
async def test_download_media_refuses_an_escaping_out_dir(output_dir: Path) -> None:
    with patch.object(tools_mod, "download_media", AsyncMock()) as download:
        result = await tools_mod.gflow_download_media(media_id=_MEDIA, out_dir="../escaped")
    assert result["error"]["title"] == "Invalid out_dir"
    download.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["gflow_upscale_image", "gflow_upscale_video"])
async def test_upscale_refuses_an_escaping_out_dir(output_dir: Path, tool: str) -> None:
    with (
        patch.object(tools_mod, "FlowApiClient") as client,
        patch.object(tools_mod, "_resolve_and_validate_profile", return_value="default"),
    ):
        result = await getattr(tools_mod, tool)(
            media_id=_MEDIA, project=_MEDIA, out_dir="../escaped"
        )
    assert result["error"]["title"] == "Invalid out_dir"
    client.assert_not_called()
