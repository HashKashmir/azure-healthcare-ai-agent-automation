"""
Healthcare AI Agent — Full Demo
Run this file to demo all four automations in sequence.

    python demo.py
"""

import os
import subprocess
import sys
import time
import requests

BASE = os.path.dirname(os.path.abspath(__file__))


def header(title: str) -> None:
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def wait_for_api(url: str, name: str, retries: int = 15) -> bool:
    for i in range(retries):
        try:
            requests.get(url, timeout=2)
            print(f"[demo] {name} is ready.")
            return True
        except Exception:
            time.sleep(1)
    print(f"[demo] WARNING: {name} did not respond at {url}. Continuing anyway.")
    return False


def run_agent(automation: str, extra_args: list[str] = []) -> None:
    cmd = [sys.executable, "-m", "agent.agent_runner", "--automation", automation] + extra_args
    subprocess.run(cmd, cwd=BASE)


def main():
    header("Starting Mock APIs")

    PrimeHR = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mock_apis.primehr_api:app", "--port", "8001"],
        cwd=BASE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    ClaimBridge = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mock_apis.claimbridge_api:app", "--port", "8002"],
        cwd=BASE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    wait_for_api("http://localhost:8001/health", "PrimeHR HR API (port 8001)")
    wait_for_api("http://localhost:8002/health", "ClaimBridge Billing API (port 8002)")

    try:
        # ── Automation 1: Onboarding ──────────────────────────────────────────
        header("AUTOMATION 1 — Employee Onboarding (PrimeHR)")
        print("Fetching employee EMP-0023, generating onboarding package,")
        print("uploading to Azure Blob, and emailing their manager.\n")
        run_agent("onboarding", ["--employee-id", "EMP-0023"])

        input("\nPress Enter to continue to Automation 2...")

        # ── Automation 2: Patient Intake ──────────────────────────────────────
        header("AUTOMATION 2 — Patient Intake (Document Intelligence)")
        print("Generating a synthetic patient intake PDF and uploading to Azure Blob...")
        subprocess.run([sys.executable, "scripts/generate_intake_pdf.py"], cwd=BASE)

        url_file = os.path.join(BASE, "data", "intake_blob_url.txt")
        if os.path.exists(url_file):
            with open(url_file) as f:
                blob_url = f.read().strip()
            print(f"\nExtracting fields, validating insurance, storing indexed record...\n")
            run_agent("intake", ["--blob-url", blob_url])
        else:
            print("[demo] Could not find intake blob URL — skipping Automation 2.")

        input("\nPress Enter to continue to Automation 3...")

        # ── Automation 3: Billing Triage ──────────────────────────────────────
        header("AUTOMATION 3 — Billing Claim Triage (ClaimBridge)")
        print("Fetching top 5 exception claims, validating ICD-10 codes,")
        print("auto-resolving what's possible, routing the rest to staff.\n")
        run_agent("billing")

        input("\nPress Enter to continue to Automation 4...")

        # ── Automation 4: Weekly Report ───────────────────────────────────────
        header("AUTOMATION 4 — Weekly Financial Report")
        print("Analyzing 4 weeks of financial metrics, generating charts,")
        print("building the HTML report, and emailing it to the admin list.\n")
        run_agent("report", ["--mode", "financial"])

        header("DEMO COMPLETE")
        print("All four automations finished.")
        print("- Check your Gmail inbox for the weekly financial report.")
        print("- Open data/reports/ to view the HTML report file.")
        print("- Run: Get-Content audit/audit_log.jsonl | Select-Object -Last 30")
        print("  to see the full audit trail of every action taken.\n")

    finally:
        PrimeHR.terminate()
        ClaimBridge.terminate()


if __name__ == "__main__":
    main()
