"""`--resume` regenerates exactly the rows whose inputs changed, and `--redo` forces one.

`gflow run` and `gflow video batch` used to resume a row whenever its output file existed,
so an edited prompt or a re-drawn start frame kept the stale picture, and redoing one shot
meant deleting files by hand. Each saved output now carries a hidden sidecar with a
fingerprint of what it was made from.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from gflow_cli.api.image import ImageRef
from gflow_cli.cli import main as cli_main
from gflow_cli.run_ledger import fingerprint, is_stale, record, sidecar_path
from tests.cli.test_cli_run import (
    _VALID_PNG,
    _fake_generated_image,
    _patched_client_factory,
    _write_config,
)
from tests.cli.test_video_batch import (
    _VALID_MP4,
    _make_fake_video_client,
    _write_frame,
    _write_video_config,
)

# ---------------------------------------------------------------------------
# The ledger itself
# ---------------------------------------------------------------------------


def test_fingerprint_follows_fields_and_file_bytes(tmp_path: Path) -> None:
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"one")
    base = fingerprint({"prompt": "a", "model": "nano2"}, [str(ref)])

    assert fingerprint({"model": "nano2", "prompt": "a"}, [str(ref)]) == base
    assert fingerprint({"prompt": "b", "model": "nano2"}, [str(ref)]) != base
    ref.write_bytes(b"two")
    assert fingerprint({"prompt": "a", "model": "nano2"}, [str(ref)]) != base


def test_a_file_without_a_sidecar_is_never_stale(tmp_path: Path) -> None:
    out = tmp_path / "k1.png"
    out.write_bytes(_VALID_PNG)

    assert is_stale(out, "abc") is False

    record([out], "abc")
    assert sidecar_path(out).name == ".k1.png.gflow.json"
    assert is_stale(out, "abc") is False
    assert is_stale(out, "def") is True


def test_an_unreadable_sidecar_counts_as_no_sidecar(tmp_path: Path) -> None:
    out = tmp_path / "k1.png"
    out.write_bytes(_VALID_PNG)
    sidecar_path(out).write_text("{not json", encoding="utf-8")

    assert is_stale(out, "abc") is False


# ---------------------------------------------------------------------------
# gflow video batch
# ---------------------------------------------------------------------------


def _video_batch(tmp_path: Path, cfg: Path, *extra: str) -> tuple[object, object]:
    fake_client = _make_fake_video_client(tmp_path)
    with (
        patch("gflow_cli.video_batch.FlowApiClient", return_value=fake_client),
        patch("gflow_cli.cli_video._resolve_profile", return_value="meo-muop"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path / "prof"),
    ):
        result = CliRunner().invoke(
            cli_main,
            ["video", "batch", "--config", str(cfg), "--jitter", "0", *extra],
            catch_exceptions=False,
        )
    return result, fake_client


def _two_clips(tmp_path: Path, prompt2: str = "clip 2") -> tuple[Path, Path, Path]:
    f1 = _write_frame(tmp_path / "k1.png")
    f2 = _write_frame(tmp_path / "k2.png")
    out1, out2 = tmp_path / "gen" / "v1.mp4", tmp_path / "gen" / "v2.mp4"
    cfg = _write_video_config(
        tmp_path,
        {
            "videos": [
                {"start": str(f1), "prompt": "clip 1", "output": str(out1)},
                {"start": str(f2), "prompt": prompt2, "output": str(out2)},
            ]
        },
    )
    return cfg, out1, out2


def test_video_batch_records_what_each_clip_was_made_from(tmp_path: Path) -> None:
    cfg, out1, out2 = _two_clips(tmp_path)

    result, _ = _video_batch(tmp_path, cfg)

    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
    for out in (out1, out2):
        assert json.loads(sidecar_path(out).read_text(encoding="utf-8"))["inputs_sha256"]


def test_video_resume_submits_nothing_when_no_input_changed(tmp_path: Path) -> None:
    cfg, _, _ = _two_clips(tmp_path)
    _video_batch(tmp_path, cfg)

    result, fake = _video_batch(tmp_path, cfg, "--resume")

    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
    assert fake.generate_video.call_count == 0  # type: ignore[attr-defined]


def test_video_resume_regenerates_only_the_row_whose_prompt_changed(tmp_path: Path) -> None:
    cfg, _, _ = _two_clips(tmp_path)
    _video_batch(tmp_path, cfg)
    cfg, _, _ = _two_clips(tmp_path, prompt2="clip 2, slower")

    result, fake = _video_batch(tmp_path, cfg, "--resume")

    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
    calls = fake.generate_video.call_args_list  # type: ignore[attr-defined]
    assert [c.kwargs["req"].prompt for c in calls] == ["clip 2, slower"]


def test_video_resume_regenerates_a_clip_whose_start_frame_was_redrawn(tmp_path: Path) -> None:
    cfg, _, _ = _two_clips(tmp_path)
    _video_batch(tmp_path, cfg)
    (tmp_path / "k1.png").write_bytes(_VALID_PNG + b"redrawn")

    result, fake = _video_batch(tmp_path, cfg, "--resume")

    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
    calls = fake.generate_video.call_args_list  # type: ignore[attr-defined]
    assert [c.kwargs["req"].prompt for c in calls] == ["clip 1"]


def test_video_resume_keeps_a_clip_made_before_the_ledger(tmp_path: Path) -> None:
    cfg, out1, _ = _two_clips(tmp_path)
    out1.parent.mkdir(parents=True, exist_ok=True)
    out1.write_bytes(_VALID_MP4)

    result, fake = _video_batch(tmp_path, cfg, "--resume")

    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
    calls = fake.generate_video.call_args_list  # type: ignore[attr-defined]
    assert [c.kwargs["req"].prompt for c in calls] == ["clip 2"]
    # The kept clip is adopted, so a later edit to its row is noticed.
    assert sidecar_path(out1).is_file()


def test_video_redo_regenerates_the_named_clip_and_keeps_the_rest(tmp_path: Path) -> None:
    cfg, _, _ = _two_clips(tmp_path)
    _video_batch(tmp_path, cfg)

    result, fake = _video_batch(tmp_path, cfg, "--redo", "v1")

    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
    calls = fake.generate_video.call_args_list  # type: ignore[attr-defined]
    assert [c.kwargs["req"].prompt for c in calls] == ["clip 1"]


def test_video_redo_of_an_unknown_name_is_a_config_error(tmp_path: Path) -> None:
    cfg, _, _ = _two_clips(tmp_path)

    result, fake = _video_batch(tmp_path, cfg, "--redo", "v9")

    assert result.exit_code == 11, result.output  # type: ignore[attr-defined]
    assert "v9" in result.output  # type: ignore[attr-defined]
    assert fake.generate_video.call_count == 0  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# gflow run
# ---------------------------------------------------------------------------


_SEEDS = itertools.count(1000, 50)


def _run(tmp_path: Path, cfg: Path, *extra: str) -> tuple[object, object]:
    # Flow returns a fresh media id for every generation; a second run must not reuse one.
    start = next(_SEEDS)
    fake_client = _patched_client_factory(
        image_for_prompt=AsyncMock(
            side_effect=[_fake_generated_image(seed=s) for s in range(start, start + 50)]
        )
    )
    fake_client.upload_reference = AsyncMock(
        return_value=ImageRef(name="ref-media", display_name="cuong", in_project=True)
    )
    with (
        patch("gflow_cli.cli_run.FlowApiClient", return_value=fake_client),
        patch("gflow_cli.cli_run._resolve_profile", return_value="meo-muop"),
        patch("gflow_cli.cli_run._make_provider_dir", return_value=tmp_path / "prof"),
    ):
        result = CliRunner().invoke(
            cli_main, ["run", "--config", str(cfg), *extra], catch_exceptions=False
        )
    return result, fake_client


def _two_frames(tmp_path: Path, ref_bytes: bytes = _VALID_PNG) -> tuple[Path, Path, Path]:
    ref = tmp_path / "cuong.png"
    ref.write_bytes(ref_bytes)
    out1, out2 = tmp_path / "gen" / "k1.png", tmp_path / "gen" / "k2.png"
    cfg = _write_config(
        tmp_path,
        {
            "prompts": [
                {"text": "first frame", "output": str(out1)},
                {"text": "second frame", "refs": [str(ref)], "output": str(out2)},
            ]
        },
    )
    return cfg, out1, out2


def test_run_resume_regenerates_a_row_whose_reference_image_changed(tmp_path: Path) -> None:
    cfg, out1, out2 = _two_frames(tmp_path)
    first, _ = _run(tmp_path, cfg)
    assert first.exit_code == 0, first.output  # type: ignore[attr-defined]
    assert sidecar_path(out1).is_file() and sidecar_path(out2).is_file()
    cfg, _, _ = _two_frames(tmp_path, ref_bytes=_VALID_PNG + b"new pose")

    result, fake = _run(tmp_path, cfg, "--resume")

    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
    calls = fake.generate_image.call_args_list  # type: ignore[attr-defined]
    assert [c.kwargs["req"].prompt for c in calls] == ["second frame"]


def test_run_redo_regenerates_the_named_frame(tmp_path: Path) -> None:
    cfg, _, _ = _two_frames(tmp_path)
    _run(tmp_path, cfg)

    result, fake = _run(tmp_path, cfg, "--redo", "k1.png")

    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
    calls = fake.generate_image.call_args_list  # type: ignore[attr-defined]
    assert [c.kwargs["req"].prompt for c in calls] == ["first frame"]


def test_image_batch_outside_gflow_run_writes_no_sidecars(tmp_path: Path) -> None:
    """Only the config runners that resume keep a ledger; other outputs stay as they were."""
    from gflow_cli import image_batch

    assert image_batch.run_image_batch.__kwdefaults__["ledger"] is False
