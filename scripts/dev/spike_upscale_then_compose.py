"""What state does an image upscale leave the composer in?

`gflow run --upscale 2k` fails the row after an upscale with "no 'mode' radio offering
'image' in the settings pane (1 option groups rendered)". This upscales one existing
image, returns to the project, opens the settings pane, and records URL, overlays,
radiogroups and screenshots at each step. Zero credits (upscale is free, no generation).

    uv run python scripts/dev/spike_upscale_then_compose.py <profile> <project_id> <media_id>
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from gflow_cli._cli_helpers import _make_provider_dir
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image_upscale import TargetResolution
from gflow_cli.api.transports.migrated_composer import MIGRATED_PROJECT_URL, READY_ANCHOR

OUT = Path(__file__).parent / "_spike_out" / "upscale_then_compose"


async def snap(page, tag: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(OUT / f"{tag}.png"))
    groups = await page.locator("[role='radiogroup']").count()
    dialogs = await page.locator("[role='dialog'], [role='menu'], .cdk-overlay-pane").count()
    radios = await page.locator("[role='radio']").all_inner_texts()
    print(f"[{tag}] url={page.url} radiogroups={groups} overlays={dialogs} radios={radios[:12]}")


async def main(profile: str, project: str, media: str) -> None:
    async with FlowApiClient(profile_dir=_make_provider_dir(profile), headless=True) as client:
        page = client.page
        await page.goto(
            MIGRATED_PROJECT_URL.format(project_id=project), wait_until="domcontentloaded"
        )
        await page.locator(READY_ANCHOR).first.wait_for(state="visible", timeout=30000)
        await page.locator(READY_ANCHOR).first.click()
        await page.wait_for_timeout(1500)
        await snap(page, "1-before-upscale-pane")
        await page.keyboard.press("Escape")
        await client.upsample_image(
            media_id=media,
            project_id=project,
            target_resolution=TargetResolution.RES_2K,
            out_path=OUT / "up.2k.jpg",
        )
        await snap(page, "2-after-upscale")
        await page.goto(
            MIGRATED_PROJECT_URL.format(project_id=project), wait_until="domcontentloaded"
        )
        await page.locator(READY_ANCHOR).first.wait_for(state="visible", timeout=30000)
        await page.wait_for_timeout(1500)
        await snap(page, "3-back-on-project")
        await page.locator(READY_ANCHOR).first.click()
        await page.wait_for_timeout(1500)
        await snap(page, "4-settings-pane")


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:4]))
