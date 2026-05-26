"""
Bulk onboarding runner — processes multiple employees in parallel batches.

Reads a comma-separated list of employee IDs, processes them in batches of
BATCH_SIZE with STAGGER_SEC delay between thread starts to stay within the
Azure OpenAI rate limit (50 RPM).

Usage (called by dashboard.py as a subprocess):
    python -m agent.bulk_onboarding_runner --employee-ids EMP-0001,EMP-0002,EMP-0003

Stdout protocol (parsed by dashboard.py appendLine):
    ONBOARDING_STARTING:{emp_id}       emitted when a thread begins
    ONBOARDING_CARD:{json}             emitted when a thread completes
"""

import argparse
import json
import os
import re
import threading
import time

from dotenv import load_dotenv

load_dotenv()

BATCH_SIZE  = 3
STAGGER_SEC = 2

_print_lock = threading.Lock()


def _safe_print(text: str) -> None:
    with _print_lock:
        print(text, flush=True)


def _extract_risk_from_audit(emp_id: str) -> tuple:
    """Scan the last 100 audit entries for this employee's risk assessment."""
    log_path = os.path.join(os.path.dirname(__file__), "..", "audit", "audit_log.jsonl")
    try:
        with open(log_path, encoding="utf-8") as f:
            lines = f.readlines()[-100:]
        for line in reversed(lines):
            try:
                entry = json.loads(line)
                if entry.get("entity_id") != emp_id:
                    continue
                if entry.get("action") != "assess_onboarding_risk":
                    continue
                raw = entry.get("output_summary", "")
                try:
                    out = json.loads(raw)
                    return out.get("risk_level", "unknown"), out.get("notification_type", "standard")
                except json.JSONDecodeError:
                    # output was truncated — pull fields with regex
                    m1 = re.search(r'"risk_level"\s*:\s*"(\w+)"', raw)
                    m2 = re.search(r'"notification_type"\s*:\s*"(\w+)"', raw)
                    return (
                        m1.group(1) if m1 else "unknown",
                        m2.group(1) if m2 else "standard",
                    )
            except Exception:
                pass
    except Exception:
        pass
    return "unknown", "standard"


def _run_employee(emp_id: str) -> None:
    from agent.agent_runner import HealthcareAIAgent
    from agent.tools.onboarding_tools import get_employee_details

    card: dict = {"employee_id": emp_id}

    # Pre-fetch employee details so the card has name/dept before the agent runs
    try:
        raw = get_employee_details(emp_id)
        data = json.loads(raw)
        if data.get("success"):
            emp = data["employee"]
            card["employee_name"] = f"{emp['first_name']} {emp['last_name']}"
            card["department"]    = emp.get("department", "")
            card["position"]      = emp.get("position", "")
    except Exception:
        pass

    _safe_print(f"ONBOARDING_STARTING:{emp_id}")
    _safe_print(f"[bulk] Starting {emp_id} — {card.get('employee_name', '?')}")

    try:
        agent = HealthcareAIAgent()
        msg = (
            f"Process the full onboarding workflow for employee {emp_id}. "
            "Retrieve their details from PrimeHR, generate the onboarding form package, "
            "upload it to storage, and notify their manager."
        )
        agent.run("onboarding", msg)
        card["status"] = "success"
        risk, notif = _extract_risk_from_audit(emp_id)
        card["risk_level"]        = risk
        card["notification_type"] = notif
    except Exception as exc:
        card["status"] = "failed"
        card["error"]  = str(exc)[:120]

    _safe_print(f"ONBOARDING_CARD:{json.dumps(card)}")
    _safe_print(f"[bulk] Done {emp_id} — {card['status']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk Employee Onboarding Runner")
    parser.add_argument(
        "--employee-ids", required=True,
        help="Comma-separated employee IDs (e.g. EMP-0001,EMP-0002,EMP-0010)",
    )
    args = parser.parse_args()

    employee_ids = [e.strip().upper() for e in args.employee_ids.split(",") if e.strip()]
    if not employee_ids:
        print("[bulk] No employee IDs provided. Exiting.", flush=True)
        return

    batches = [employee_ids[i : i + BATCH_SIZE] for i in range(0, len(employee_ids), BATCH_SIZE)]

    print(
        f"[bulk] Processing {len(employee_ids)} employee(s) in "
        f"{len(batches)} batch(es) of up to {BATCH_SIZE} — "
        f"{STAGGER_SEC}s stagger between starts",
        flush=True,
    )

    for bi, batch in enumerate(batches):
        print(f"[bulk] === Batch {bi + 1}/{len(batches)}: {batch} ===", flush=True)
        threads: list[threading.Thread] = []
        for i, emp_id in enumerate(batch):
            if i > 0:
                time.sleep(STAGGER_SEC)
            t = threading.Thread(target=_run_employee, args=(emp_id,), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        print(f"[bulk] Batch {bi + 1} complete.", flush=True)

    print("[bulk] All employees processed.", flush=True)


if __name__ == "__main__":
    main()
