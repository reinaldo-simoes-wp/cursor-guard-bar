#!/usr/bin/env python3
"""cursor-guard-bar — SwiftBar plugin: lock your Mac while Cursor agents keep working.

Menu bar shows a shield plus the number of active Cursor agent sessions.
"Lock & Guard" starts a caffeinate keep-awake process and locks the screen via
the native macOS lock screen. Guarding stops automatically once the screen is
unlocked (checked on each refresh).

<swiftbar.title>cursor-guard-bar</swiftbar.title>
<swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
<swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>
"""

import base64
import ctypes
import json
import os
import re
import signal
import subprocess
import sys
import time

VERSION = "1.0.0"
REPO_URL = "https://github.com/reinaldo-simoes-wp/cursor-guard-bar"

CONFIG_DIR = os.path.expanduser("~/.config/cursor-guard-bar")
PID_FILE = os.path.join(CONFIG_DIR, "caffeinate.pid")

PROJECTS_DIR = os.path.expanduser("~/.cursor/projects")
HOOKS_STATUS_FILE = os.path.expanduser("~/.cursor-guard/agent-status.json")

ACTIVE_THRESHOLD_SEC = 45
COMPLETED_EXPIRY_SEC = 120
RECENT_THRESHOLD_SEC = 600
TAIL_BUFFER_SIZE = 32768
HEAD_BUFFER_SIZE = 16384

# Don't auto-stop the guard right after locking — the lock state can take a
# moment to register and SwiftBar refreshes immediately after the action.
UNLOCK_GRACE_SEC = 60

GREEN = "#34C759"
YELLOW = "#FFCC00"
GRAY = "#8E8E93"
RED = "#FF3B30"


# --- guard (caffeinate) management ---------------------------------------


def read_guard():
    """Return (pid, started_at) for a live caffeinate guard, else (None, None)."""
    try:
        with open(PID_FILE) as f:
            parts = f.read().split()
        pid, started = int(parts[0]), float(parts[1])
    except (OSError, ValueError, IndexError):
        return None, None

    if not pid_is_caffeinate(pid):
        clear_pidfile()
        return None, None
    return pid, started


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


def clear_pidfile():
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def start_guard():
    pid, _ = read_guard()
    if pid:
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
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(f"{proc.pid} {time.time()}")


def stop_guard():
    pid, _ = read_guard()
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    clear_pidfile()


# --- screen lock (login.framework / CoreGraphics via ctypes) --------------


def lock_screen():
    login = ctypes.CDLL("/System/Library/PrivateFrameworks/login.framework/login")
    login.SACLockScreenImmediate()


def screen_is_locked():
    try:
        cf = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        cg = ctypes.CDLL(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )

        cg.CGSessionCopyCurrentDictionary.restype = ctypes.c_void_p
        session = cg.CGSessionCopyCurrentDictionary()
        if not session:
            return False

        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_uint32,
        ]
        kCFStringEncodingUTF8 = 0x08000100
        key = cf.CFStringCreateWithCString(
            None, b"CGSSessionScreenIsLocked", kCFStringEncodingUTF8
        )

        cf.CFDictionaryGetValue.restype = ctypes.c_void_p
        cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        value = cf.CFDictionaryGetValue(session, key)

        locked = False
        if value:
            cf.CFBooleanGetValue.restype = ctypes.c_bool
            cf.CFBooleanGetValue.argtypes = [ctypes.c_void_p]
            locked = bool(cf.CFBooleanGetValue(value))

        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cf.CFRelease(key)
        cf.CFRelease(session)
        return locked
    except Exception:
        return False


def auto_unguard():
    """Stop guarding if the screen has been unlocked (past the grace period)."""
    pid, started = read_guard()
    if not pid:
        return
    if time.time() - started < UNLOCK_GRACE_SEC:
        return
    if not screen_is_locked():
        stop_guard()


# --- agent scanner ---------------------------------------------------------


def slugify(s):
    return re.sub(r"[^a-zA-Z0-9-]", "-", s)


def clean_project_name(slug):
    home = os.path.expanduser("~")
    slugified_home = slugify("-".join(p for p in home.split(os.sep) if p))
    if not slug.startswith(slugified_home):
        return slug
    remainder = slug[len(slugified_home):].lstrip("-")
    if remainder.startswith("Documents-repositories-"):
        return remainder[len("Documents-repositories-"):] or slug
    return "~/" + remainder if remainder else slug


