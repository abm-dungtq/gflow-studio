"""`gflow run --upscale 2k` saves Flow's 2K beside every frame, so video starts from 2K.

Frames came back at Flow's 1K (768x1376 for 9:16), too soft to start a video from, and
upscaling meant one `gflow image upscale` per file with a browser launch each. The upscale
now happens in the run's own session; the 1K stays as the row's output (the ledger and
`--resume` follow it) and a resumed row gets only the upscale it is missing.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from gflow_cli.api.dto import GeneratedImage
from gflow_cli.cli import main as cli_main
from gflow_cli.errors import UpscaleUnavailableError
from tests.cli.test_cli_run import _patched_client_factory, _write_config

_PROJECT = "22222222-3333-4444-5555-666666666666"
_SEEDS = itertools.count(9000, 50)
_JPEG_2K = b"\xff\xd8\xff\xe0" + b"2" * 64


@pytest.fixture(autouse=True)
def _no_stagger() -> Iterator[None]:
    with patch("gflow_cli.lanes.LANE_STAGGER_S", 0.0):
        yield


def _image(seed: int) -> GeneratedImage:
    return GeneratedImage(
        media_name=str(uuid.UUID(int=seed)),
        workflow_id=f"wf-{seed}",
        seed=seed,
        prompt="any",
        model_name_type="NARWHAL",
        aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
        fife_url=f"https://lh3.googleusercontent.com/{seed}",
        dimensions=(768, 1376),
    )


def _client(upscale_error: Exception | None = None) -> MagicMock:
    start = next(_SEEDS)
    client = _patched_client_factory(
        image_for_prompt=AsyncMock(side_effect=[_image(s) for s in range(start, start + 50)])
    )

    async def upsample(*, out_path: Path, **_: Any) -> Path:
        if upscale_error is not None:
            raise upscale_error
        saved = out_path.with_suffix(".jpg")
        saved.write_bytes(_JPEG_2K)
        return saved

    client.upsample_image = AsyncMock(side_effect=upsample)
    return client


def _run(tmp_path: Path, cfg: Path, *extra: str, **client_kw: Any) -> tuple[Any, list[MagicMock]]:
    made: list[MagicMock] = []

    def factory(**_: Any) -> MagicMock:
        made.append(_client(**client_kw))
        return made[-1]

    with (
        patch("gflow_cli.cli_run.FlowApiClient", side_effect=factory),
        patch("gflow_cli.cli_run._resolve_profile", return_value="meo-muop"),
        patch("gflow_cli.cli_run._make_provider_dir", side_effect=lambda n: tmp_path / n),
    ):
        result = CliRunner().invoke(
            cli_main,
            ["run", "--config", str(cfg), "--project", _PROJECT, *extra],
            catch_exceptions=False,
        )
    return result, made


def _frames(tmp_path: Path, n: int = 2) -> Path:
    return _write_config(
        tmp_path,
        {
            "prompts": [
                {"text": f"frame {i}", "output": str(tmp_path / f"gen/k{i}.png")}
                for i in range(1, n + 1)
            ]
        },
    )


def _generated(clients: list[MagicMock]) -> int:
    return sum(c.generate_image.await_count for c in clients)


def _upscaled(clients: list[MagicMock]) -> int:
    return sum(c.upsample_image.await_count for c in clients)


def test_every_frame_gets_its_2k_beside_it_and_keeps_its_1k(tmp_path: Path) -> None:
    result, clients = _run(tmp_path, _frames(tmp_path), "--upscale", "2k")

    assert result.exit_code == 0, result.output
    assert "2/2 succeeded" in result.output
    gen = tmp_path / "gen"
    assert sorted(p.name for p in gen.iterdir() if not p.name.startswith(".")) == [
        "k1.2k.jpg",
        "k1.png",
        "k2.2k.jpg",
        "k2.png",
    ]
    assert (gen / "k1.2k.jpg").read_bytes() == _JPEG_2K
    calls = clients[0].upsample_image.await_args_list
    assert {c.kwargs["project_id"] for c in calls} == {_PROJECT}


def test_resume_adds_only_the_missing_2k_without_generating_again(tmp_path: Path) -> None:
    cfg = _frames(tmp_path)
    first, _ = _run(tmp_path, cfg)
    assert first.exit_code == 0, first.output
    (tmp_path / "gen/k1.2k.jpg").write_bytes(b"already there")

    result, clients = _run(tmp_path, cfg, "--resume", "--upscale", "2k")

    assert result.exit_code == 0, result.output
    assert "2 resumed" in result.output
    assert _generated(clients) == 0
    assert _upscaled(clients) == 1
    assert (tmp_path / "gen/k1.2k.jpg").read_bytes() == b"already there"
    assert (tmp_path / "gen/k2.2k.jpg").read_bytes() == _JPEG_2K


def test_a_redone_frame_drops_the_2k_of_its_old_picture(tmp_path: Path) -> None:
    cfg = _frames(tmp_path)
    first, _ = _run(tmp_path, cfg, "--upscale", "2k")
    assert first.exit_code == 0, first.output

    result, _ = _run(tmp_path, cfg, "--redo", "k1")

    assert result.exit_code == 0, result.output
    assert not list((tmp_path / "gen").glob("k1.2k.*"))
    assert (tmp_path / "gen/k2.2k.jpg").is_file()


def test_a_failed_upscale_keeps_the_frame_and_prints_the_retry(tmp_path: Path) -> None:
    result, _ = _run(
        tmp_path,
        _frames(tmp_path, n=1),
        "--upscale",
        "2k",
        upscale_error=UpscaleUnavailableError("2K menu item disabled"),
    )

    assert result.exit_code == 0, result.output
    assert "1/1 succeeded" in result.output
    assert "gflow image upscale" in result.output and "--scale 2k" in result.output
    assert (tmp_path / "gen/k1.png").is_file()


def test_lanes_deal_a_resumed_frame_only_to_add_its_2k(tmp_path: Path) -> None:
    cfg = _frames(tmp_path, n=3)
    first, _ = _run(tmp_path, cfg, "--profiles", "a,b")
    assert first.exit_code == 0, first.output
    for name in ("k1", "k3"):
        (tmp_path / f"gen/{name}.2k.jpg").write_bytes(b"done")

    result, clients = _run(tmp_path, cfg, "--profiles", "a,b", "--resume", "--upscale", "2k")

    assert result.exit_code == 0, result.output
    assert "3 resumed" in result.output
    assert _generated(clients) == 0
    assert _upscaled(clients) == 1
    assert (tmp_path / "gen/k2.2k.jpg").read_bytes() == _JPEG_2K
