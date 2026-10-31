"""`gflow run --resume` regenerates exactly the rows whose inputs changed; `--redo` forces one.

Drives the real CLI four times against one project with explicit per-row outputs: a fresh
run, an unchanged resume that must submit nothing, a resume after one prompt was edited
that must submit only that row, and a `--redo` of the other row.

Needs GFLOW_CLI_E2E_PROFILE and GFLOW_CLI_E2E_PROJECT. Costs 4 images of daily quota,
0 credits, and creates no project.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from .test_image_uuid_ref_e2e import _run_gflow

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_image]


def _write(config: Path, project: str, out: Path, second_prompt: str) -> None:
    config.write_text(
        json.dumps(
            {
                "project": project,
                "prompts": [
                    {
                        "text": "a small red paper kite on a white table",
                        "aspect_ratio": "1:1",
                        "output": str(out / "k1.png"),
                    },
                    {"text": second_prompt, "aspect_ratio": "1:1", "output": str(out / "k2.png")},
                ],
            }
        ),
        encoding="utf-8",
    )


def test_e2e_resume_follows_the_ledger(e2e_env: dict[str, str], tmp_path: Path) -> None:
    project = os.environ.get("GFLOW_CLI_E2E_PROJECT", "").strip()
    if not project:
        pytest.skip("GFLOW_CLI_E2E_PROJECT must name an existing Flow project id")
    config, out = tmp_path / "run.json", tmp_path / "gen"
    argv = ["run", "--config", str(config)]

    _write(config, project, out, "a small green paper boat on a white table")
    first = _run_gflow(argv, e2e_env)
    assert first.returncode == 0, first.stderr[-1500:]
    # One hidden ledger per saved frame; the extension follows the bytes (#96).
    assert len(list(out.glob(".*.gflow.json"))) == 2, sorted(p.name for p in out.iterdir())

    unchanged = _run_gflow([*argv, "--resume"], e2e_env)
    assert unchanged.returncode == 0, unchanged.stderr[-1500:]
    assert "0/2 succeeded" in unchanged.stdout and "2 resumed" in unchanged.stdout, (
        unchanged.stdout[-800:]
    )

    _write(config, project, out, "a small green paper boat on a white table, seen from above")
    edited = _run_gflow([*argv, "--resume"], e2e_env)
    assert edited.returncode == 0, edited.stderr[-1500:]
    assert "1/2 succeeded" in edited.stdout and "1 resumed" in edited.stdout, edited.stdout[-800:]

    redone = _run_gflow([*argv, "--redo", "k1"], e2e_env)
    assert redone.returncode == 0, redone.stderr[-1500:]
    assert "1/2 succeeded" in redone.stdout and "1 resumed" in redone.stdout, redone.stdout[-800:]
