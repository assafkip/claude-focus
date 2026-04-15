# Original task

Refactor the billing module so subscription renewals use the new webhook handler.
Do NOT touch unrelated modules. Do NOT add new abstractions beyond what the task requires.

## Acceptance

- `src/billing/renewals.py` uses `webhooks.renewal_handler`
- Existing tests in `tests/billing/` still pass
- No new files outside `src/billing/`

## Out of scope

- Logging, metrics, error reporting overhauls
- Other modules that happen to look similar
- "Opportunistic" cleanup
