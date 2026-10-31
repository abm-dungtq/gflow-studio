"""`gflow run --profiles A,B` makes one config's rows on two profiles side by side.

Two browsers on two profiles of one Google account generate into one project at once, the
way projects used to do with a shell script that started one `gflow` per profile.

Needs GFLOW_CLI_E2E_PROFILES (two profile names, comma-separated, signed in to the same
Google account) and GFLOW_CLI_E2E_PROJECT (a project that account owns). Costs 2 images of
daily quota, 0 credits, and creates no project.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from .test_image_uuid_ref_e2e import _run_gflow

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_image]


def test_e2e_two_profiles_make_one_row_each(e2e_env: dict[str, str], tmp_path: Path) -> None:
    profiles = os.environ.get("GFLOW_CLI_E2E_PROFILES", "").strip()
    project = os.environ.get("GFLOW_CLI_E2E_PROJECT", "").strip()
    if len(profiles.split(",")) != 2 or not project:
        pytest.skip("GFLOW_CLI_E2E_PROFILES=a,b and GFLOW_CLI_E2E_PROJECT must be set")
    first, second = (name.strip() for name in profiles.split(","))
    config, out = tmp_path / "run.json", tmp_path / "gen"
    config.write_text(
        json.dumps(
            {
                "prompts": [
                    {
                        "text": "a small red paper kite on a white table",
                        "aspect_ratio": "1:1",
                        "output": str(out / "k0.png"),
                    },
                    {
                        "text": "a small green paper boat on a white table",
                        "aspect_ratio": "1:1",
                        "output": str(out / "k1.png"),
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    result = _run_gflow(
        ["run", "--config", str(config), "--profiles", profiles, "--project", project], e2e_env
    )

    assert result.returncode == 0, result.stderr[-1500:]
    assert f"lane {first}: rows 0" in result.stdout, result.stdout[-1500:]
    assert f"lane {second}: rows 1" in result.stdout, result.stdout[-1500:]
    assert "2/2 succeeded" in result.stdout, result.stdout[-800:]
    assert len(list(out.glob("k*.*"))) == 2, sorted(p.name for p in out.iterdir())
