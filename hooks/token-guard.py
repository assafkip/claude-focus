#!/usr/bin/env python3
"""
Token Guard — runaway loop detection for Claude Code.

Catches every attention-drift failure mode documented in the research:
exact retries, tool spirals, runaway agent spawns, grep drift, edit loops,
re-read loops, rate-limited MCP storms, and time-based stalls.

Hook wiring:
  - PreToolUse: counts tool calls and enforces limits
  - UserPromptSubmit: resets per-message counters

Exit codes:
  0 = allow (optionally with a warning injected via stdout JSON)
  2 = block (stderr message goes back to Claude as feedback)

Research basis:
  Liu et al. "Lost in the Middle: How Language Models Use Long Contexts" (2023)
  Laban et al. "LLMs Get Lost in Multi-Turn Conversation" (2025)
"""

import hashlib
import json
import os
import sys
import time


# --- Thresholds (override via env vars) ---
RETRY_LIMIT = int(os.environ.get("CLAUDE_FOCUS_RETRY_LIMIT", 3))
VOLUME_CEILING = int(os.environ.get("CLAUDE_FOCUS_VOLUME_CEILING", 50))
VOLUME_WARNING = int(os.environ.get("CLAUDE_FOCUS_VOLUME_WARNING", 35))
AGENT_CEILING = int(os.environ.get("CLAUDE_FOCUS_AGENT_CEILING", 25))
MCP_RATE_WINDOW = 60
MCP_RATE_LIMIT = int(os.environ.get("CLAUDE_FOCUS_MCP_RATE_LIMIT", 30))
READ_SPIRAL_LIMIT = int(os.environ.get("CLAUDE_FOCUS_READ_SPIRAL_LIMIT", 15))
FILE_REREAD_LIMIT = int(os.environ.get("CLAUDE_FOCUS_FILE_REREAD_LIMIT", 3))
GREP_DRIFT_LIMIT = int(os.environ.get("CLAUDE_FOCUS_GREP_DRIFT_LIMIT", 5))
EDIT_FAIL_LIMIT = int(os.environ.get("CLAUDE_FOCUS_EDIT_FAIL_LIMIT", 3))
AGENT_NO_OUTPUT_LIMIT = int(os.environ.get("CLAUDE_FOCUS_AGENT_NO_OUTPUT_LIMIT", 3))
STALL_TIME_SECONDS = int(os.environ.get("CLAUDE_FOCUS_STALL_TIME_SECONDS", 120))
STALL_MIN_CALLS = int(os.environ.get("CLAUDE_FOCUS_STALL_MIN_CALLS", 10))

# Sensitive file patterns (blocks Edit/Write, not Read)
SENSITIVE_PATTERNS = (".env", ".pem", ".key", "credentials", "id_rsa", "id_ed25519")


def cache_path(session_id):
    return f"/tmp/claude-focus-{session_id}.json"


def load_cache(session_id):
    path = cache_path(session_id)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {
        "session_id": session_id,
        "tool_calls_since_user": 0,
        "agent_calls_since_user": 0,
        "mcp_timestamps": [],
        "repeat_map": {},
        "consecutive_reads": 0,
        "warnings_issued": 0,
        "file_read_counts": {},
        "greps_since_write": 0,
        "edit_targets": {},
        "agents_without_write": 0,
        "last_write_time": time.time(),
        "calls_since_write": 0,
    }


def save_cache(session_id, cache):
    path = cache_path(session_id)
    try:
        with open(path, "w") as f:
            json.dump(cache, f)
    except IOError:
        pass


