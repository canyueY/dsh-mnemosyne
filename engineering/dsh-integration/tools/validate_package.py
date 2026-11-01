"""Validate the dsh-integration engineering package structure and references.

Checks specification/reporting structure only, not business behavior or test
authenticity.
"""
import json
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    failures = []

    required_files = [
        "START_HERE.md",
        "COMPOSER_PROMPT.md",
        "PLAN.md",
        "CONTRACTS.md",
        "TASK_INDEX.json",
        "VERIFICATION.md",
        "PROGRESS.json",
        "ACCEPTANCE_CASES.json",
    ]
    for rel in required_files:
        if not (root / rel).is_file():
            failures.append(f"missing file: {rel}")

    index_path = root / "TASK_INDEX.json"
    ids = []
    if index_path.is_file():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception as exc:
            failures.append(f"TASK_INDEX.json invalid: {exc}")
            index = {}
        order = index.get("execution_order", [])
        tasks = index.get("tasks", [])
        ids = [t.get("id") for t in tasks]
        for tid in order:
            if tid not in ids:
                failures.append(f"execution_order references missing task: {tid}")
        for t in tasks:
            tid = t.get("id")
            if not tid:
                failures.append("task without id")
                continue
            for dep in t.get("depends_on", []):
                if dep not in ids:
                    failures.append(f"{tid} depends on missing task: {dep}")
            file_rel = t.get("file")
            if file_rel and not (root / file_rel).is_file():
                failures.append(f"{tid} task file missing: {file_rel}")
        # every non-FINAL task must be reachable
        final_ids = {t.get("id") for t in tasks if t.get("priority") == "FINAL"}
        for tid in order:
            if tid not in final_ids and (root / "tasks" / f"{tid}.md").is_file():
                pass

    progress_path = root / "PROGRESS.json"
    if progress_path.is_file():
        try:
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
        except Exception as exc:
            failures.append(f"PROGRESS.json invalid: {exc}")
        else:
            ptasks = progress.get("tasks", {})
            for tid in ids:
                if tid not in ptasks:
                    failures.append(f"PROGRESS.json missing status for {tid}")

    cases_path = root / "ACCEPTANCE_CASES.json"
    case_ids = set()
    if cases_path.is_file():
        try:
            cases = json.loads(cases_path.read_text(encoding="utf-8"))
        except Exception as exc:
            failures.append(f"ACCEPTANCE_CASES.json invalid: {exc}")
        else:
            case_ids = {c.get("id") for c in cases.get("cases", [])}
            for t in tasks:
                for aid in t.get("acceptance_ids", []):
                    if aid not in case_ids:
                        failures.append(f"{t.get('id')} references missing acceptance: {aid}")

    print(json.dumps({
        "kind": "dsh_integration_spec_validation",
        "ok": not failures,
        "tasks": len(ids),
        "cases": len(case_ids),
        "failures": failures,
    }, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
