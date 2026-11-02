"""Post-download 2K/4K copy of a generated image, shared by t2i/i2i, ``gflow run`` and the worker.

Flow's image upscale re-draws the picture rather than resizing it (small glyphs change), so
the image a row produced stays where it is and the upscale is written beside it as
``<stem>.2k.<ext>``. A failed upscale never fails the generation: the original is kept and
the warning names the command that retries it.
"""

from __future__ import annotations

import glob
from typing import TYPE_CHECKING, Any

import structlog

from gflow_cli.api.image_upscale import TargetResolution
from gflow_cli.api.video import is_media_uuid

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

logger = structlog.get_logger(__name__)

UPSCALE_SCALES = ("2k", "4k")


def upscaled_copy(path: Path, scale: str) -> Path | None:
    """The ``<stem>.<scale>.*`` file already beside ``path``, if any."""
    found = sorted(
        p for p in path.parent.glob(f"{glob.escape(path.stem)}.{scale}.*") if p.is_file()
    )
    return found[0] if found else None


def drop_stale_upscales(path: Path) -> list[Path]:
    """Delete the upscales of the image ``path`` held before it was generated again."""
    dropped: list[Path] = []
    for scale in UPSCALE_SCALES:
        while (old := upscaled_copy(path, scale)) is not None:
            old.unlink()
            dropped.append(old)
    if dropped:
        logger.info("image.stale_upscales_dropped", files=[str(p) for p in dropped])
    return dropped


async def upscale_saved_images(
    client: Any,
    images: Sequence[tuple[str, Path]],
    *,
    project_id: str,
    scale: str,
    only_missing: bool = False,
) -> tuple[list[Path], list[str]]:
    """Write Flow's ``scale`` copy beside each ``(media_id, saved_path)``.

    ``only_missing`` skips an image whose copy already exists (a resumed row). Returns the
    files written and one warning per image that could not be upscaled.
    """
    resolution = TargetResolution.from_cli(scale)
    made: list[Path] = []
    warnings: list[str] = []
    for media_id, path in images:
        if only_missing and upscaled_copy(path, scale) is not None:
            continue
        if not is_media_uuid(media_id):
            warnings.append(
                f"{path.name}: not upscaled to {scale.upper()}; its Flow media id is not "
                "in the local catalog."
            )
            continue
        try:
            saved = await client.upsample_image(
                media_id=media_id,
                project_id=project_id,
                target_resolution=resolution,
                out_path=path.with_name(f"{path.stem}.{scale}{path.suffix}"),
            )
        except Exception as exc:  # noqa: BLE001 - the original image is already safe
            logger.warning("image.upscale_after_download_failed", media_id=media_id, error=str(exc))
            warnings.append(
                f"{path.name}: upscale to {scale.upper()} failed ({type(exc).__name__}: "
                f"{exc}); the original was kept. Retry with: gflow image upscale "
                f"{media_id} --scale {scale} --project {project_id}"
            )
            continue
        made.append(saved)
        logger.info("image.upscaled_after_download", media_id=media_id, path=str(saved))
    return made, warnings
