"""Can an image that is not in the project grid's first load be upscaled?

`gflow image upscale` on a frame made days earlier in a project of a few hundred images
raised exit 23 "image tile ... not found": the grid lists only its first load, and the
upscale waits and reloads but never scrolls. This opens the image's detail view directly
(`/project/<id>/edit/<media>`, the page a tile click lands on) and records whether the
download button and the 2K menu item are there. Zero credits; nothing is clicked past
opening the menu.

    uv run python scripts/dev/spike_upscale_detail_url.py <profile> <project_id> <media_id>
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from gflow_cli._cli_helpers import _make_provider_dir
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports.migrated_composer import MIGRATED_PROJECT_URL
from gflow_cli.api.transports.migrated_upscale import DOWNLOAD_BUTTON_SELECTOR, menu_token_pattern

OUT = Path(__file__).parent / "_spike_out" / "upscale_detail_url"


async def main(profile: str, project: str, media: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    async with FlowApiClient(profile_dir=_make_provider_dir(profile), headless=True) as client:
        page = client.page
        grid = MIGRATED_PROJECT_URL.format(project_id=project)
        await page.goto(grid, wait_until="domcontentloaded")
        await page.wait_for_timeout(8000)
        tiles = await page.locator("img[data-media-id]").count()
        listed = await page.locator(f'img[data-media-id="{media}"]').count()
        print(f"[grid] tiles={tiles} target_listed={listed}")
        await page.goto(f"{grid}/edit/{media}", wait_until="domcontentloaded")
        btn = page.locator(DOWNLOAD_BUTTON_SELECTOR).first
        try:
            await btn.wait_for(timeout=20000)
        except Exception as exc:  # noqa: BLE001 - spike records whatever happened
            print(f"[detail] url={page.url} download button missing: {type(exc).__name__}")
            await page.screenshot(path=str(OUT / "detail.png"))
            return
        shown = await page.locator(f'img[data-media-id="{media}"]').count()
        print(f"[detail] url={page.url} download_button=yes target_img={shown}")
        await btn.click()
        await page.wait_for_timeout(800)
        items = await page.locator("[role='menuitem']").all_inner_texts()
        has_2k = any(menu_token_pattern("2K").search(t) for t in items)
        print(f"[menu] items={len(items)} has_2K={has_2k}")
        await page.screenshot(path=str(OUT / "menu.png"))


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:4]))
