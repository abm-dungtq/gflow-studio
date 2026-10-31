"""`gflow run --cast cast.json`: `@Name` in a row pulls in that character's reference image.

Projects kept one approved image per character and pasted its path into every row that
showed the character; a row that forgot it drew a stranger. A cast file names each image
once, and a mention the cast cannot serve is reported instead of silently drawing without
a reference.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from gflow_cli.cast_file import Cast
from gflow_cli.cli import main as cli_main
from gflow_cli.cli_run import BatchConfig
from gflow_cli.errors import ConfigurationError
from tests.cli.test_cli_run import _patched_client_factory
from tests.cli.test_ref_aliases import _VALID_PNG


def _cast(tmp_path: Path, members: dict[str, Any]) -> Path:
    for raw in members.values():
        if isinstance(raw, str):
            image = tmp_path / raw
            image.parent.mkdir(parents=True, exist_ok=True)
            image.write_bytes(_VALID_PNG)
    path = tmp_path / "cast.json"
    path.write_text(json.dumps(members), encoding="utf-8")
    return path


def _config(
    tmp_path: Path, rows: list[dict[str, Any]], cast_path: Path
) -> tuple[BatchConfig, Cast]:
    cfg_file = tmp_path / "run.json"
    cfg_file.write_text(json.dumps({"prompts": rows}), encoding="utf-8")
    cast_file = Cast.load(cast_path)
    return BatchConfig.from_json_path(cfg_file, cast_file=cast_file), cast_file


def test_a_mentioned_character_brings_its_image_as_a_named_reference(tmp_path: Path) -> None:
    cast_path = _cast(tmp_path, {"Ngân": "chars/ngan.png", "OngTu": "chars/tu.png"})

    cfg, cast_file = _config(
        tmp_path, [{"text": "@ngân rót trà cho @OngTu, rồi @Ngân cười"}], cast_path
    )

    row = cfg.prompts[0]
    assert row.refs == (
        str((tmp_path / "chars/ngan.png").resolve()),
        str((tmp_path / "chars/tu.png").resolve()),
    )
    assert row.text == "reference image 1 rót trà cho reference image 2, rồi reference image 1 cười"
    assert cast_file.warnings == []


def test_the_rows_own_refs_come_first_and_win_a_shared_name(tmp_path: Path) -> None:
    cast_path = _cast(tmp_path, {"Ngan": "chars/ngan.png", "Lai": "chars/lai.png"})
    (tmp_path / "own").mkdir()
    (tmp_path / "own/ngan.png").write_bytes(_VALID_PNG)

    cfg, _ = _config(
        tmp_path,
        [{"text": "@Ngan chải tóc cho @Lai", "ref": {"name": "Ngan", "path": "own/ngan.png"}}],
        cast_path,
    )

    assert cfg.prompts[0].refs == (
        str((tmp_path / "own/ngan.png").resolve()),
        str((tmp_path / "chars/lai.png").resolve()),
    )
    assert cfg.prompts[0].text == "reference image 1 chải tóc cho reference image 2"


def test_mentions_the_cast_cannot_serve_are_reported_once_per_name(tmp_path: Path) -> None:
    cast_path = _cast(tmp_path, {"Ngan": "chars/ngan.png", "Lai": None})

    cfg, cast_file = _config(
        tmp_path,
        [{"text": "@Lai đứng sau @BaSau"}, {"text": "bến sông, @lai và @BaSau"}],
        cast_path,
    )

    assert cfg.prompts[0].text == "@Lai đứng sau @BaSau"
    assert cfg.prompts[0].refs == ()
    assert cast_file.warnings == [
        "@Lai has no reference image in the cast yet (prompts 0, 1).",
        "@BaSau is not in the cast (prompts 0, 1); Flow will look it up as a saved character.",
    ]


def test_a_character_named_without_at_is_reported_but_a_common_word_is_not(
    tmp_path: Path,
) -> None:
    cast_path = _cast(tmp_path, {"Tâm": "chars/tam.png", "Ngan": "chars/ngan.png"})

    cfg, cast_file = _config(
        tmp_path,
        [{"text": "Tâm ngồi bên bến, tâm trạng nặng nề"}, {"text": "@Ngan nhìn Ngan trong gương"}],
        cast_path,
    )

    assert cfg.prompts[0].refs == ()
    assert cast_file.warnings == [
        "Tâm is named without @ (prompts 0); no reference image attached."
    ]


def test_cast_images_over_the_models_cap_are_refused_naming_the_cast(tmp_path: Path) -> None:
    cast_path = _cast(tmp_path, {"A": "c/a.png", "B": "c/b.png"})
    (tmp_path / "own").mkdir()
    for name in ("x", "y"):
        (tmp_path / f"own/{name}.png").write_bytes(_VALID_PNG)

    with pytest.raises(ConfigurationError, match=r"4 reference images \(2 from the cast\)"):
        _config(
            tmp_path,
            [{"text": "@A và @B", "model": "nano2-lite", "refs": ["own/x.png", "own/y.png"]}],
            cast_path,
        )


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"text": "@Ngan", "ref": "own/x.png", "refs": ["own/x.png"]}, "both 'ref' and 'refs'"),
        ({"text": "@Ngan", "refs": "own/x.png"}, "non-empty list"),
    ],
    ids=["ref-and-refs", "refs-not-a-list"],
)
def test_a_malformed_row_keeps_its_own_error(
    tmp_path: Path, row: dict[str, Any], expected: str
) -> None:
    cast_path = _cast(tmp_path, {"Ngan": "chars/ngan.png"})
    (tmp_path / "own").mkdir()
    (tmp_path / "own/x.png").write_bytes(_VALID_PNG)

    with pytest.raises(ConfigurationError, match=expected):
        _config(tmp_path, [row], cast_path)


@pytest.mark.parametrize(
    ("members", "expected"),
    [
        ({"Ngan": "chars/missing.png"}, "does not exist"),
        ({"Ngan": None, "ngan": None}, "listed twice"),
        ({"Ông Tư": None}, "must match"),
        ({"Ngan": 3}, "image path or null"),
    ],
    ids=["missing-image", "same-name", "space-in-name", "not-a-path"],
)
def test_a_bad_cast_is_refused(tmp_path: Path, members: dict[str, Any], expected: str) -> None:
    path = tmp_path / "cast.json"
    path.write_text(json.dumps(members), encoding="utf-8")

    with pytest.raises(ConfigurationError, match=expected):
        Cast.load(path)


def _run(tmp_path: Path, cast_path: Path, text: str) -> Any:
    cfg = tmp_path / "run.json"
    cfg.write_text(json.dumps({"prompts": [{"text": text}]}), encoding="utf-8")
    fake = _patched_client_factory()
    with (
        patch("gflow_cli.cli_run.FlowApiClient", return_value=fake) as client_cls,
        patch("gflow_cli.cli_run._resolve_profile", return_value="p"),
        patch("gflow_cli.cli_run._make_provider_dir", return_value=tmp_path / "profiles/p"),
    ):
        result = CliRunner().invoke(
            cli_main,
            [
                "run",
                "--config",
                str(cfg),
                "--cast",
                str(cast_path),
                "--output-dir",
                str(tmp_path / "out"),
            ],
            catch_exceptions=False,
        )
    return result, client_cls


def test_run_prints_what_the_cast_could_not_serve(tmp_path: Path) -> None:
    cast_path = _cast(tmp_path, {"Lai": None})

    result, _ = _run(tmp_path, cast_path, "@Lai chải tóc trước gương bể")

    assert result.exit_code == 0, result.output
    assert "@Lai has no reference image in the cast yet (prompts 0)" in result.output


def test_run_with_a_broken_cast_exits_11_before_any_browser(tmp_path: Path) -> None:
    path = tmp_path / "cast.json"
    path.write_text(json.dumps({"Ngan": "chars/missing.png"}), encoding="utf-8")

    result, client_cls = _run(tmp_path, path, "@Ngan đứng ở bến")

    assert result.exit_code == 11, result.output
    assert client_cls.call_count == 0
