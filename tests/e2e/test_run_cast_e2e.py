"""`gflow run --cast` gives a row the reference image of the character it mentions.

Drives the real CLI against one project: a first run draws a character sheet, a second run
writes `@Cao` with a cast file pointing at that sheet (uploaded and referenced, nothing
passed by hand), a resume after adding an unused character to the cast submits nothing,
and a resume after replacing the character's image redraws the row that mentions it.

Needs GFLOW_CLI_E2E_PROFILE and GFLOW_CLI_E2E_PROJECT. Costs 3 images of daily quota,
0 credits, and creates no project.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from .test_image_uuid_ref_e2e import _run_gflow

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_image]


def _config(path: Path, project: str, rows: list[dict[str, str]]) -> None:
    path.write_text(json.dumps({"project": project, "prompts": rows}), encoding="utf-8")


def test_e2e_cast_attaches_the_mentioned_characters_image(
    e2e_env: dict[str, str], tmp_path: Path
) -> None:
    project = os.environ.get("GFLOW_CLI_E2E_PROJECT", "").strip()
    if not project:
        pytest.skip("GFLOW_CLI_E2E_PROJECT must name an existing Flow project id")
    chars, gen = tmp_path / "chars", tmp_path / "gen"

    sheet = tmp_path / "sheet.json"
    _config(
        sheet,
        project,
        [
            {
                "text": "a small orange cartoon fox standing, plain white background",
                "aspect_ratio": "1:1",
                "output": str(chars / "cao.png"),
            }
        ],
    )
    drawn = _run_gflow(["run", "--config", str(sheet)], e2e_env)
    assert drawn.returncode == 0, drawn.stderr[-1500:]
    (fox,) = chars.glob("cao.*")

    cast = tmp_path / "cast.json"
    cast.write_text(json.dumps({"Cao": f"chars/{fox.name}"}), encoding="utf-8")
    scene = tmp_path / "scene.json"
    _config(
        scene,
        project,
        [
            {
                "text": "@Cao sitting on a red wooden chair, plain white background",
                "aspect_ratio": "1:1",
                "output": str(gen / "k1.png"),
            }
        ],
    )
    argv = ["run", "--config", str(scene), "--cast", str(cast)]

    first = _run_gflow(argv, e2e_env)
    assert first.returncode == 0, first.stderr[-1500:]
    assert "1/1 succeeded" in first.stdout, first.stdout[-800:]
    (ledger,) = gen.glob(".k1.*.gflow.json")
    assert ledger.is_file()

    cast.write_text(json.dumps({"Cao": f"chars/{fox.name}", "Lai": None}), encoding="utf-8")
    unchanged = _run_gflow([*argv, "--resume"], e2e_env)
    assert unchanged.returncode == 0, unchanged.stderr[-1500:]
    assert "0/1 succeeded" in unchanged.stdout and "1 resumed" in unchanged.stdout, (
        unchanged.stdout[-800:]
    )

    (made,) = gen.glob("k1.*")
    shutil.copyfile(made, fox)
    replaced = _run_gflow([*argv, "--resume"], e2e_env)
    assert replaced.returncode == 0, replaced.stderr[-1500:]
    assert "1/1 succeeded" in replaced.stdout, replaced.stdout[-800:]
