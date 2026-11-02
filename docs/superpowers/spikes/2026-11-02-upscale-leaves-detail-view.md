# Spike: what an image upscale leaves behind for the next row (2026-11-02)

**Question.** `gflow run --upscale 2k` failed every second row with `UiSelectorDriftError`:
"no 'mode' radio offering 'image' in the settings pane (1 option groups rendered)". The same
two rows without `--upscale` ran green. What state does the upscale leave?

**Script.** [`scripts/dev/spike_upscale_then_compose.py`](../../../scripts/dev/spike_upscale_then_compose.py)
upscales one existing image, returns to the project, and records the URL, overlays and radio
groups at each step. It costs no credits.

## Observations (one profile, served flow.google.com, Vietnamese UI)

| Step | URL | Radio groups in the settings pane |
|---|---|---|
| Before the upscale | `/project/<id>` | 3 (mode, aspect, count) |
| After the upscale | `/project/<id>/edit/<media>` | 0 |
| After `goto` back to `/project/<id>` | `/project/<id>` | 3 |

- The incident bundle of the failed row shows the image detail view, with Flow's "upscale
  finished" toast, and not the editor.
- A logged run showed that `client.page.url` was `about:blank` during the batch. The composer
  drives another page. Navigating `client.page` back to the project therefore never touched the
  page the next row uses.
- `MigratedComposer.ensure_editor` skipped navigation when the URL *started with* the project
  URL. `/project/<id>/edit/<media>` does start with it.

## Finding

The detail view is a sub-route of the project, and it has no settings pane. The editor check now
compares scheme, host and path (`_on_project_page`), so the next row navigates back. The upscale
helper no longer navigates at all. `rename_project` used the same prefix test and was changed with
it.

`services/video_upscale.py` still returns `client.page` to the project after a video upscale. On
this transport that page is not the composer's page. The editor check fix covers the next row, so
the extra navigation is harmless and was left alone.
