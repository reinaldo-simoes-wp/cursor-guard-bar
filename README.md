# cursor-guard-bar

SwiftBar plugin to lock your Mac privately and securely while Cursor agents keep working locally.

It uses the **native macOS lock screen** (real password / Touch ID security) plus **`caffeinate`** to keep the system awake so agents keep running. The display is still allowed to sleep — only system/idle sleep is blocked.

## Install

1. Install [SwiftBar](https://swiftbar.app) (`brew install swiftbar`)
2. Clone this repo and symlink the plugin into your SwiftBar plugin folder:

```bash
git clone https://github.com/reinaldo-simoes-wp/cursor-guard-bar.git
ln -s "$(pwd)/cursor-guard-bar/cursor-guard.30s.py" ~/.swiftbar/
```

3. Refresh SwiftBar (or relaunch it)

## Usage

The menu bar shows a shield with the number of active Cursor agents (e.g. `🛡 2`). The shield is highlighted while guarding.

| Action | Effect |
|--------|--------|
| **Start Guarding** | Starts `caffeinate -is` so the system stays awake |
| **Lock Screen** | Locks the screen (shown only while guarding) |
| **Stop Guarding** | Kills caffeinate (shown only while guarding) |

Starting the guard does **not** lock the screen — lock whenever you're ready (via **Lock Screen** or Ctrl+Cmd+Q). Guarding stops **automatically** after you lock and later unlock; until then it keeps running across unlocks so you can step away repeatedly.

The dropdown also lists recent Cursor agent sessions (project, status, current action), detected by scanning `~/.cursor/projects/*/agent-transcripts/`.

## Known limitation

Closing the lid still sleeps the machine unless you're on power with an external display attached (macOS clamshell rule — `caffeinate` cannot override it). Leave the lid open while guarding.
