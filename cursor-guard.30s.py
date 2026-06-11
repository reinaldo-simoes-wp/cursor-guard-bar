#!/usr/bin/env python3
"""cursor-guard-bar — SwiftBar plugin: lock your Mac while Cursor agents keep working.

"Start Guarding" launches a caffeinate keep-awake process so agents keep
running; lock the screen whenever you like ("Lock Screen" or Ctrl+Cmd+Q).
Guarding is a manual toggle: it stays on until you click "Stop Guarding".

<xbar.title>cursor-guard-bar</xbar.title>
<xbar.version>v1.1.0</xbar.version>
<xbar.author>Reinaldo Simoes</xbar.author>
<xbar.author.github>reinaldo-simoes-wp</xbar.author.github>
<xbar.desc>Lock your Mac privately and securely while Cursor agents keep working locally. Native lock screen + caffeinate keep-awake.</xbar.desc>
<xbar.dependencies>python3</xbar.dependencies>
<xbar.abouturl>https://github.com/reinaldo-simoes-wp/cursor-guard-bar</xbar.abouturl>

<swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
<swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>
"""

import base64
import ctypes
import json
import os
import signal
import subprocess
import sys
import time

VERSION = "1.1.0"
REPO_URL = "https://github.com/reinaldo-simoes-wp/cursor-guard-bar"

CONFIG_DIR = os.path.expanduser("~/.config/cursor-guard-bar")
STATE_FILE = os.path.join(CONFIG_DIR, "guard.json")

GREEN = "#34C759"
GRAY = "#8E8E93"


# --- guard (caffeinate) management ---------------------------------------


def read_guard():
    """Return guard state dict for a live caffeinate guard, else None."""
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
        pid = int(state["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None

    if not pid_is_caffeinate(pid):
        clear_state()
        return None
    return state


def write_state(state):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def pid_is_caffeinate(pid):
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except Exception:
        return False
    return out.endswith("caffeinate")


def clear_state():
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass


def start_guard():
    if read_guard():
        return
    # -i: prevent idle sleep, -s: prevent system sleep while on AC power.
    # Display sleep stays allowed so the locked screen can go dark.
    proc = subprocess.Popen(
        ["/usr/bin/caffeinate", "-is"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    write_state({"pid": proc.pid, "started": time.time()})


def stop_guard():
    state = read_guard()
    if state:
        try:
            os.kill(int(state["pid"]), signal.SIGTERM)
        except OSError:
            pass
    clear_state()


# --- screen lock (login.framework via ctypes) -----------------------------


def lock_screen():
    login = ctypes.CDLL("/System/Library/PrivateFrameworks/login.framework/login")
    login.SACLockScreenImmediate()


# --- menu rendering ---------------------------------------------------------


def render_menu():
    state = read_guard()
    guarding = state is not None

    plugin_path = os.path.realpath(__file__)

    # SwiftBar renders sfimage at "large" scale by default, which sits taller
    # than standard menu bar icons — sfconfig scales it down.
    sfconfig = base64.b64encode(json.dumps({"scale": "medium"}).encode()).decode()
    symbol = "lock.shield.fill" if guarding else "shield"
    line = f"| sfimage={symbol} sfconfig={sfconfig}"
    if guarding:
        line += f" sfcolor={GREEN}"
    print(line)

    print("---")

    if guarding:
        since = time.strftime("%H:%M", time.localtime(state.get("started", 0)))
        print(f"Guarding since {since} | sfimage=checkmark.shield color={GREEN}")
        print(
            f"Lock Screen | bash={plugin_path} param1=lock"
            " terminal=false sfimage=lock.fill"
        )
        print(
            f"Stop Guarding | bash={plugin_path} param1=unguard"
            " terminal=false refresh=true sfimage=shield.slash"
        )
    else:
        print(
            f"Start Guarding | bash={plugin_path} param1=guard"
            " terminal=false refresh=true sfimage=lock.shield"
        )

    print("---")
    print(f"cursor-guard-bar v{VERSION} | size=11 color={GRAY} href={REPO_URL}")


# --- entry point -------------------------------------------------------------


def main():
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "guard":
            start_guard()
        elif cmd == "lock":
            lock_screen()
        elif cmd == "unguard":
            stop_guard()
        return
    render_menu()


if __name__ == "__main__":
    main()
