# claude-focus

**Three hooks that stop Claude Code from drifting, lying about "done," and burning tokens.** ~550 lines of Python. Zero dependencies. MIT. Drop them into `~/.claude/hooks/` and wire three lines of settings.

```bash
git clone https://github.com/assafkip/claude-focus.git ~/.claude/hooks/claude-focus
cp ~/.claude/hooks/claude-focus/examples/settings.example.json ~/.claude/settings.json
```

Open Claude Code. The hooks are live. If you already have a `~/.claude/settings.json`, merge the `hooks` block instead of overwriting.

The three hooks are `token-guard.py` (circuit breaker), `verification-gate.py` (catches false "done" reports), and `echo-of-prompt.py` (re-injects the original task so the model stops drifting). They run as PreToolUse / UserPromptSubmit / Stop hooks. No LLM call sits in the hot path, so they are fast and deterministic.

Version 1.1 - updated 2026-05-28

## How do I stop Claude Code from running away and burning tokens?

Add a `PreToolUse` hook that counts tool calls and blocks the turn when it sees a runaway pattern. `token-guard.py` is that hook. It catches the model retrying a failed call three times, making 50 tool calls with no user input, spawning subagents that produce nothing, or hammering an MCP server. When a pattern trips, it blocks the turn (exit code 2) and tells the model what to do instead.

| Trigger | What it catches | Action |
|---------|-----------------|--------|
| Same tool + same input, 3x | Retry loops without diagnosis | Block |
| 50 tool calls since last user message | Runaway execution | Block |
| 25 subagents since last user message | Agent-spawn storm | Block |
| 30 MCP calls in 60s | API hammering | Block |
| 3 edit attempts on same file | Wrong-approach edit spiral | Block |
| Attempted edit to `.env`, `.pem`, `.key` | Accidental secret exposure | Block |
| 15 consecutive reads, no write | Grep drift | Warn |
| Same file read 3x | Re-read loop | Warn |
| 5 greps since last write | Searching instead of working | Warn |
| 3 agents with no output between them | Agents not producing | Warn |
| 2 min + 10 calls since last write | Time-based stall | Warn |

Every threshold is an environment variable (`CLAUDE_FOCUS_VOLUME_CEILING`, `CLAUDE_FOCUS_RETRY_LIMIT`, and so on), so you can tune it without touching the code.

## Why does Claude Code lose track of the task halfway through?

The drift has research names. "Lost in the Middle" (Liu et al., 2023) describes how models lose the middle of a long context. Multi-turn drift (Laban et al., 2025) describes how they degrade over a long back-and-forth. The fix is not a better prompt. It is a hook that does not depend on the model behaving.

`echo-of-prompt.py` fights this directly. Write the task into `.claude/task-context.md`, and every 15 tool calls (configurable via `CLAUDE_FOCUS_ECHO_INTERVAL`) the hook re-injects it as `additionalContext`:

```
[echo-of-prompt - re-anchoring task context at call 30]
...original task content...
[Re-read this. Verify the current tool call still serves the original task.]
```

Attention drift stops compounding because the original requirements keep coming back into the model's working context.

## How do I stop Claude Code from claiming it finished work it never did?

Claude self-reports "done" without checking. `verification-gate.py` does not let it. Drop a JSON contract in `.claude/contracts/`:

```json
{
  "name": "daily-report",
  "required_file": "output/report-{date}.json",
  "required_keys": ["summary", "action_items", "sources"],
  "min_size_bytes": 200
}
```

On every Stop event (when Claude tries to end its turn), the gate checks every active contract. File missing, keys missing, or empty values? It blocks the turn with a diagnostic:

```
VERIFICATION FAILED. You reported the work is done. It isn't.
  - [daily-report] output/report-2026-05-28.json missing required keys: ['action_items']
Do NOT claim completion until every contract passes.
```

Self-reports become falsifiable. The turn cannot end until the file on disk actually matches the contract.

## What is a Claude Code hook and how do I write one?

A hook is a command Claude Code runs at a lifecycle event: before a tool call (`PreToolUse`), when you submit a prompt (`UserPromptSubmit`), when the turn ends (`Stop`), and others. The hook reads a JSON payload on stdin and signals back with its exit code. Exit 0 allows the action. Exit 2 blocks it and sends stderr back to the model as feedback. All three hooks in this repo follow that contract, which is why they work without any LLM in the loop.

## claude-focus vs full Claude Code frameworks

The big free frameworks are kitchen sinks: hundreds of skills, a whole operating system to install when you wanted a seatbelt. The bare guardrail snippets floating around GitHub tend to have no tests, no tuning profiles, and no install path. claude-focus is attention control done well: three small hooks that drop into any setup.

| | claude-focus (free) | Kitchen-sink framework | Token Guard Kit (paid) |
|---|---|---|---|
| Runaway circuit breaker | Yes | Buried in a large system | Yes, hardened |
| False-done verification gate | Yes | No | Yes, deadlock-proof |
| Task re-anchoring | Yes | No | Yes |
| Install footprint | Three files | The whole framework | Two minutes, any repo |
| Tests | No | Varies | 35 pytest unit tests |
| Tuning profiles | No | No | safe / aggressive / paranoid |
| Per-OS setup guides | No | Varies | macOS, Linux, Windows |

## How do I verify the hooks are actually wired?

In any Claude Code session, ask it to do something that forces a retry loop, like "read a file that doesn't exist, then retry 3 times." You should see a block message from `token-guard.py` instead of a fourth attempt. If you don't, the hook isn't picking up. Check your `~/.claude/settings.json` and confirm the `command` paths resolve to the cloned files.

## Can I tune the thresholds?

Yes. Every threshold is an environment variable. Set them in your shell before launching Claude Code:

```bash
export CLAUDE_FOCUS_VOLUME_CEILING=30        # hard stop at 30 tool calls instead of 50
export CLAUDE_FOCUS_RETRY_LIMIT=2            # block after 2 identical calls instead of 3
export CLAUDE_FOCUS_ECHO_INTERVAL=10         # re-inject task every 10 calls
export CLAUDE_FOCUS_CONTRACTS_DIR=.claude/contracts
export CLAUDE_FOCUS_CONTEXT_FILE=.claude/task-context.md
```

The defaults are sane for everyday coding. Full list lives in each hook's source.

## What this is not

- Not a memory system. See [beads](https://github.com/anthropics/beads) or [claude-mem](https://github.com/thedotmack/claude-mem).
- Not a task manager. See [claude-task-master](https://github.com/eyaltoledano/claude-task-master).
- Not an agent orchestrator. See [claude-flow](https://github.com/ruvnet/claude-flow).

claude-focus is attention control. It catches deterministic patterns. It does not read intent and it will not fix a vague prompt. It stops the spinning, not the thinking.

## License

MIT. See [LICENSE](LICENSE).

---

I built these hooks for my own Claude Code work and open-sourced them. This repo is the free core.

The full **Token Guard Kit** adds a 35-test pytest suite shipped green, three tuning profiles (safe / aggressive / paranoid), a deadlock-proof Stop gate, an instruction-budget preflight CLI, advanced pipeline reliability modules, and a two-minute installer with per-OS guides: https://claudedaddy.gumroad.com/l/yybwrk

More kits for founders building on Claude Code: https://claudedaddy.io

Want one wired to your own setup, or a larger Claude Code reliability system built around it? I build these for teams. Book a call: https://calendar.app.google/cMFvhvDsfi9iyWYy9
