# Keeping the generation browser hidden: diagnose and fix

For agents (and people) answering "gflow's Chrome is showing on my screen" or "it keeps
stealing my keyboard and mouse", on **macOS or Windows**. It records what was measured,
how to find the cause on the user's machine, and the order in which to try fixes.

`gflow auth login` is **meant** to be visible: it waits for a person. Everything below
is about generation runs (`gflow image`, `gflow video`, `gflow run`, MCP tools).

## What is known

gflow drives a real Chrome. There are three ways to keep it out of the user's way:

| Setting | Window | Takes focus | Measured |
|---|---|---|---|
| `GFLOW_CLI_HEADLESS=true` | none | never | macOS, images: 6/6 on 2026-10-28, and again 2026-10-30 with the user-agent fix below. Not measured: video, Windows. |
| `GFLOW_CLI_WINDOW_MODE=offscreen` (default) | placed at `-30000,-30000`, then minimized | yes, when Chrome starts | Windows 11: the position stays past every monitor (images and video OK, 2026-09/10). macOS: Chrome pulls it onto the nearest display (see below). |
| `GFLOW_CLI_WINDOW_MODE=background` | macOS only: background-only Chrome clone, also minimized | never | macOS. On Windows it logs a warning and falls back to `offscreen`. |

Facts measured on macOS (2026-10-30), with two monitors, the second to the left of the
main one:

- **Chrome clamps an off-display position.** Asked for `-30000,-30000`, the window landed at
  `-2560,103`, fully visible on the left monitor. Moving it later via CDP still leaves a
  ~40 px corner on screen. On Windows the position was never clamped onto a monitor.
- **Minimizing hides the window but not completely.** The minimized window shows as a tile in
  the Dock, with the minimize animation. Opening a tab or `page.bring_to_front()` restores it,
  so gflow re-minimizes after both (`keep_window_hidden`, `bring_to_front_keep_hidden`).
  A minimized tab still reports `document.visibilityState == "visible"`.
- **Hiding the app (Cmd-H) does not work.** The background-only clone refuses `hide`; regular
  Chrome hides but reappears as soon as a tab opens.
- **Headless is the only mode with no window at all.** Its one measured tell was the user
  agent: `HeadlessChrome/<v>`. gflow now sends the headed string instead, built from the
  installed Chrome's version (`chrome_version`, `headed_user_agent`); page, workers and the
  `User-Agent` header were checked. UA-CH brands already read "Google Chrome".

## Diagnose first

Do these in order. The first one found the cause on 2026-10-30.

### 1. Is the installed gflow the one you fixed?

Tools like opus-video call the `gflow` on `PATH`, usually a `uv tool` install. If it predates
the fix, nothing you change in the repo reaches it.

```bash
uv tool list | grep gflow
cat "$(uv tool dir)/gflow-cli/lib/python3.11/site-packages/gflow_cli-"*.dist-info/direct_url.json
```

On Windows: `Get-Content "$(uv tool dir)\gflow-cli\Lib\site-packages\gflow_cli-*.dist-info\direct_url.json"`.
The `commit_id` must contain the fix. Update with
`uv tool install --force --reinstall "gflow-cli @ git+https://github.com/abm-dungtq/gflow-studio.git@main"`.

### 2. Which settings does gflow actually read?

`.env` in the current directory wins over `<home>/.env`. The home is
`~/Library/Application Support/gflow-cli` on macOS and `%LOCALAPPDATA%\abm-dungtq\gflow-cli`
on Windows. Run this from the directory the caller runs gflow in:

```bash
uv run python -c "from gflow_cli.config import Settings as S; s=S(); print(s.headless, s.window_mode, s.browser_window_position)"
```

### 3. Which process is the visible window?

Find gflow's Chrome (its command line contains `gflow-cli` and `profile_`) and walk up its
parents to see who started it.

macOS:

```bash
ps -axo pid,ppid,command | grep -i "user-data-dir=.*gflow-cli/profile_" | grep -v -- "--type=" | grep -v grep
ps -o pid,ppid,command -p <ppid>
osascript -e 'tell application "System Events" to get {position, value of attribute "AXMinimized"} of every window of (first process whose unix id is <pid>)'
```

Windows (PowerShell):

```powershell
Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
  Where-Object { $_.CommandLine -match 'gflow-cli\\profile_' -and $_.CommandLine -notmatch '--type=' } |
  Select-Object ProcessId, ParentProcessId, CommandLine
Get-CimInstance Win32_Process -Filter "ProcessId=<ppid>" | Select-Object Name, CommandLine
```

No match means it is not gflow's browser: it is the user's own Chrome or a login window.
In the command line, `--headless` means no window can exist, and `--window-position=...`
shows what gflow asked for.