def update_counters(tool_name, tool_input, cache):
    cache["tool_calls_since_user"] = cache.get("tool_calls_since_user", 0) + 1

    if tool_name == "Agent":
        cache["agent_calls_since_user"] = cache.get("agent_calls_since_user", 0) + 1

    input_hash = hashlib.md5(
        (tool_name + json.dumps(tool_input, sort_keys=True)).encode()
    ).hexdigest()[:12]
    key = f"{tool_name}:{input_hash}"
    repeat_map = cache.get("repeat_map", {})
    repeat_map[key] = repeat_map.get(key, 0) + 1
    cache["repeat_map"] = repeat_map

    if tool_name in ("Read", "Grep", "Glob"):
        cache["consecutive_reads"] = cache.get("consecutive_reads", 0) + 1
    elif tool_name in ("Edit", "Write", "Bash", "Agent"):
        cache["consecutive_reads"] = 0

    if tool_name == "Read":
        file_path = tool_input.get("file_path", "")
        if file_path:
            counts = cache.get("file_read_counts", {})
            counts[file_path] = counts.get(file_path, 0) + 1
            cache["file_read_counts"] = counts

    if tool_name in ("Grep", "Glob"):
        cache["greps_since_write"] = cache.get("greps_since_write", 0) + 1

    if tool_name == "Edit":
        file_path = tool_input.get("file_path", "")
        if file_path:
            targets = cache.get("edit_targets", {})
            targets[file_path] = targets.get(file_path, 0) + 1
            cache["edit_targets"] = targets

    if tool_name == "Agent":
        cache["agents_without_write"] = cache.get("agents_without_write", 0) + 1

    cache["calls_since_write"] = cache.get("calls_since_write", 0) + 1
    if tool_name in ("Edit", "Write"):
        cache["greps_since_write"] = 0
        cache["agents_without_write"] = 0
        cache["last_write_time"] = time.time()
        cache["calls_since_write"] = 0
    if tool_name == "Write":
        cache["edit_targets"] = {}

    if tool_name.startswith("mcp__"):
        now = time.time()
        timestamps = cache.get("mcp_timestamps", [])
        timestamps = [t for t in timestamps if now - t < MCP_RATE_WINDOW]
        timestamps.append(now)
        cache["mcp_timestamps"] = timestamps

    return cache


def check_sensitive_file(tool_name, tool_input):
    if tool_name not in ("Edit", "Write"):
        return None
    file_path = (tool_input.get("file_path", "") or "").lower()
    for pattern in SENSITIVE_PATTERNS:
        if pattern in file_path:
            return f"BLOCK: attempted to modify sensitive file matching '{pattern}'."
    return None


def check_exact_retry(tool_name, tool_input, cache):
    input_hash = hashlib.md5(
        (tool_name + json.dumps(tool_input, sort_keys=True)).encode()
    ).hexdigest()[:12]
    key = f"{tool_name}:{input_hash}"
    count = cache.get("repeat_map", {}).get(key, 0)
    if count >= RETRY_LIMIT:
        return f"You've attempted this exact call {count} times. Stop. Diagnose the failure and report what's blocking you."
    return None


def check_volume(cache):
    calls = cache.get("tool_calls_since_user", 0)
    if calls >= VOLUME_CEILING:
        return ("block", f"{VOLUME_CEILING} tool calls without user input. Stop. Summarize what you've accomplished and what's remaining.")
    if calls >= VOLUME_WARNING and cache.get("warnings_issued", 0) == 0:
        remaining = VOLUME_CEILING - calls
        cache["warnings_issued"] = 1
        return ("warn", f"{calls} tool calls since the last user message. {remaining} remaining before hard stop. Focus on producing output.")
    return None


def check_agent_ceiling(tool_name, cache):
    if tool_name != "Agent":
        return None
    count = cache.get("agent_calls_since_user", 0)
    if count > AGENT_CEILING:
        return f"{AGENT_CEILING} subagents spawned since last user message. Use direct tool calls (Grep, Glob, Read) instead."
    return None


def check_mcp_rate(tool_name, cache):
    if not tool_name.startswith("mcp__"):
        return None
    timestamps = cache.get("mcp_timestamps", [])
    if len(timestamps) > MCP_RATE_LIMIT:
        return f"{MCP_RATE_LIMIT} MCP calls in the last {MCP_RATE_WINDOW} seconds. Pause and batch your requests."
    return None


def check_read_spiral(tool_name, cache):
    if tool_name not in ("Read", "Grep", "Glob"):
        return None
    count = cache.get("consecutive_reads", 0)
    if count >= READ_SPIRAL_LIMIT:
        return f"{READ_SPIRAL_LIMIT} consecutive read operations with no output. Are you exploring or producing?"
    return None


def check_file_reread(tool_name, tool_input, cache):
    if tool_name != "Read":
        return None
    file_path = tool_input.get("file_path", "")
    count = cache.get("file_read_counts", {}).get(file_path, 0)
    if count >= FILE_REREAD_LIMIT:
        short = os.path.basename(file_path)
        return f"You've read {short} {count} times. You already have this information. Use it or move on."
    return None


