#!/usr/bin/env python3
"""
Verification Gate — catches lying self-reports.

Claude self-reports "done" without verifying. This hook doesn't let it.

Reads contracts from .claude/contracts/*.json. Each contract declares a file
that must exist and keys it must contain. On Stop (end of turn), every active
contract is checked. If any fail, the turn is blocked with a diagnostic.

Contract format (.claude/contracts/example.json):
{
  "name": "morning-routine",
  "required_file": "output/morning-log.json",
  "required_keys": ["phases_complete", "follow_ups_drafted"],
  "min_size_bytes": 100,
  "active": true
}

{date} in required_file is replaced with today's YYYY-MM-DD.

Hook wiring: Stop event
Exit codes:
  0 = all contracts pass (or none active)
  2 = at least one contract failed (stderr message goes to Claude)
"""

import datetime
import glob
import json
import os
import sys


CONTRACTS_DIR = os.environ.get("CLAUDE_FOCUS_CONTRACTS_DIR", ".claude/contracts")


def load_contracts():
    if not os.path.isdir(CONTRACTS_DIR):
        return []
    contracts = []
    for path in sorted(glob.glob(os.path.join(CONTRACTS_DIR, "*.json"))):
        try:
            with open(path) as f:
                data = json.load(f)
            if data.get("active", True):
                data["_source"] = path
                contracts.append(data)
        except (json.JSONDecodeError, IOError) as e:
            contracts.append({"_source": path, "_load_error": str(e), "active": True})
    return contracts


def resolve_file_path(template):
    today = datetime.date.today().isoformat()
    return template.replace("{date}", today)


def verify_contract(contract):
    """Return (ok: bool, message: str)."""
    if "_load_error" in contract:
        return False, f"Contract {contract['_source']} failed to load: {contract['_load_error']}"

    name = contract.get("name", contract.get("_source", "unnamed"))
    file_template = contract.get("required_file")
    if not file_template:
        return True, ""

    file_path = resolve_file_path(file_template)

    if not os.path.exists(file_path):
        return False, f"[{name}] required file missing: {file_path}"

    min_size = contract.get("min_size_bytes", 0)
    if min_size and os.path.getsize(file_path) < min_size:
        actual = os.path.getsize(file_path)
        return False, f"[{name}] {file_path} is {actual} bytes, contract requires >= {min_size}"

    required_keys = contract.get("required_keys", [])
    if required_keys:
        try:
            with open(file_path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            return False, f"[{name}] {file_path} unreadable as JSON: {e}"

        if not isinstance(data, dict):
            return False, f"[{name}] {file_path} is not a JSON object (required for key checks)"

        missing = [k for k in required_keys if k not in data]
        if missing:
            return False, f"[{name}] {file_path} missing required keys: {missing}"

        empty = [k for k in required_keys if k in data and not data[k]]
        if empty:
            return False, f"[{name}] {file_path} has empty required keys: {empty}"

    return True, f"[{name}] ok"


def main():
    try:
        json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        pass

    contracts = load_contracts()
    if not contracts:
        sys.exit(0)

    failures = []
    for contract in contracts:
        ok, msg = verify_contract(contract)
        if not ok:
            failures.append(msg)

    if failures:
        header = "VERIFICATION FAILED. You reported the work is done. It isn't."
        body = "\n".join(f"  - {f}" for f in failures)
        hint = "\nDo NOT claim completion until every contract passes. Produce the missing output, then stop."
        print(f"{header}\n{body}{hint}", file=sys.stderr)
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
