# Spike: moving a `-o` video across filesystems (#1114)

**Date:** 2026-10-29 · **Script:** [`scripts/dev/spike_1114_cross_device_relocate.py`](../../../scripts/dev/spike_1114_cross_device_relocate.py)
· **Cost:** $0 (no browser, no Flow call; the generation client is a stub)

## Question

The fix for #1114 replaces `Path.replace` with `shutil.move` in the CLI relocation
(`cli_video._relocate_single_video`) and the MCP queued path (`worker/daemon.py`). Its unit
tests fake `EXDEV` by patching `os.rename`/`os.replace`. Does the production code hold on
two **real** filesystems, does overwriting an existing `-o` file still work, and what
happens when an MCP `output` names a directory?

## Setup

- **Linux, two mounts** (the reporter's shape): `python:3.12-slim` in Docker, the source on a
  `--tmpfs /mnt/a`, the target on the container's overlay root (`/work/out`). The script
  checks `st_dev` and prints `distinct: true`.
- **Windows 11, one volume**: this host has only `C:`, so the cross-device arms could not
  run there. The overwrite and directory arms ran.

Readings were written in the script's docstring before the run.

## What was observed

| Arm | What ran | Linux (2 mounts) | Windows (1 volume) |
|---|---|---|---|
| A control | `Path.replace`, the pre-fix line, tmpfs → disk | `OSError` errno **18** (EXDEV) | not measured |
| B CLI | `_relocate_video_output`, tmpfs → disk | bytes landed, source removed | not measured |
| C MCP worker | `FlowWorker.process_task` (t2v, `output_file`) | task `completed`, bytes landed, source removed | not measured |
| D overwrite | relocation onto an existing target | target replaced (`NEW`), same mount and cross mount | target replaced, same mount |
| E directory | worker with `output_file` = an existing directory | task `completed`; clip moved **inside** the directory | same |

Control for arm E on `develop` (Windows): `Path.replace` onto a directory raised
`PermissionError` (errno 13), so the billed task failed and the clip stayed in the download
directory.

## Reading

- **A + B + C:** the root cause is real on a real kernel, and the fix holds on both
  surfaces. This is the reporter's exact failure, now measured rather than simulated.
- **D:** `shutil.move` keeps the overwrite behaviour of `Path.replace`. On Windows it gets
  there by a copy, because `os.rename` refuses an existing target and `shutil.move` falls
  back to `copy2` + `unlink`.
- **E:** a new wrong outcome. Before the fix, a directory `output` failed after billing. With
  `shutil.move` alone, the queue row records the **directory** as the clip's `local_path`
  while the clip sits inside it under its media-id name. The CLI never reaches this
  (`-o` is `click.Path(dir_okay=False)`), but the MCP `output` argument had no such check.
  The PR adds it: `gflow_generate_video` rejects a directory `output` with
  `Invalid Output` before any job is queued, so nothing is billed.

## Not measured

- **A cross-volume move on Windows** (e.g. `C:` → `D:`). This host has one volume. CPython's
  `shutil.move` falls back to a copy on any `OSError` from `os.rename`, which covers Windows'
  `ERROR_NOT_SAME_DEVICE`. That is read from the stdlib, not run. A second drive, or a
  mounted VHD, would settle it by running the script with `--a` and `--b` on different
  volumes.
