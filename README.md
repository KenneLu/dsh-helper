# dsh-helper v1.7.0

**English** | [简体中文](README.zh-CN.md)

Windows tray tool that manages DeepSeek Harness's `dsh web` from a right-click menu: start, stop, restart, open the panel, copy the panel URL, and status refresh. Status refresh runs every 1 minute by default and is adjustable from the menu. The port is fixed at `3080` by default (the official `dsh web` fallback port); only when that port is taken does the current launch fall back to an automatic port.

Added in 1.7.0:

- **Online updates**: menu items "Check for dsh-helper updates" and "Download and update dsh-helper" — also auto-checked at startup (24-hour throttle). Downloads come from GitHub Releases (zip + sha256 verified); after you quit the tray, the update is applied automatically and the new version restarts.
- **User data area**: config and logs live in `%LOCALAPPDATA%\dsh-helper\` (`config.json` + `log\`); a pre-1.7 config.json next to the exe is migrated automatically on first run. Logs rotate at 1 MB × 3 backups, with an "Open log folder" menu item.
- **Panel URL masking**: the token is shown as `••••••` in the menu; the full URL (including the token) is only available via "Copy panel URL", which sits directly under the URL line.

## Running

Launch `out\dsh-helper-pkg-<YYYYMMDD-HHmmssfff>\dsh-helper.exe`. A grey whale icon appears in the tray; once dsh is running the whale turns blue with a soft warm-gold spout.

**Single instance only**: double-clicking again does not open a second tray — it shows a notice and cancels that launch.

Right-click menu:

| Menu item | Description |
|---|---|
| Status | Shows whether dsh is stopped, starting, running, stopping, or in error |
| Current panel | Shows the actual listening address, with the token masked (`••••••`) |
| Copy panel URL | Copies the full URL (including the token) to the clipboard — the only way to get it |
| Check for dsh-helper updates | Queries GitHub Releases and notifies on the result |
| Download and update dsh-helper | Downloads the new version (sha256 verified); applied after you quit the tray |
| Start dsh Web | Runs `dsh.cmd web --no-open --host 127.0.0.1 --port 3080` (falls back to a free port if 3080 is busy); opens the panel once ready |
| Stop dsh Web | Sends Ctrl+C for a graceful dsh exit (up to 8 s), then force-kills the process tree on timeout |
| Restart dsh Web | Graceful stop, then restart on the configured port (default 3080); opens the panel on success |
| Open dsh panel | Opens the current URL in the default browser (also the double-click default action) |
| Refresh status | Re-scans dsh processes and listening ports |
| Autostart | Per-user HKCU Run key, no admin required; the checkbox is only shown when the registered path is this exact program |
| Start dsh Web on launch | Starts dsh Web automatically after dsh-helper starts |
| Open config file | Opens the runtime config |
| dsh.cmd path | Submenu: open path, auto-detect, choose path |
| Open log folder | Opens the running log directory |
| Status refresh interval | 5 s / 20 s / 1 min / 5 min / 10 min, written back to `config.json` |
| Quit | Gracefully stops dsh first (up to 8 s), then exits; greyed out while waiting |

Icon states: grey whale and spout when dsh is not running; original blue whale with a soft warm-gold spout when it is.

## Design notes

- Only a confirmed `dsh.cmd` path from the config is used; on first start or when the path goes stale it is auto-detected via PATH and npm environment variables.
- Single instance: a named mutex (`Local\dsh-helper-single-instance`) pins the tray process to one. The mutex is kernel-managed and released when the process dies. One instance only — multiple trays would each watch the same dsh with contradictory state, and **every instance's "Quit" stops dsh first**. The guard fails open (better an extra tray than an app that won't start).
- Before starting, existing `dsh web` processes are scanned; if a panel is already up, its URL is adopted instead of creating a second service.
- Stop, restart, and quit all send `Ctrl+C` to the dsh process tree first for the official graceful exit: dsh disposes plugins and flushes sessions so no memory or session data is lost. Up to 8 seconds, then `taskkill /T /F` cleans up the tree. `Ctrl+C` is broadcast exactly **once** (a second signal in the same console makes dsh skip flushing). Attaching/detaching the console rebinds standard handles — they must be restored, or every later `subprocess.run` throws `WinError 6`.
- The actual URL is identified from both dsh's startup output and the local listening port.
- Compatible with DSH 0.1.2+ token URLs: `?token=...` is kept, and the 3xx token-to-cookie exchange is treated as "service ready".
- The panel opens once automatically after a successful start; a browser-handoff failure only notifies and never turns the dsh start into an error.
- The current URL lives only in memory and the menu — no accounts, passwords, or external services.

## Configuration

`config.json` lives in the user data directory (`%LOCALAPPDATA%\dsh-helper\`):

```json
{
  "dsh_cmd": "",
  "host": "127.0.0.1",
  "port": 3080,
  "status_refresh_interval_sec": 60,
  "start_on_launch": false,
  "autostart": false
}
```

`port` defaults to `3080` (the official dsh web fallback port, matching the VS Code extension). Before starting, the port is probed: free means it is used as-is; **taken means this launch falls back to a free port**, with the reason in the notification and log. `0` means never fix a port — dsh/the OS picks a free one each time. Avoid the Windows dynamic range (49152–65535) or a random ephemeral port may steal it.

An empty `dsh_cmd` means dsh is not configured yet. It is auto-detected on first start or when the configured path goes stale; picking a new dsh.cmd runs `dsh.cmd web --help` as validation without starting the web service.

`status_refresh_interval_sec` accepts `5`, `20`, `60`, `300`, or `600`; default `60`.

"Auto-detect" searches the existing configured path, the Windows PATH, and common npm directories, validating each candidate with `dsh.cmd web --help`; the first valid path is written to the config. If none validates, the old config is kept.

## Packaging

Uses a fixed Python and PyInstaller:

```text
H:\Tools\Python\Python313\python.exe
build.bat nopause
```

`build.bat` runs a compile gate, builds an `onedir/noconsole` package, then runs `--smoke` to verify the dsh command and port decision (fixed port when free, otherwise automatic fallback). Historical packages stay in `out\`.

Official releases are built by CI: push a `v<semver>` tag (e.g. `v1.7.0`) and the release workflow publishes a zip + sha256 on GitHub Releases — the same layout the in-app updater consumes. The shipped exe is named `dsh-helper.exe` without a version (the autostart registry stores the full path; versions live in the zip/folder names).

## FAQ

- **Menu says dsh.cmd not found**: use "dsh.cmd path → Auto-detect", or "Choose path" to point at the real dsh.cmd.
- **URL changed after starting**: with the default fixed 3080 it no longer changes; only a fallback to an automatic port changes it — use "Copy panel URL" for the latest address (the notification and log explain the fallback).
- **Want a fixed address**: 3080 is fixed by default; to change it, set `config.json`'s `port` to a free port outside 49152–65535.
- **DSH 0.1.2+ token URLs**: never hand-edit `?token=...` out of the URL; a fresh token is generated on each restart.
- **What happens to dsh on Quit**: Quit stops dsh gracefully first (up to 8 s) so plugins dispose and sessions flush, then exits the tray. During those 8 seconds the status line shows "Stopping", the icon turns grey, and Start/Stop/Restart/Quit are all disabled — intentional; let it finish.
- **Autostart unchecked but the registry entry exists**: the Run key does not point at this exact program (the package folder moved, or the old package was deleted). The checkbox means "it will actually start next boot, and it will be this one" — so unchecked is honest. Click "Autostart" once to overwrite with the current path. The program never edits the registry on its own.
- **A notice says "already running" after double-clicking**: one tray instance is already alive (the whale in the notification area); the single-instance guard cancelled this launch.
- **Correct order when switching versions**: quit the old instance before starting the new package. The mutex name is fixed and version-less, so all versions from 1.5 on mutually block across versions — starting a new package while an old one runs shows "already running". Versions 1.4 and earlier have no guard. Since 1.7.0, the in-app updater handles this: quit the tray and it swaps and restarts for you.
- **Tray right-click unresponsive / menu clicks do nothing**: known 1.4/1.5 bug — graceful stop left dangling console handles, `subprocess` threw `WinError 6`, and the exception escaping a menu callback stalled menu rendering. **Fixed in 1.6** (handles restored; query failures only log). Kill `dsh-helper-*.exe` from Task Manager if you hit it; dsh is unaffected.