def read_hooks_status():
    try:
        with open(HOOKS_STATUS_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def read_first_line(path):
    try:
        with open(path, "rb") as f:
            chunk = f.read(HEAD_BUFFER_SIZE).decode("utf-8", "replace")
        idx = chunk.find("\n")
        return chunk[:idx] if idx >= 0 else chunk
    except OSError:
        return None


def read_last_lines(path, count):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            read_size = min(TAIL_BUFFER_SIZE, size)
            if read_size == 0:
                return []
            f.seek(size - read_size)
            text = f.read(read_size).decode("utf-8", "replace")
        return [ln for ln in text.split("\n") if ln][-count:]
    except OSError:
        return []


def normalize_content(obj):
    raw = (obj.get("message") or {}).get("content") if isinstance(
        obj.get("message"), dict
    ) else None
    if raw is None:
        raw = obj.get("content")
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and raw.get("type"):
        return [raw]
    return None


def extract_query(json_line):
    try:
        obj = json.loads(json_line)
    except ValueError:
        return None
    content = normalize_content(obj)
    if not content:
        return None
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text") or ""
            m = re.search(r"<user_query>\s*([\s\S]*?)\s*</user_query>", text)
            if m:
                q = m.group(1).strip()
                return q[:80] + "…" if len(q) > 80 else q
            if obj.get("role") == "user" and text.strip():
                q = text.strip()
                return q[:80] + "…" if len(q) > 80 else q
    return None


def extract_current_action(lines):
    for line in reversed(lines):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("role") != "assistant":
            continue
        content = normalize_content(obj)
        if not content:
            continue
        if all(c.get("type") == "thinking" for c in content if isinstance(c, dict)):
            continue
        for item in reversed(content):
            if isinstance(item, dict) and item.get("type") == "tool_use":
                return item.get("name")
        return None
    return None


def last_turn_info(lines):
    for line in reversed(lines):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        role = obj.get("role")
        if role == "user":
            return {"last_role": "user", "has_pending_tool": False}
        if role == "assistant":
            content = normalize_content(obj)
            if not content:
                continue
            dict_items = [c for c in content if isinstance(c, dict)]
            if dict_items and all(c.get("type") == "thinking" for c in dict_items):
                continue
            has_tool = any(c.get("type") == "tool_use" for c in dict_items)
            return {"last_role": "assistant", "has_pending_tool": has_tool}
    return None


def newest_subagent_mtime(agent_dir):
    sub_dir = os.path.join(agent_dir, "subagents")
    newest = 0
    try:
        for name in os.listdir(sub_dir):
            if not name.endswith(".jsonl"):
                continue
            try:
                mt = os.stat(os.path.join(sub_dir, name)).st_mtime
                newest = max(newest, mt)
            except OSError:
                pass
    except OSError:
        return 0
    return newest


def scan_agents():
    if not os.path.isdir(PROJECTS_DIR):
        return []

    now = time.time()
    agents = []
    hooks_status = read_hooks_status()

    try:
        project_entries = sorted(os.listdir(PROJECTS_DIR))
    except OSError:
        return []

    for proj in project_entries:
        transcripts_dir = os.path.join(PROJECTS_DIR, proj, "agent-transcripts")
        if not os.path.isdir(transcripts_dir):
            continue

        try:
            entries = os.listdir(transcripts_dir)
        except OSError:
            continue
        # Directories first so they win over a same-named flat .jsonl
        entries.sort(
            key=lambda n: (0 if os.path.isdir(os.path.join(transcripts_dir, n)) else 1)
        )

        seen = set()
        for entry in entries:
            entry_path = os.path.join(transcripts_dir, entry)
            if os.path.isdir(entry_path):
                agent_id = entry
                jsonl_path = os.path.join(entry_path, f"{entry}.jsonl")
                agent_dir = entry_path
            elif entry.endswith(".jsonl"):
                agent_id = entry[: -len(".jsonl")]
                jsonl_path = entry_path
                agent_dir = None
            else:
                continue

            if agent_id in seen:
                continue
            seen.add(agent_id)

            hook_entry = (hooks_status or {}).get(agent_id) or {}
            hook_status = hook_entry.get("status")

            try:
                mtime = os.stat(jsonl_path).st_mtime
            except OSError:
                continue

            sub_mtime = newest_subagent_mtime(agent_dir) if agent_dir else 0
            effective_mtime = max(mtime, sub_mtime)
            age_sec = max(0, now - effective_mtime)
            if age_sec > RECENT_THRESHOLD_SEC:
                continue

            first_line = read_first_line(jsonl_path)
            query = extract_query(first_line) if first_line else None

            last_lines = read_last_lines(jsonl_path, 10)
            current_action = extract_current_action(last_lines)
            turn_info = last_turn_info(last_lines)

            subagents_active = sub_mtime > 0 and (now - sub_mtime) <= ACTIVE_THRESHOLD_SEC
            is_stale = age_sec > ACTIVE_THRESHOLD_SEC
            assistant_finished = (
                turn_info is not None
                and turn_info["last_role"] == "assistant"
                and not turn_info["has_pending_tool"]
            )

            hook_definitive = hook_status in ("completed", "aborted", "error", "ended")
            if hook_definitive:
                completed = not subagents_active
            elif hook_status == "running":
                completed = False
            else:
                completed = (assistant_finished or is_stale) and not subagents_active

            if completed and age_sec > COMPLETED_EXPIRY_SEC:
                continue

            if hook_status == "error" and completed:
                status = "error"
            elif completed:
                status = "completed"
            elif not current_action:
                status = "idle"
            else:
                status = "active"

            agents.append(
                {
                    "project": clean_project_name(proj),
                    "query": query or "Agent session",
                    "status": status,
                    "current_action": current_action,
                    "age_sec": round(age_sec),
                }
            )

    agents.sort(key=lambda a: a["age_sec"])
    return agents


# --- menu rendering ---------------------------------------------------------


STATUS_STYLE = {
    "active": (GREEN, "active"),
    "idle": (YELLOW, "idle"),
    "completed": (GRAY, "done"),
    "error": (RED, "error"),
}


def humanize_action(name):
    if not name:
        return None
    mapping = {
        "Shell": "Running command",
        "Read": "Reading file",
        "Write": "Writing file",
        "StrReplace": "Editing file",
        "Grep": "Searching code",
        "Glob": "Finding files",
        "SemanticSearch": "Searching code",
        "Task": "Running subagent",
        "TodoWrite": "Planning",
        "WebSearch": "Searching web",
        "WebFetch": "Fetching page",
    }
    return mapping.get(name, name)


def render_menu():
    auto_unguard()

    guard_pid, guard_started = read_guard()
    guarding = guard_pid is not None
    agents = scan_agents()
    active_count = sum(1 for a in agents if a["status"] == "active")

    plugin_path = os.path.realpath(__file__)

    # Menu bar title. SwiftBar renders sfimage at "large" scale by default,
    # which sits taller than standard menu bar icons — sfconfig scales it down.
    sfconfig = base64.b64encode(json.dumps({"scale": "medium"}).encode()).decode()
    title_text = f"{active_count} " if active_count else ""
    symbol = "lock.shield.fill" if guarding else "shield"
    line = f"{title_text}| sfimage={symbol} sfconfig={sfconfig}"
    if guarding:
        line += f" sfcolor={GREEN}"
    print(line)

    print("---")

    if guarding:
        since = time.strftime("%H:%M", time.localtime(guard_started))
        print(f"Guarding since {since} | sfimage=lock.fill color={GREEN}")
        print(
            f"Stop Guarding | bash={plugin_path} param1=unguard"
            " terminal=false refresh=true sfimage=shield.slash"
        )
    else:
        print(
            f"Lock & Guard | bash={plugin_path} param1=lock"
            " terminal=false refresh=true sfimage=lock.shield"
        )

    print("---")

    if agents:
        print(f"Cursor Agents ({len(agents)}) | size=12 color={GRAY}")
        for agent in agents:
            color, label = STATUS_STYLE.get(agent["status"], (GRAY, agent["status"]))
            action = humanize_action(agent["current_action"])
            detail = action if action else label
            print(f"{agent['project']} — {detail} | color={color}")
            print(f"--{agent['query']} | size=11 color={GRAY}")
            print(f"--{label} · {agent['age_sec']}s ago | size=11 color={GRAY}")
    else:
        print(f"No recent Cursor agents | color={GRAY}")

    print("---")
    print(f"cursor-guard-bar v{VERSION} | size=11 color={GRAY} href={REPO_URL}")


# --- entry point -------------------------------------------------------------


def main():
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "lock":
            start_guard()
            lock_screen()
        elif cmd == "unguard":
            stop_guard()
        return
    render_menu()


if __name__ == "__main__":
    main()
