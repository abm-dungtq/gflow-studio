"""`gflow_generate_image(upscale="2k")` is the MCP twin of `gflow image t2i/i2i --upscale`."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from gflow_cli.mcp.tools import _TokenBucket, gflow_generate_image


async def _call(**kw: object) -> tuple[dict[str, object], object]:
    with (
        patch("gflow_cli.mcp.tools._rate_limiter", _TokenBucket(capacity=8, refill_rate=0.0)),
        patch("gflow_cli.mcp.tools._resolve_and_validate_profile", return_value="default"),
        patch("gflow_cli.mcp.tools._run_generation_task") as run,
    ):
        run.return_value = {"status": "queued"}
        result = await gflow_generate_image(prompt="a cat", wait=False, **kw)  # type: ignore[arg-type]
    return result, run


@pytest.mark.asyncio
async def test_the_scale_reaches_the_queued_payload_lowercased() -> None:
    result, run = await _call(upscale="2K")

    assert result["status"] != "error", result
    assert run.call_args.kwargs["payload"]["upscale"] == "2k"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_no_scale_writes_no_payload_key() -> None:
    _, run = await _call()

    assert "upscale" not in run.call_args.kwargs["payload"]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_an_unknown_scale_is_refused_before_anything_is_queued() -> None:
    result, run = await _call(upscale="1k")

    assert result["status"] == "error"
    assert result["error"]["title"] == "Invalid upscale"  # type: ignore[index]
    run.assert_not_called()  # type: ignore[attr-defined]
