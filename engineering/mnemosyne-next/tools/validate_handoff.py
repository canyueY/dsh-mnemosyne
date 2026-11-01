"""Validate the handoff specification, never assert implementation/test success."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath


PACK = Path(__file__).resolve().parents[1]
REPO = PACK.parents[1]


def check(completion: bool = False) -> dict:
    failures: list[str] = []

    def require(condition, message):
        if not condition:
            failures.append(message)

    def local_path(root: Path, relative: str) -> Path:
        pure = PurePosixPath(relative)
        require(not pure.is_absolute() and ".." not in pure.parts and ":" not in relative,
                f"non-local path: {relative}")
        target = (root / relative).resolve()
        require(target.is_relative_to(root.resolve()), f"path outside root: {relative}")
        return target

    def read_json(name):
        return json.loads((PACK / name).read_text(encoding="utf-8"))

    for name in ("START_HERE.md", "COMPOSER_PROMPT.md", "CONTRACTS.md", "VERIFICATION.md",
                 "DEFERRED.md", "REFERENCE_PLAN.md", "reports/README.md",
                 "tools/run_checks.ps1"):
        path = local_path(PACK, name)
        require(path.is_file() and path.stat().st_size > 0, f"missing/empty: {name}")
    index = read_json("TASK_INDEX.json")
    acceptance = read_json("ACCEPTANCE_CASES.json")
    state = read_json("PROGRESS.json")
    sources = read_json("SOURCE_MANIFEST.json")
    tasks = index["tasks"]
    cases = acceptance["cases"]
    ids = [t["id"] for t in tasks]
    require(ids == [f"E{i:02}" for i in range(16)], "expected E00..E15 exactly")
    require(index["execution_order"] == ids, "execution_order mismatch")
    order = {item: n for n, item in enumerate(ids)}
    for task in tasks:
        require(local_path(PACK, task["file"]).is_file(), f"missing task: {task['id']}")
        for dep in task["depends_on"]:
            require(dep in order and order[dep] < order[task["id"]],
                    f"invalid/cyclic dependency: {task['id']} -> {dep}")
        for existing in task["read_first"]:
            require(local_path(REPO, existing).exists(), f"existing anchor missing: {existing}")
        for existing in task["existing_python_tests"]:
            require(local_path(REPO / "service", existing).is_file(),
                    f"existing regression missing: {existing}")
        for planned in task["required_python_tests"]:
            local_path(REPO / "service", planned)
            require(planned.startswith("tests/") and planned.endswith(".py"),
                    f"invalid planned Python test: {planned}")
        for planned in task["required_node_tests"]:
            local_path(REPO, planned)
    case_ids = [c["id"] for c in cases]
    require(case_ids == [f"AC{i:02}" for i in range(1, 51)], "expected unique AC01..AC50")
    require(acceptance["case_count"] == len(cases), "case_count mismatch")
    by_task = {t["id"]: t for t in tasks}
    for case in cases:
        require(case["task"] in by_task, f"unmapped case: {case['id']}")
        if case["task"] not in by_task:
            continue
        task = by_task[case["task"]]
        require(case["id"] in task["acceptance_ids"], f"task omitted case: {case['id']}")
        require(case["test_file"] in task["required_python_tests"],
                f"test mapping outside task: {case['id']}")
        require(case["visibility"] == "public", f"incorrect blind-eval label: {case['id']}")
        for key in ("given", "when", "expected", "test_name", "fixture_family"):
            require(bool(case.get(key)), f"missing {key}: {case['id']}")
    for task in tasks:
        require(set(task["acceptance_ids"]).issubset(case_ids), f"unknown AC: {task['id']}")
    require(set(by_task["E15"]["acceptance_ids"]) == set(case_ids), "final case coverage incomplete")
    require(len(sources["source_texts"]) == 11, "source inventory must contain eleven files")
    require(len({s["file"] for s in sources["source_texts"]}) == 11, "duplicate source names")
    snapshot = local_path(PACK, sources["source_plan"]["snapshot"])
    require(hashlib.sha256(snapshot.read_bytes()).hexdigest() == sources["source_plan"]["sha256"],
            "reference plan snapshot hash mismatch")
    entries = state["tasks"]
    require([e["id"] for e in entries] == ids, "progress task set/order mismatch")
    progress_by_id = {e["id"]: e for e in entries}
    for name in ("current_task", "next_task"):
        require(state.get(name) is None or state[name] in by_task, f"invalid {name}")
    allowed = {"pending", "in_progress", "partial", "blocked", "done"}
    for entry in entries:
        require(entry["status"] in allowed, f"invalid status: {entry['id']}")
        if entry["status"] == "done":
            for dep in by_task[entry["id"]]["depends_on"]:
                require(progress_by_id[dep]["status"] == "done",
                        f"done with incomplete dependency: {entry['id']} -> {dep}")
            report = entry.get("report")
            require(bool(report), f"done without report: {entry['id']}")
            if report:
                require(local_path(PACK, report).is_file(), f"done report missing: {report}")
    if completion:
        require(all(e["status"] == "done" for e in entries), "implementation has incomplete tasks")
        require((PACK / "reports/CASE_RESULTS.json").is_file(), "final case results missing")
        require((PACK / "reports/FINAL_REPORT.md").is_file(), "final report missing")
        # This only checks reporting coverage; it cannot authenticate test results.
        if (PACK / "reports/CASE_RESULTS.json").is_file():
            result = read_json("reports/CASE_RESULTS.json")
            rows = result["cases"] if isinstance(result, dict) else result
            require({r["case_id"] for r in rows} == set(case_ids), "final result coverage mismatch")
            require(len(rows) == len(case_ids), "duplicate final case results")
            for row in rows:
                require(row.get("status") == "passed", f"case not passed: {row.get('case_id')}")
                for key in ("test_locator", "command", "log"):
                    require(bool(row.get(key)), f"missing final {key}: {row.get('case_id')}")
                if row.get("log"):
                    require(local_path(PACK, row["log"]).is_file(),
                            f"case log missing: {row.get('case_id')}")
    return {"kind": "engineering_spec_validation", "ok": not failures,
            "tasks": len(tasks), "public_cases": len(cases), "source_documents": len(sources["source_texts"]),
            "implementation_status": state["implementation_state"], "failures": failures,
            "limits": "Checks specification and reporting structure only, not business behavior or test authenticity."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--completion", action="store_true",
                        help="also require completion report structure; does not verify pass claims")
    args = parser.parse_args()
    try:
        result = check(args.completion)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = {"kind": "engineering_spec_validation", "ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
