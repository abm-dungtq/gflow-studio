"""Remember what each config-runner output was made from, so ``--resume`` redoes only edits.

Next to every file a ``gflow run`` or ``gflow video batch`` row saves, a hidden
``.<name>.gflow.json`` holds a fingerprint of the row's inputs: its fields (prompt, model,
aspect, ...) and the bytes of every local file it read (reference images, start and end
frames). On ``--resume`` a row whose sidecar disagrees is generated again; a file with no
sidecar (made before the ledger existed, or copied in by hand) keeps the older rule, so
upgrading never re-bills a finished episode.

Only the inputs are compared, never the output's own bytes: projects crop, pad or re-encode
their outputs after gflow saves them, and that is not a reason to spend credits again.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import cast

import structlog

from gflow_cli.errors import ConfigurationError

logger = structlog.get_logger(__name__)

_FIELD = "inputs_sha256"


def sidecar_path(path: Path) -> Path:
    """The hidden ledger file that sits next to ``path``."""
    return path.with_name(f".{path.name}.gflow.json")


def fingerprint(fields: Mapping[str, object], files: Iterable[str]) -> str:
    """Hash ``fields`` plus the bytes of each entry in ``files`` that is a local file.

    An entry that is not a file (``batch:N``, a Flow media id) counts by its text.
    """
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True, default=str).encode("utf-8"))
    for spec in files:
        path = Path(spec)
        content = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else spec
        digest.update(f"\0{spec}\0{content}".encode())
    return digest.hexdigest()


def record(paths: Iterable[Path], inputs: str) -> None:
    """Write ``inputs`` next to each of ``paths``. Never raises: the output is what matters."""
    for path in paths:
        try:
            sidecar_path(path).write_text(json.dumps({_FIELD: inputs}), encoding="utf-8")
        except OSError as exc:
            logger.warning("run_ledger.write_failed", path=str(path), error=str(exc))


def is_stale(path: Path, inputs: str) -> bool:
    """True only when ``path`` has a readable sidecar recording different inputs."""
    try:
        data: object = json.loads(sidecar_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and cast("dict[str, object]", data).get(_FIELD) != inputs


def check_redo_names(redo: Iterable[str], outputs: Iterable[Path]) -> set[str]:
    """Return ``redo`` as a set, refusing a name that matches none of ``outputs`` (exit 11)."""
    names = set(redo)
    known = {key for path in outputs for key in redo_keys(path)}
    if unknown := sorted(names - known):
        raise ConfigurationError(
            detail=f"--redo names no row's output: {', '.join(unknown)}",
            remediation_hint="Use an output file name (k3.png) or its stem (k3).",
        )
    return names


def redo_keys(path: Path) -> set[str]:
    """The names ``--redo`` accepts for ``path``: full path as written, file name, stem."""
    return {str(path), path.name, path.stem}
