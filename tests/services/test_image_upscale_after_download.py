"""`--upscale 2k`: a saved image gets Flow's 2K beside it, never in place of it.

Flow's image upscale re-draws the picture (small glyphs change), so the 1K a row was
approved on stays where it is and the 2K lands next to it as ``<stem>.2k.<ext>``. A failed
upscale keeps the 1K and says how to retry; it never fails the row.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.image_upscale import TargetResolution
from gflow_cli.errors import UpscaleUnavailableError
from gflow_cli.services.image_upscale import (
    drop_stale_upscales,
    upscale_saved_images,
    upscaled_copy,
)

_MEDIA = "11111111-2222-3333-4444-555555555555"
_PROJECT = "22222222-3333-4444-5555-666666666666"
_JPEG_2K = b"\xff\xd8\xff\xe0" + b"2" * 64


def _client(side_effect: Any = None) -> MagicMock:
    client = MagicMock()

    async def fake(
        *, media_id: str, project_id: str, target_resolution: TargetResolution, out_path: Path
    ) -> Path:
        assert (media_id, project_id) == (_MEDIA, _PROJECT)
        assert target_resolution is TargetResolution.RES_2K
        saved = out_path.with_suffix(".jpg")  # Flow's bytes decide the extension
        saved.write_bytes(_JPEG_2K)
        return saved

    client.upsample_image = AsyncMock(side_effect=side_effect or fake)
    return client


@pytest.mark.asyncio
async def test_the_2k_lands_beside_the_1k(
    tmp_path: Path,
) -> None:
    one_k = tmp_path / "k1.png"
    one_k.write_bytes(b"\x89PNG-1k")
    client = _client()

    made, warnings = await upscale_saved_images(
        client, [(_MEDIA, one_k)], project_id=_PROJECT, scale="2k"
    )

    assert warnings == []
    assert made == [tmp_path / "k1.2k.jpg"]
    assert (tmp_path / "k1.2k.jpg").read_bytes() == _JPEG_2K
    assert one_k.read_bytes() == b"\x89PNG-1k"
    assert upscaled_copy(one_k, "2k") == tmp_path / "k1.2k.jpg"


@pytest.mark.asyncio
async def test_an_existing_2k_is_kept_when_only_missing_ones_are_asked_for(
    tmp_path: Path,
) -> None:
    one_k = tmp_path / "k1.jpg"
    one_k.write_bytes(b"1k")
    (tmp_path / "k1.2k.jpg").write_bytes(b"old 2k")
    client = _client()

    made, warnings = await upscale_saved_images(
        client, [(_MEDIA, one_k)], project_id=_PROJECT, scale="2k", only_missing=True
    )

    assert (made, warnings) == ([], [])
    client.upsample_image.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_upscale_keeps_the_1k_and_names_the_retry(tmp_path: Path) -> None:
    one_k = tmp_path / "k1.jpg"
    one_k.write_bytes(b"1k")
    client = _client(side_effect=UpscaleUnavailableError("2K menu item disabled"))

    made, warnings = await upscale_saved_images(
        client, [(_MEDIA, one_k)], project_id=_PROJECT, scale="2k"
    )

    assert made == []
    assert one_k.read_bytes() == b"1k"
    assert not list(tmp_path.glob("k1.2k.*"))
    (warning,) = warnings
    assert "k1.jpg" in warning and "UpscaleUnavailableError" in warning
    assert f"gflow image upscale {_MEDIA} --scale 2k --project {_PROJECT}" in warning


@pytest.mark.asyncio
async def test_an_image_without_a_known_media_id_is_reported_not_sent(tmp_path: Path) -> None:
    one_k = tmp_path / "k1.jpg"
    one_k.write_bytes(b"1k")
    client = _client()

    made, warnings = await upscale_saved_images(
        client, [("resumed-0-0", one_k)], project_id=_PROJECT, scale="2k"
    )

    assert made == []
    assert len(warnings) == 1 and "media id" in warnings[0]
    client.upsample_image.assert_not_awaited()


def test_regenerating_a_row_drops_the_upscales_of_its_old_image(tmp_path: Path) -> None:
    one_k = tmp_path / "k1.png"
    for name in ("k1.2k.jpg", "k1.4k.png", "k10.2k.jpg", "k1.jpg"):
        (tmp_path / name).write_bytes(b"x")

    dropped = drop_stale_upscales(one_k)

    assert sorted(p.name for p in dropped) == ["k1.2k.jpg", "k1.4k.png"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["k1.jpg", "k10.2k.jpg"]


def test_a_bracketed_name_matches_only_its_own_upscale(tmp_path: Path) -> None:
    for name in ("shot1_0.2k.png", "shot[1]_0.2k.png", "shot[1]_0.png"):
        (tmp_path / name).write_bytes(b"x")

    assert upscaled_copy(tmp_path / "shot[1]_0.png", "2k") == tmp_path / "shot[1]_0.2k.png"
    drop_stale_upscales(tmp_path / "shot[1]_0.png")
    assert (tmp_path / "shot1_0.2k.png").is_file()
