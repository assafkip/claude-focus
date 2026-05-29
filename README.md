# claude-focus

**Three hooks that fix Claude Code's attention drift.** ~550 lines of Python. Zero dependencies.

Claude Code has the same executive function problems humans do. It forgets instructions mid-session. Rushes output. Self-reports "done" without verifying. Retries failed calls instead of diagnosing. Spawns agents that produce nothing. Burns 40k tokens to change one line.

Research calls it *Lost in the Middle* ([Liu et al., 2023](https://arxiv.org/abs/2307.03172)) and multi-turn drift ([Laban et al., 2025](https://arxiv.org/abs/2505.06120)). Everyone has felt it.

claude-focus is three PreToolUse / Stop hooks that stop it. Deterministic. No LLM calls in the hot path.

## Install

```bash
git clone https://github.com/assafkip/claude-focus.git ~/.claude/hooks/claude-focus
```

Then wire the hooks into your settings. **If you already have `~/.claude/settings.json`, merge the `hooks` block manually — don't overwrite.** If you don't:

```bash
cp ~/.claude/hooks/claude-focus/examples/settings.example.json ~/.claude/settings.json
```

Open Claude Code. The hooks are live.

### Verify it's wired

In any Claude Code session, ask it to do something that forces a retry loop (e.g., `read a file that doesn't exist, then retry 3 times`). You should see a block message from `token-guard.py` instead of a fourth attempt. If you don't, the hook isn't picking up — check your `~/.claude/settings.json` and confirm the `command` paths resolve.

## What each hook does

### 1. `token-guard.py` — circuit breaker

Counts tool calls per user message. Catches the eleven failure modes the research papers describe:

| Trigger | What it catches | Action |
|---------|-----------------|--------|
| Same tool + same input, 3× | Retry loops without diagnosis | **Block** |
| 50 tool calls since last user message | Runaway execution | **Block** |
| 25 subagents since last user message | Agent-spawn storm | **Block** |
| 30 MCP calls in 60s | API hammering | **Block** |
| 3 edit attempts on same file | Wrong-approach edit spiral | **Block** |
| Attempted edit to `.env`, `.pem`, `.key` | Accidental secret exposure | **Block** |
| 15 consecutive reads, no write | Grep drift | Warn |
| Same file read 3× | Re-read loop | Warn |
| 5 greps since last write | Searching instead of working | Warn |
| 3 agents with no output between them | Agents not producing | Warn |
| 2 min + 10 calls since last write | Time-based stall | Warn |

Secret exfiltration check (row 6) is a security guardrail, not just attention control. Claude can't accidentally touch `.env`, `.pem`, `.key` even if a tool call tries.

Example block message sent back to Claude:

```
You've attempted this exact call 3 times. Stop.
Diagnose the failure and report what's blocking you.
```

Every threshold is overridable via environment variable (`CLAUDE_FOCUS_VOLUME_CEILING`, etc.).

### 2. `verification-gate.py` — catches lying self-reports

Claude claims "done" without checking. This doesn't let it.

Drop a JSON contract in `.claude/contracts/`:

```json
{
  "name": "daily-report",
  "required_file": "output/report-{date}.json",
  "required_keys": ["summary", "action_items", "sources"],
  "min_size_bytes": 200
}
```

On every Stop event (when Claude tries to finish its turn), the gate checks every active contract. File missing? Keys missing? Empty values? It blocks the turn with a diagnostic:

```
VERIFICATION FAILED. You reported the work is done. It isn't.
  - [daily-report] output/report-2026-04-14.json missing required keys: ['action_items']
Do NOT claim completion until every contract passes.
```

Self-reports become unfalsifiable.

### 3. `echo-of-prompt.py` — fights Lost in the Middle

Claude loses the original task by call 20. This re-injects it.

Write the task into `.claude/task-context.md`:

```markdown
# Original task
Refactor the billing module so subscription renewals use the new webhook handler.
Do NOT touch unrelated modules. Do NOT add abstractions beyond what the task requires.
```

Every 15 tool calls (configurable via `CLAUDE_FOCUS_ECHO_INTERVAL`), the hook re-injects it as `additionalContext`, prefixed with:

```
[echo-of-prompt — re-anchoring task context at call 30]
...original task content...
[Re-read this. Verify the current tool call still serves the original task.]
```

Attention drift stops compounding.

## Configuration

Every threshold is an environment variable. Examples:

```bash
export CLAUDE_FOCUS_VOLUME_CEILING=30        # Hard stop at 30 tool calls
export CLAUDE_FOCUS_ECHO_INTERVAL=10         # Re-inject task every 10 calls
export CLAUDE_FOCUS_CONTRACTS_DIR=.claude/contracts
export CLAUDE_FOCUS_CONTEXT_FILE=.claude/task-context.md
```

Full list in each hook's source.

## What this is not

- Not a memory system. See [beads](https://github.com/anthropics/beads) or [claude-mem](https://github.com/thedotmack/claude-mem).
- Not a task manager. See [claude-task-master](https://github.com/eyaltoledano/claude-task-master).
- Not an agent orchestrator. See [claude-flow](https://github.com/ruvnet/claude-flow).

claude-focus is **attention control**. One job. Three hooks.

## Part of claude-cortex

These three hooks are extracted from [claude-cortex](https://github.com/assafkip/claude-cortex), a full Claude Code operating system that runs three of my businesses. If you want the complete framework (agents, skills, canonical memory, cross-instance bridge), see that repo.

## License

MIT

---

I built these hooks for my own Claude Code work and open-sourced them. I ship paid Claude Code kits for founders at https://claudedaddy.gumroad.com, and I build these systems for teams that want one wired to their own setup. Book a call: https://calendar.app.google/cMFvhvDsfi9iyWYy9