def check_grep_drift(tool_name, cache):
    if tool_name not in ("Grep", "Glob"):
        return None
    count = cache.get("greps_since_write", 0)
    if count >= GREP_DRIFT_LIMIT:
        return f"{count} searches without producing output. You're searching, not working. Pick a direction."
    return None


def check_edit_spiral(tool_name, tool_input, cache):
    if tool_name != "Edit":
        return None
    file_path = tool_input.get("file_path", "")
    count = cache.get("edit_targets", {}).get(file_path, 0)
    if count >= EDIT_FAIL_LIMIT:
        short = os.path.basename(file_path)
        return f"{count} edit attempts on {short}. The approach isn't working. Read the file again, find the exact string, or stop and describe the failure."
    return None


def check_agent_no_output(tool_name, cache):
    if tool_name != "Agent":
        return None
    count = cache.get("agents_without_write", 0)
    if count >= AGENT_NO_OUTPUT_LIMIT:
        return f"{count} agents spawned with no output written. Agents aren't helping. Use Grep/Glob/Read directly or describe what you're looking for."
    return None


def check_time_stall(cache):
    last_write = cache.get("last_write_time", time.time())
    elapsed = time.time() - last_write
    calls = cache.get("calls_since_write", 0)
    if elapsed >= STALL_TIME_SECONDS and calls >= STALL_MIN_CALLS:
        minutes = int(elapsed // 60)
        return f"{minutes} minutes and {calls} tool calls since your last write. You may be stuck. Summarize what you've tried and what's blocking you."
    return None


def block(message):
    print(message, file=sys.stderr)
    sys.exit(2)


def warn(message):
    print(json.dumps({"additionalContext": message}))
    sys.exit(0)


def reset_per_message_counters(cache):
    cache["tool_calls_since_user"] = 0
    cache["agent_calls_since_user"] = 0
    cache["repeat_map"] = {}
    cache["consecutive_reads"] = 0
    cache["warnings_issued"] = 0
    cache["file_read_counts"] = {}
    cache["greps_since_write"] = 0
    cache["edit_targets"] = {}
    cache["agents_without_write"] = 0
    cache["last_write_time"] = time.time()
    cache["calls_since_write"] = 0
    return cache


def main():
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    session_id = hook_input.get("session_id", "unknown")
    hook_event = hook_input.get("hook_event_name", "")

    if hook_event == "UserPromptSubmit":
        cache = load_cache(session_id)
        cache = reset_per_message_counters(cache)
        save_cache(session_id, cache)
        sys.exit(0)

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})

    cache = load_cache(session_id)
    cache = update_counters(tool_name, tool_input, cache)

    msg = check_sensitive_file(tool_name, tool_input)
    if msg:
        save_cache(session_id, cache)
        block(msg)

    msg = check_exact_retry(tool_name, tool_input, cache)
    if msg:
        save_cache(session_id, cache)
        block(msg)

    result = check_volume(cache)
    if result:
        level, msg = result
        save_cache(session_id, cache)
        if level == "block":
            block(msg)
        else:
            warn(msg)

    msg = check_agent_ceiling(tool_name, cache)
    if msg:
        save_cache(session_id, cache)
        block(msg)

    msg = check_mcp_rate(tool_name, cache)
    if msg:
        save_cache(session_id, cache)
        block(msg)

    msg = check_read_spiral(tool_name, cache)
    if msg:
        save_cache(session_id, cache)
        warn(msg)

    msg = check_file_reread(tool_name, tool_input, cache)
    if msg:
        save_cache(session_id, cache)
        warn(msg)

    msg = check_grep_drift(tool_name, cache)
    if msg:
        save_cache(session_id, cache)
        warn(msg)

    msg = check_edit_spiral(tool_name, tool_input, cache)
    if msg:
        save_cache(session_id, cache)
        block(msg)

    msg = check_agent_no_output(tool_name, cache)
    if msg:
        save_cache(session_id, cache)
        warn(msg)

    msg = check_time_stall(cache)
    if msg:
        save_cache(session_id, cache)
        warn(msg)

    save_cache(session_id, cache)
    sys.exit(0)


if __name__ == "__main__":
    main()
