from pathlib import Path

import pytest

from gflow_cli.mcp.tools import confine_to_output_dir


@pytest.mark.parametrize("bad", ["../x.png", "a/../../x.png", "/etc/cron.d/x", ".."])
def test_rejects_escape(tmp_path: Path, bad: str):
    with pytest.raises(ValueError):
        confine_to_output_dir(bad, root=tmp_path)


def test_rejects_symlink_escape(tmp_path: Path):
    (tmp_path / "ln").symlink_to("/tmp")
    with pytest.raises(ValueError):
        confine_to_output_dir("ln/x.png", root=tmp_path)


@pytest.mark.parametrize("ok", ["x.png", "sub/dir/x.png", "."])
def test_accepts_inside(tmp_path: Path, ok: str):
    assert confine_to_output_dir(ok, root=tmp_path).is_relative_to(tmp_path.resolve())
