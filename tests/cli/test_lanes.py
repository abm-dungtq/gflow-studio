"""`--profiles a,b` splits a config run across profiles, side by side, in one command.

Projects ran two profiles of one Google account in parallel with a shell script that cut
the config into per-profile files and started one `gflow` per file 25 s apart. gflow now
deals the rows itself, says which profile ran each row, and stops every lane when one hits
an error that ends its session (WAF, auth, quota), instead of letting the other lane keep
submitting on a throttled account.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from gflow_cli.cli import main as cli_main
from gflow_cli.errors import ConfigurationError, WafRejectionError
from gflow_cli.lanes import parse_profiles, split_rows
from tests.cli.test_cli_run import _fake_generated_image, _patched_client_factory, _write_config
from tests.cli.test_video_batch import _make_fake_video_client, _write_frame, _write_video_config


@pytest.fixture(autouse=True)
def _no_stagger() -> Iterator[None]:
    with patch("gflow_cli.lanes.LANE_STAGGER_S", 0.0):
        yield


# ---------------------------------------------------------------------------
# Dealing rows
# ---------------------------------------------------------------------------


def test_rows_are_dealt_round_robin_and_an_idle_profile_gets_no_lane() -> None:
    assert split_rows(["a", "b"], [0, 1, 2, 3, 4]) == [("a", (0, 2, 4)), ("b", (1, 3))]
    assert split_rows(["a", "b", "c"], [0]) == [("a", (0,))]


@pytest.mark.parametrize("raw", ["a,,b", "a,a", " , "])
def test_a_blank_or_repeated_profile_is_refused(raw: str) -> None:
    with pytest.raises(ConfigurationError):
        parse_profiles(raw)


# ---------------------------------------------------------------------------
# gflow video batch --profiles
# ---------------------------------------------------------------------------


def _clips(tmp_path: Path, n: int) -> Path:
    frames = [_write_frame(tmp_path / f"k{i}.png") for i in range(n)]
    return _write_video_config(
        tmp_path,
        {
            "videos": [
                {"start": str(f), "prompt": f"clip {i}", "output": str(tmp_path / f"gen/v{i}.mp4")}
                for i, f in enumerate(frames)
            ]
        },
    )


def _video_lanes(tmp_path: Path, cfg: Path, fakes: dict[str, MagicMock], *extra: str) -> Any:
    with (
        patch(
            "gflow_cli.video_batch.FlowApiClient",
            side_effect=lambda **kw: fakes[kw["profile_dir"].name],
        ),
        patch("gflow_cli.cli_video._make_provider_dir", side_effect=lambda n: tmp_path / n),
    ):
        return CliRunner().invoke(
            cli_main,
            ["video", "batch", "--config", str(cfg), "--jitter", "0", *extra],
            catch_exceptions=False,
        )


def _prompts(fake: MagicMock) -> list[str]:
    return [c.kwargs["req"].prompt for c in fake.generate_video.call_args_list]


def test_video_rows_run_on_their_lane_and_the_json_says_where(tmp_path: Path) -> None:
    cfg = _clips(tmp_path, 4)
    fakes = {"a": _make_fake_video_client(tmp_path), "b": _make_fake_video_client(tmp_path)}

    result = _video_lanes(tmp_path, cfg, fakes, "--profiles", "a,b", "--project", "P1", "--json")

    assert result.exit_code == 0, result.output
    assert _prompts(fakes["a"]) == ["clip 0", "clip 2"]
    assert _prompts(fakes["b"]) == ["clip 1", "clip 3"]
    payload = json.loads(result.stdout)
    assert payload["lanes"] == {"a": [0, 2], "b": [1, 3]}
    assert [r["profile"] for r in payload["results"]] == ["a", "b", "a", "b"]
    assert all((tmp_path / f"gen/v{i}.mp4").is_file() for i in range(4))


def test_a_waf_on_one_lane_stops_every_lane(tmp_path: Path) -> None:
    cfg = _clips(tmp_path, 4)
    fakes = {
        "a": _make_fake_video_client(
            tmp_path, side_effect=lambda *_a: WafRejectionError(detail="403")
        ),
        "b": _make_fake_video_client(tmp_path),
    }

    result = _video_lanes(tmp_path, cfg, fakes, "--profiles", "a,b", "--project", "P1", "--json")

    assert result.exit_code == 10, result.output
    assert _prompts(fakes["a"]) == ["clip 0"]
    assert _prompts(fakes["b"]) == []
    rows = {r["index"]: r for r in json.loads(result.stdout)["results"]}
    assert rows[0]["status"] == "fail"
    assert all(rows[i]["status"] == "skipped" for i in (1, 2, 3))
    assert "lane a" in rows[1]["error"]


def test_video_lanes_resume_deals_only_the_missing_rows(tmp_path: Path) -> None:
    cfg = _clips(tmp_path, 4)
    first = {"a": _make_fake_video_client(tmp_path), "b": _make_fake_video_client(tmp_path)}
    _video_lanes(tmp_path, cfg, first, "--profiles", "a,b", "--project", "P1")
    (tmp_path / "gen/v3.mp4").unlink()
    again = {"a": _make_fake_video_client(tmp_path), "b": _make_fake_video_client(tmp_path)}

    result = _video_lanes(tmp_path, cfg, again, "--profiles", "a,b", "--project", "P1", "--resume")

    assert result.exit_code == 0, result.output
    assert _prompts(again["a"]) == ["clip 3"]
    assert again["b"].__aenter__.call_count == 0


@pytest.mark.parametrize(
    "extra",
    [
        ("--profiles", "a,b"),
        ("--profiles", "a,b", "--profile", "a", "--project", "P1"),
        ("--profiles", "a,a", "--project", "P1"),
    ],
    ids=["no-project", "with-profile", "repeated"],
)
def test_video_lanes_config_mistakes_are_exit_11(tmp_path: Path, extra: tuple[str, ...]) -> None:
    cfg = _clips(tmp_path, 2)
    fakes = {"a": _make_fake_video_client(tmp_path), "b": _make_fake_video_client(tmp_path)}

    with patch("gflow_cli.cli_video.default_project", return_value=None):
        result = _video_lanes(tmp_path, cfg, fakes, *extra)

    assert result.exit_code == 11, result.output
    assert fakes["a"].generate_video.call_count == 0


# ---------------------------------------------------------------------------
# gflow run --profiles
# ---------------------------------------------------------------------------

_SEEDS = itertools.count(5000, 50)


def _run_lanes(
    tmp_path: Path, cfg: Path, *extra: str, waf_on: str | None = None
) -> tuple[Any, list[str]]:
    seen: list[str] = []

    def factory(**kw: Any) -> MagicMock:
        name = kw["profile_dir"].name
        seen.append(name)
        start = next(_SEEDS)
        images = [_fake_generated_image(seed=s) for s in range(start, start + 50)]
        return _patched_client_factory(
            image_for_prompt=AsyncMock(
                side_effect=WafRejectionError(detail="403") if name == waf_on else images
            )
        )

    with (
        patch("gflow_cli.cli_run.FlowApiClient", side_effect=factory),
        patch("gflow_cli.cli_run._make_provider_dir", side_effect=lambda n: tmp_path / n),
    ):
        result = CliRunner().invoke(
            cli_main, ["run", "--config", str(cfg), *extra], catch_exceptions=False
        )
    return result, seen


def test_run_lanes_make_every_frame_and_show_the_lane(tmp_path: Path) -> None:
    cfg = _write_config(
        tmp_path,
        {
            "prompts": [
                {"text": f"frame {i}", "output": str(tmp_path / f"gen/k{i}.png")} for i in range(3)
            ]
        },
    )

    result, seen = _run_lanes(tmp_path, cfg, "--profiles", "a,b", "--project", "P1")

    assert result.exit_code == 0, result.output
    assert sorted(seen) == ["a", "b"]
    assert "lane a: rows 0, 2" in result.output and "lane b: rows 1" in result.output
    assert all((tmp_path / "gen").glob(f"k{i}.*") for i in range(3))
    assert "3/3 succeeded" in result.output


def test_run_lanes_refuse_rows_chained_by_batch_ref(tmp_path: Path) -> None:
    cfg = _write_config(
        tmp_path,
        {"prompts": [{"text": "parent"}, {"text": "child", "ref": "batch:0"}]},
    )

    result, seen = _run_lanes(tmp_path, cfg, "--profiles", "a,b", "--project", "P1")

    assert result.exit_code == 11, result.output
    assert "batch:N" in result.output
    assert seen == []


def test_a_waf_on_one_image_lane_stops_every_lane(tmp_path: Path) -> None:
    cfg = _write_config(
        tmp_path,
        {
            "prompts": [
                {"text": f"frame {i}", "output": str(tmp_path / f"gen/k{i}.png")} for i in range(4)
            ]
        },
    )

    result, seen = _run_lanes(tmp_path, cfg, "--profiles", "a,b", "--project", "P1", waf_on="a")

    assert result.exit_code == 10, result.output
    # Lane b never opened a browser: lane a stopped before b's turn came.
    assert seen == ["a"]
    assert "0/4 succeeded" in result.output and "3 skipped" in result.output