### 4. Where is the window, and where are the monitors?

A window "off-screen" by gflow's request can still sit on a monitor that has a negative
coordinate. Compare the window rectangle with the monitor bounds.

macOS:

```bash
osascript -l JavaScript -e 'ObjC.import("AppKit"); var s=$.NSScreen.screens, o=[]; for (var i=0;i<s.count;i++){var f=s.objectAtIndex(i).frame; o.push(f.origin.x+","+f.origin.y+" "+f.size.width+"x"+f.size.height)}; o.join(" | ")'
```

Windows (PowerShell):

```powershell
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Screen]::AllScreens | Select-Object DeviceName, Bounds
Add-Type @'
using System; using System.Runtime.InteropServices;
public struct RECT { public int L, T, R, B; }
public static class W {
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
}
'@
$h = (Get-Process -Id <pid>).MainWindowHandle; $r = New-Object RECT
[W]::GetWindowRect($h, [ref]$r) | Out-Null; "$($r.L),$($r.T) minimized=$([W]::IsIconic($h))"
```

### 5. Does it take focus?

Sample the foreground app every 0.1 to 0.2 s during a run and count how often it is gflow's
Chrome. On macOS, `lsappinfo front` gives the front app (see
[the headless spike](superpowers/spikes/2026-10-28-headless-generation.md) for the method).
On Windows:

```powershell
Add-Type @'
using System; using System.Runtime.InteropServices;
public static class F {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
}
'@
1..600 | ForEach-Object { $p = 0; [F]::GetWindowThreadProcessId([F]::GetForegroundWindow(), [ref]$p) | Out-Null; $p; Start-Sleep -Milliseconds 200 } | Group-Object | Sort-Object Count
```

Chrome's helper processes are children of the main one, so compare against the gflow
Chrome PID and its children.

## Fix, in this order

1. **Install the fix** if step 1 showed an old build.
2. **`GFLOW_CLI_HEADLESS=true`** in `<home>/.env`. It is the only mode with no window and no
   focus change on any platform. Verify with one zero-credit image (below) while sampling focus.
3. **If headless fails** in a way a headed run on the same profile does not (403,
   `RecaptchaError`, a WAF exit 10 that does not recur headed), remove the line and stay
   headed. On Windows the default `offscreen` window is already past every monitor and is
   minimized; the remaining cost is the focus grab when Chrome starts, which no flag measured
   so far prevents (`--no-focus-on-init` made no difference).
4. **If the headed window is on a monitor** (step 4), check the log for
   `browser_manager.minimized_window_hides_page`. On a platform where a minimized tab reports
   `hidden`, gflow restores the window rather than let Flow stall, and only the position
   protects you.

Do not fix it by hiding windows from outside gflow (an AppleScript loop re-activating the
previous app, a Windows tool that moves windows): opus-video tried that on macOS, and the
loop closed menus and broke the Vietnamese IME.

## Verify

- Use a **test profile**, never the user's main ones, and an existing Flow project:
  `gflow image t2i "<prompt>" --profile <test> --project <id> -o tmp/check.jpg`. Images cost
  no credits; video does, so ask before a video check.
- **Exit 10 (WAF) or a reCAPTCHA error: stop.** Do not retry in a loop. Google throttles
  the whole machine, so first check whether another gflow run (headed or not) failed at the
  same time; that is what separated cause from coincidence on 2026-10-30.
- Report exit code, time, the focus count from step 5, and the window state from step 3.
  Add Windows results to the table under "What is known".

## Where the code is

- `src/gflow_cli/browser_manager.py`: `generation_window_config`, `hides_generation_window`,
  `minimize_window`, `keep_window_hidden`, `bring_to_front_keep_hidden`, `chrome_version`,
  `headed_user_agent`.
- Launch sites: `FlowApiClient._persistent_context_kwargs` and its launch in
  `src/gflow_cli/api/client.py`; `UiAutomationTransport` in
  `src/gflow_cli/api/transports/ui_automation.py`.
- Tests: `tests/test_window_hiding.py`, `tests/test_headless_user_agent.py`,
  `tests/api/test_client_window_position.py`.
- Settings: [CONFIGURATION](CONFIGURATION.md) § `GFLOW_CLI_HEADLESS`,
  `GFLOW_CLI_BROWSER_WINDOW_POSITION`, `GFLOW_CLI_WINDOW_MODE`.

## Not measured yet

- Headless on Windows, and headless video anywhere.
- On Windows: whether a minimized tab stays `visible`, and whether `chrome_version` finds the
  version folder next to `chrome.exe` on the user's install (if not, the default user agent,
  with `HeadlessChrome`, is kept).
- Long-term reCAPTCHA scoring of headless runs on one account.
