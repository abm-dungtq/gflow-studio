# Upscaling an image the project grid does not list

**Date:** 2026-11-02 · **Script:** `scripts/dev/spike_upscale_detail_url.py` · **Cost:** 0 credits

## Question

`gflow image upscale <media> --scale 2k --project <id>` failed with exit 23, "image tile for
media_id … not found", for a frame generated five days earlier in a project of a few hundred
images. Is the image unreachable, or is the lookup wrong?

## Observation

One run on a flow.google.com account, profile with a live session:

| Step | Result |
|---|---|
| Project grid after 8 s | 8 `img[data-media-id]` tiles; the target is not among them |
| `/project/<id>/edit/<media>` opened directly | download button present, target `img` present |
| Download menu | 3 items, one matching the `2K` token |

## Conclusion

The grid renders only its first load, and the upscale waited and reloaded but never scrolled,
so any image outside that load was unreachable. The detail view opens an image by id regardless
of its place in the grid. Video upscale already opened the clip editor first; image upscale now
shares that path (`open_download_menu`), keeping the grid as the fallback where a project Flow
will not open shows its not-found landing (the detail URL does not redirect).

Covered by `tests/e2e/test_image_upscale_e2e.py::test_e2e_upscale_reaches_an_image_the_grid_does_not_list`.
