"""
Bulk intake runner — processes multiple patients from an uploaded Excel file.

Reads patient records from a JSON file written by the dashboard upload endpoint,
generates a PDF per patient, uploads to Azure Blob (or local-dev fallback),
then runs the intake agent for each one in parallel batches.

Usage (called by dashboard.py as a subprocess):
    python -m agent.bulk_intake_runner --patients-file data/bulk_intake_patients.json

Stdout protocol:
    INTAKE_STARTING:{patient_name}     emitted when a thread begins
    INTAKE_CARD:{json}                 emitted when a thread completes
"""

import argparse
import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

BATCH_SIZE  = 3
STAGGER_SEC = 2

_print_lock = threading.Lock()


def _safe_print(text: str) -> None:
    with _print_lock:
        print(text, flush=True)


def _run_patient(patient: dict) -> None:
    import sys as _sys
    # Ensure project root is on path so scripts/ and agent/ imports work
    _sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

    from scripts.generate_intake_pdf import generate_pdf, upload_and_get_sas_url
    from agent.tools.intake_tools import validate_insurance
    from agent.agent_runner import HealthcareAIAgent

    key  = uuid.uuid4().hex[:8]
    name = f"{patient.get('first_name', '')} {patient.get('last_name', '')}".strip() or "Unknown"

    card: dict = {
        "patient_name": name,
        "insurance_id": patient.get("insurance_id", ""),
        "pdf_key":      key,
        "status":       "running",
    }

    _safe_print(f"INTAKE_STARTING:{name}")
    _safe_print(f"[bulk-intake] Starting {name}")

    # Pre-check eligibility so the card always shows the right badge
    try:
        dos = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        elig = json.loads(validate_insurance(patient.get("insurance_id", "INS-999"), name, dos))
        card["is_eligible"] = elig.get("is_eligible", False)
        card["plan_name"]   = elig.get("plan_name", "")
    except Exception:
        card["is_eligible"] = None
        card["plan_name"]   = ""

    try:
        data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
        os.makedirs(data_dir, exist_ok=True)

        # Write patient data file for the intake tool's local-dev fallback
        patient_json_path = os.path.join(data_dir, f"intake_patient_{key}.json")
        with open(patient_json_path, "w", encoding="utf-8") as f:
            json.dump(patient, f)

        # Generate PDF using this patient's data
        pdf_path = os.path.join(data_dir, f"intake_bulk_{key}.pdf")
        generate_pdf(pdf_path, patient=patient)

        # Try Azure Blob upload; fall back to local-dev URL if unavailable
        try:
            blob_url = upload_and_get_sas_url(pdf_path, blob_name=f"intake_bulk_{key}.pdf")
        except Exception:
            blob_url = f"local-dev://{key}"
            _safe_print(f"[bulk-intake] Azure Blob unavailable — using local-dev fallback for {name}")

        agent = HealthcareAIAgent()
        msg = (
            f"Process the patient intake document at {blob_url}. "
            "Extract all fields, validate insurance eligibility, and store the indexed record."
        )
        agent.run("intake", msg)
        card["status"] = "success"

    except Exception as exc:
        card["status"] = "failed"
        card["error"]  = str(exc)[:120]

    _safe_print(f"INTAKE_CARD:{json.dumps(card)}")
    _safe_print(f"[bulk-intake] Done {name} — {card['status']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk Patient Intake Runner")
    parser.add_argument(
        "--patients-file", required=True,
        help="Path to JSON file containing list of patient dicts (written by upload endpoint)",
    )
    args = parser.parse_args()

    patients_path = args.patients_file
    if not os.path.exists(patients_path):
        print(f"[bulk-intake] Patients file not found: {patients_path}", flush=True)
        sys.exit(1)

    with open(patients_path, encoding="utf-8") as f:
        patients: list[dict] = json.load(f)

    if not patients:
        print("[bulk-intake] No patients to process.", flush=True)
        return

    batches = [patients[i : i + BATCH_SIZE] for i in range(0, len(patients), BATCH_SIZE)]

    print(
        f"[bulk-intake] Processing {len(patients)} patient(s) in "
        f"{len(batches)} batch(es) of up to {BATCH_SIZE} — "
        f"{STAGGER_SEC}s stagger between starts",
        flush=True,
    )

    for bi, batch in enumerate(batches):
        names = [f"{p.get('first_name','')} {p.get('last_name','')}".strip() for p in batch]
        print(f"[bulk-intake] === Batch {bi + 1}/{len(batches)}: {names} ===", flush=True)
        threads: list[threading.Thread] = []
        for i, patient in enumerate(batch):
            if i > 0:
                time.sleep(STAGGER_SEC)
            t = threading.Thread(target=_run_patient, args=(patient,), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        print(f"[bulk-intake] Batch {bi + 1} complete.", flush=True)

    print("[bulk-intake] All patients processed.", flush=True)


if __name__ == "__main__":
    main()
