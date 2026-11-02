"""`gflow image t2i/i2i --upscale 2k` saves Flow's 2K beside the image it just made."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from gflow_cli.cli import main as cli_main
from gflow_cli.errors import UpscaleUnavailableError
from tests.cli.test_cli_image import _make_generated_image, _make_i2i_client, _make_t2i_client

_MEDIA = str(uuid.UUID(int=7))
_PROJECT = "22222222-3333-4444-5555-666666666666"


def _with_upsample(client: MagicMock, error: Exception | None = None) -> MagicMock:

    async def upsample(*, out_path: Path, **_: Any) -> Path:
        if error is not None:
            raise error
        saved = out_path.with_suffix(".jpg")
        saved.write_bytes(b"\xff\xd8\xff 2k")
        return saved

    client.upsample_image = AsyncMock(side_effect=upsample)
    return client


def _invoke(client: MagicMock, tmp_path: Path, args: list[str]) -> Any:
    with (
        patch("gflow_cli.cli_image.FlowApiClient", return_value=client),
        patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
        patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
    ):
        return CliRunner().invoke(cli_main, args, catch_exceptions=False)


def test_t2i_json_lists_the_2k_saved_beside_the_image(tmp_path: Path) -> None:
    out = tmp_path / "k1.png"

    result = _invoke(
        _with_upsample(_make_t2i_client(images=[_make_generated_image(media_name=_MEDIA)])),
        tmp_path,
        [
            "image",
            "t2i",
            "a cat",
            "--project",
            _PROJECT,
            "-o",
            str(out),
            "--upscale",
            "2K",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["upscaled"] == [str(tmp_path / "k1.2k.jpg")]
    assert data["images"][0]["local_path"] == str(out)
    assert out.is_file()


def test_t2i_json_without_the_flag_has_no_upscaled_key(tmp_path: Path) -> None:
    client = _make_t2i_client(images=[_make_generated_image(media_name=_MEDIA)])

    result = _invoke(client, tmp_path, ["image", "t2i", "a cat", "--json", "--out", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "upscaled" not in json.loads(result.stdout)


@pytest.mark.parametrize("as_json", [False, True], ids=["table", "json"])
def test_i2i_keeps_the_image_when_the_upscale_fails(tmp_path: Path, as_json: bool) -> None:
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\n")
    client = _with_upsample(
        _make_i2i_client(images=[_make_generated_image(media_name=_MEDIA)]),
        error=UpscaleUnavailableError("2K menu item disabled"),
    )
    out = tmp_path / "k2.png"

    result = _invoke(
        client,
        tmp_path,
        [
            "image",
            "i2i",
            "a cat",
            "--ref",
            str(ref),
            "--project",
            _PROJECT,
            "-o",
            str(out),
            "--upscale",
            "2k",
            *(["--json"] if as_json else []),
        ],
    )

    assert result.exit_code == 0, result.output
    assert out.is_file() and not list(tmp_path.glob("k2.2k.*"))
    assert f"gflow image upscale {_MEDIA} --scale 2k --project {_PROJECT}" in result.stderr
    if as_json:
        assert json.loads(result.stdout)["upscaled"] == []
