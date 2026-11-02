"""`--upscale 2k` saves Flow's real 2K beside each generated image, on every door.

`gflow run` draws two frames with the flag, so the second row is submitted after the first
row's upscale left the page on its detail view. A resume after deleting one 2K adds only
that 2K and generates nothing. `gflow image t2i` and the MCP `gflow_generate_image` twin
each return their 2K path. `gflow image upscale` reaches the project's oldest recorded
image, which the grid's first load does not list.

Needs GFLOW_CLI_E2E_PROFILE and GFLOW_CLI_E2E_PROJECT. Costs 4 images of daily quota and 6
upscales (free), 0 credits, and creates no project.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from pathlib import Path

import pytest
from PIL import Image

from gflow_cli.config import reset_settings
from gflow_cli.mcp import tools as mcp_tools

from .test_image_uuid_ref_e2e import _run_gflow

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_image]


def _project() -> str:
    pid = os.environ.get("GFLOW_CLI_E2E_PROJECT", "").strip()
    if not pid:
        pytest.skip("GFLOW_CLI_E2E_PROJECT must name an existing Flow project id")
    return pid


def _assert_2k_of(one_k: Path, two_k: Path) -> None:
    with Image.open(one_k) as small, Image.open(two_k) as big:
        assert big.width >= 2 * small.width - 2, (small.size, big.size)
        assert abs(big.width / big.height - small.width / small.height) < 0.02


def test_e2e_run_saves_every_frames_2k_and_resume_adds_only_the_missing_one(
    e2e_env: dict[str, str], tmp_path: Path
) -> None:
    gen = tmp_path / "gen"
    cfg = tmp_path / "frames.json"
    cfg.write_text(
        json.dumps(
            {
                "project": _project(),
                "prompts": [
                    {
                        "text": "a red paper boat on a calm pond, morning light",
                        "aspect_ratio": "9:16",
                        "output": str(gen / "k1.png"),
                    },
                    {
                        "text": "a blue kite over a green hill, clear sky",
                        "aspect_ratio": "9:16",
                        "output": str(gen / "k2.png"),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    argv = ["run", "--config", str(cfg), "--upscale", "2k"]

    first = _run_gflow(argv, e2e_env)
    assert first.returncode == 0, first.stderr[-1500:]
    assert "2/2 succeeded" in first.stdout, first.stdout[-800:]
    for name in ("k1", "k2"):
        (one_k,) = [p for p in gen.glob(f"{name}.*") if ".2k." not in p.name]
        (two_k,) = gen.glob(f"{name}.2k.*")
        _assert_2k_of(one_k, two_k)

    (old,) = gen.glob("k1.2k.*")
    old.unlink()
    again = _run_gflow([*argv, "--resume"], e2e_env)
    assert again.returncode == 0, again.stderr[-1500:]
    assert "0/2 succeeded" in again.stdout and "2 resumed" in again.stdout, again.stdout[-800:]
    assert list(gen.glob("k1.2k.*")), again.stdout[-800:]


def test_e2e_cli_t2i_json_returns_the_2k(e2e_env: dict[str, str], tmp_path: Path) -> None:
    out = tmp_path / "boat.png"
    proc = _run_gflow(
        [
            "image",
            "t2i",
            "a wooden rowing boat on a misty lake",
            "--aspect",
            "9:16",
            "--project",
            _project(),
            "-o",
            str(out),
            "--upscale",
            "2k",
            "--json",
        ],
        e2e_env,
    )

    assert proc.returncode == 0, proc.stderr[-1500:]
    data = json.loads(proc.stdout)
    (two_k,) = data["upscaled"]
    _assert_2k_of(Path(data["images"][0]["local_path"]), Path(two_k))


def test_e2e_mcp_image_returns_the_2k(
    e2e_env: dict[str, str], e2e_profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del e2e_profile_dir  # selects the real authenticated gflow home
    for key in ("GFLOW_CLI_DB_PATH", "GFLOW_CLI_OUTPUT_DIR"):
        monkeypatch.setenv(key, e2e_env[key])
    reset_settings()

    result = asyncio.run(
        mcp_tools.gflow_generate_image(
            prompt="a brass lantern on a stone step at dusk",
            aspect="9:16",
            profile=e2e_env["GFLOW_CLI_PROFILE"],
            project=_project(),
            upscale="2k",
            wait=True,
        )
    )

    assert result["status"] == "completed", result
    assert "upscale_warning" not in result, result
    (two_k,) = result["upscaled"]
    (one_k,) = result["files"]
    _assert_2k_of(Path(one_k), Path(two_k))


def _oldest_recorded_image(env: Mapping[str, str], project: str) -> str:
    catalog = {k: v for k, v in env.items() if k != "GFLOW_CLI_DB_PATH"}
    profile = env["GFLOW_CLI_PROFILE"]
    listed = _run_gflow(
        ["data", "list", "images", "--profile", profile, "--limit", "1000", "--json"], catalog
    )
    assert listed.returncode == 0, listed.stderr[-800:]
    rows = [json.loads(line) for line in listed.stdout.splitlines() if line.strip()]
    mine = sorted(
        (r for r in rows if r.get("project_id") == project and r.get("media_id")),
        key=lambda r: r["created_at"],
    )
    if not mine:
        pytest.skip(f"the real catalog records no image of {profile} in {project}")
    return mine[0]["media_id"]


def test_e2e_upscale_reaches_an_image_the_grid_does_not_list(
    e2e_env: dict[str, str], tmp_path: Path
) -> None:
    """The grid lists only its first load, so an image made days earlier was "not found"
    (exit 23); its detail view opens it by id."""
    project = _project()
    media = _oldest_recorded_image(e2e_env, project)

    proc = _run_gflow(
        ["image", "upscale", media, "--scale", "2k", "--project", project, "--out", str(tmp_path)],
        e2e_env,
    )

    assert proc.returncode == 0, proc.stderr[-1500:]
    (two_k,) = tmp_path.rglob(f"{media}_2k.*")
    with Image.open(two_k) as big:
        assert min(big.size) >= 1500, big.size
