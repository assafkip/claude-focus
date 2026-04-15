#!/usr/bin/env python3
"""
Echo of Prompt — fights Lost-in-the-Middle drift.

Claude forgets the original task by call 20. This re-injects it.

On every PreToolUse call past a configurable interval (default: every 15 calls),
reads .claude/task-context.md (if present) and returns its content as
additionalContext so the model re-sees the original task requirements.

Also fires once at the start of a session so context is anchored early.

Research basis:
  Liu et al. "Lost in the Middle: How Language Models Use Long Contexts" (2023)
  Laban et al. "LLMs Get Lost in Multi-Turn Conversation" (2025)

Hook wiring: PreToolUse
Exit codes:
  0 = allow (optionally with additionalContext injected via stdout JSON)
"""

import json
import os
import sys


CONTEXT_FILE = os.environ.get("CLAUDE_FOCUS_CONTEXT_FILE", ".claude/task-context.md")
ECHO_INTERVAL = int(os.environ.get("CLAUDE_FOCUS_ECHO_INTERVAL", 15))
MAX_CONTEXT_CHARS = int(os.environ.get("CLAUDE_FOCUS_MAX_CONTEXT_CHARS", 4000))


def cache_path(session_id):
    return f"/tmp/claude-focus-echo-{session_id}.json"


def load_cache(session_id):
    path = cache_path(session_id)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"call_count": 0, "last_echo_at": 0}


def save_cache(session_id, cache):
    try:
        with open(cache_path(session_id), "w") as f:
            json.dump(cache, f)
    except IOError:
        pass


def read_context():
    if not os.path.exists(CONTEXT_FILE):
        return None
    try:
        with open(CONTEXT_FILE) as f:
            content = f.read().strip()
    except IOError:
        return None
    if not content:
        return None
    if len(content) > MAX_CONTEXT_CHARS:
        content = content[:MAX_CONTEXT_CHARS] + "\n... [truncated]"
    return content


def should_echo(cache):
    count = cache["call_count"]
    last_echo = cache["last_echo_at"]
    if count == 1:
        return True
    if count - last_echo >= ECHO_INTERVAL:
        return True
    return False


def main():
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    session_id = hook_input.get("session_id", "unknown")
    cache = load_cache(session_id)
    cache["call_count"] += 1

    if not should_echo(cache):
        save_cache(session_id, cache)
        sys.exit(0)

    context = read_context()
    if not context:
        save_cache(session_id, cache)
        sys.exit(0)

    cache["last_echo_at"] = cache["call_count"]
    save_cache(session_id, cache)

    message = (
        f"[echo-of-prompt — re-anchoring task context at call {cache['call_count']}]\n\n"
        f"{context}\n\n"
        f"[Re-read this. Verify the current tool call still serves the original task.]"
    )
    print(json.dumps({"additionalContext": message}))
    sys.exit(0)


if __name__ == "__main__":
    main()
