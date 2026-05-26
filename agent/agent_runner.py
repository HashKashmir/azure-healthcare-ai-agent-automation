"""
Healthcare AI Agent — main entry point.

Uses Azure OpenAI Chat Completions API with function calling to orchestrate
multi-step tool calls across all four healthcare administration automations.
This approach works with any Azure OpenAI model (including o4-mini) and gives
full control over the tool-call loop with audit logging at every step.

Usage:
    python -m agent.agent_runner --automation onboarding --employee-id EMP-0023
    python -m agent.agent_runner --automation billing
    python -m agent.agent_runner --automation intake --blob-url <url>
    python -m agent.agent_runner --automation report --mode financial
"""

import argparse
import inspect
import json
import os
import re

from dotenv import load_dotenv

load_dotenv()

# ── Tool imports ──────────────────────────────────────────────────────────────

from agent.tools.onboarding_tools import (
    get_employee_details,
    fill_onboarding_form,
    assess_onboarding_risk,
    store_document,
    notify_employee,
    notify_manager,
)
from agent.tools.billing_tools import (
    get_billing_exception_queue,
    get_claim_details,
    validate_icd10_code,
    classify_claim,
    resubmit_claim,
    route_to_staff,
    record_claim_outcome,
    notify_staff_claim_routed,
    generate_billing_report,
    send_billing_summary_email,
)
from agent.tools.intake_tools import (
    extract_document_fields,
    validate_insurance,
    store_indexed_record,
    notify_patient,
    notify_staff_ineligible,
)
from agent.tools.reporting_tools import (
    fetch_data_csv,
    analyze_trends,
    generate_charts,
    build_report,
    send_report_email,
)
from audit.audit_logger import write_audit_entry

# ── Tool registry ─────────────────────────────────────────────────────────────

TOOL_REGISTRY: dict[str, callable] = {
    "get_employee_details":        get_employee_details,
    "fill_onboarding_form":        fill_onboarding_form,
    "assess_onboarding_risk":      assess_onboarding_risk,
    "store_document":              store_document,
    "notify_employee":             notify_employee,
    "notify_manager":              notify_manager,
    "extract_document_fields":     extract_document_fields,
    "validate_insurance":          validate_insurance,
    "store_indexed_record":        store_indexed_record,
    "notify_patient":              notify_patient,
    "notify_staff_ineligible":     notify_staff_ineligible,
    "get_billing_exception_queue": get_billing_exception_queue,
    "get_claim_details":           get_claim_details,
    "validate_icd10_code":         validate_icd10_code,
    "classify_claim":              classify_claim,
    "resubmit_claim":              resubmit_claim,
    "route_to_staff":              route_to_staff,
    "record_claim_outcome":        record_claim_outcome,
    "notify_staff_claim_routed":   notify_staff_claim_routed,
    "generate_billing_report":     generate_billing_report,
    "send_billing_summary_email":  send_billing_summary_email,
    "fetch_data_csv":              fetch_data_csv,
    "analyze_trends":              analyze_trends,
    "generate_charts":             generate_charts,
    "build_report":                build_report,
    "send_report_email":           send_report_email,
}

# ── Tool schema builder ───────────────────────────────────────────────────────

def _python_type_to_json(annotation) -> str:
    return {str: "string", int: "integer", float: "number", bool: "boolean"}.get(annotation, "string")


def _build_tool_schema(fn: callable) -> dict:
    """Convert a Python function into an OpenAI function-calling tool schema."""
    sig  = inspect.signature(fn)
    doc  = inspect.getdoc(fn) or ""
    # Use only the first sentence of the docstring to minimise content-filter surface
    first_sentence = doc.split(".")[0].strip() + "."
    desc = first_sentence

    properties: dict = {}
    required: list   = []

    for name, param in sig.parameters.items():
        ann = param.annotation if param.annotation != inspect.Parameter.empty else str
        properties[name] = {"type": _python_type_to_json(ann)}
        if param.default is inspect.Parameter.empty:
            required.append(name)

    return {
        "type": "function",
        "function": {
            "name":        fn.__name__,
            "description": desc,
            "parameters": {
                "type":       "object",
                "properties": properties,
                "required":   required,
            },
        },
    }

# ── Automation config ─────────────────────────────────────────────────────────

def _load_prompt(name: str) -> str:
    path = os.path.join(os.path.dirname(__file__), "prompts", f"{name}.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    return f"You are the Healthcare AI {name} agent."


AUTOMATION_CONFIG = {
    "onboarding": {
        "prompt_file": "onboarding_prompt",
        "agent_name":  "Healthcare AI HR Onboarding Agent",
        "tools":       [get_employee_details, fill_onboarding_form, assess_onboarding_risk, store_document, notify_employee, notify_manager],
    },
    "intake": {
        "prompt_file": "intake_prompt",
        "agent_name":  "Healthcare AI Patient Intake Agent",
        "tools":       [extract_document_fields, validate_insurance, store_indexed_record, notify_patient, notify_staff_ineligible],
    },
    "billing": {
        "prompt_file": "billing_classifier_prompt",
        "agent_name":  "Healthcare AI Billing Triage Agent",
        "tools":       [
            get_billing_exception_queue, get_claim_details, validate_icd10_code,
            classify_claim, resubmit_claim, route_to_staff,
            record_claim_outcome, notify_staff_claim_routed,
            generate_billing_report, send_billing_summary_email,
        ],
    },
    "report": {
        "prompt_file": "report_analysis_prompt",
        "agent_name":  "Healthcare AI Analytics Agent",
        "tools":       [fetch_data_csv, analyze_trends, generate_charts, build_report, send_report_email],
    },
}

# ── Agent runner ──────────────────────────────────────────────────────────────

class HealthcareAIAgent:
    """
    Orchestrates tool-calling agents for each Healthcare AI automation using the
    Azure OpenAI Chat Completions API with function calling.
    """

    def __init__(self):
        from openai import OpenAI
        # The AZURE_OPENAI_ENDPOINT in .env already contains the full base path
        # (e.g. https://resource.openai.azure.com/openai/v1) — Azure AI Foundry format.
        # Using plain OpenAI with base_url avoids the double-path issue that AzureOpenAI causes.
        self.client = OpenAI(
            base_url=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
            api_key=os.getenv("AZURE_API_KEY", ""),
            max_retries=5,
        )
        self.model = os.getenv("MODEL_DEPLOYMENT_NAME", "o4-mini")
        print(f"[agent] OpenAI client ready (Azure AI Foundry) — model: {self.model}")

    def run(self, automation: str, user_message: str) -> str:
        """
        Run one automation end-to-end. Posts the user message and then
        loops, dispatching every tool call the model requests until it
        produces a final text response.
        """
        cfg          = AUTOMATION_CONFIG[automation]
        tools        = [_build_tool_schema(fn) for fn in cfg["tools"]]
        instructions = _load_prompt(cfg["prompt_file"])
        tool_names   = [t["function"]["name"] for t in tools]

        print(f"\n[agent] Starting '{automation}' — tools: {tool_names}")

        messages = [
            {"role": "system", "content": instructions},
            {"role": "user",   "content": user_message},
        ]

        iteration = 0
        max_iterations = 60

        while iteration < max_iterations:
            iteration += 1
            print(f"[agent] Calling model (iteration {iteration})...")

            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
            )

            choice = response.choices[0]
            finish = choice.finish_reason
            print(f"[agent] finish_reason: {finish}")

            if finish == "stop":
                return choice.message.content or ""

            if finish == "tool_calls":
                # Append the assistant's tool-call message to the thread
                messages.append(choice.message)

                for tc in choice.message.tool_calls:
                    fn_name = tc.function.name
                    try:
                        fn_args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        fn_args = {}

                    print(f"[agent]   -> {fn_name}({list(fn_args.keys())})")

                    fn = TOOL_REGISTRY.get(fn_name)
                    if fn is None:
                        output = json.dumps({"error": f"Unknown tool '{fn_name}'"})
                    else:
                        try:
                            output = fn(**fn_args)
                            if not isinstance(output, str):
                                output = json.dumps(output)
                        except Exception as e:
                            output = json.dumps({"error": str(e)})

                    print(f"[agent]   <- {fn_name}: {output[:120]}...")

                    write_audit_entry(
                        automation=automation,
                        action=fn_name,
                        entity_id=(
                            fn_args.get("employee_id")
                            or fn_args.get("claim_id")
                            or fn_args.get("report_mode")
                            or "—"
                        ),
                        input_summary=json.dumps(fn_args)[:300],
                        output_summary=output[:300],
                    )

                    messages.append({
                        "role":         "tool",
                        "tool_call_id": tc.id,
                        "content":      output,
                    })

            else:
                # length or content_filter — return whatever we have
                return choice.message.content or f"Run ended with finish_reason='{finish}'"

        return "Run exceeded maximum iterations."


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Healthcare AI Agent")
    parser.add_argument(
        "--automation", required=True,
        choices=["onboarding", "intake", "billing", "report"],
    )
    parser.add_argument("--employee-id", default="EMP-0023")
    parser.add_argument("--blob-url",    default="")
    parser.add_argument("--mode",        default="financial",
                        choices=["financial", "clinical", "billing", "all"])
    args = parser.parse_args()

    automation = args.automation

    if automation == "onboarding":
        msg = (
            f"Process the full onboarding workflow for employee {args.employee_id}. "
            "Retrieve their details from PrimeHR, generate the onboarding form package, "
            "upload it to storage, and notify their manager."
        )
    elif automation == "intake":
        blob = args.blob_url or "https://healthcareprojectdata.blob.core.windows.net/intake-pdfs/sample_intake.pdf"
        msg = (
            f"Process the patient intake document at {blob}. "
            "Extract all fields, validate insurance eligibility, and store the indexed record."
        )
    elif automation == "billing":
        msg = (
            "Fetch the current billing exception queue and triage each claim. "
            "Auto-resolve all resolvable exceptions and route the rest to staff. "
            "Provide a full summary at the end."
        )
    elif args.mode == "all":
        agent = HealthcareAIAgent()
        combined = []
        for m in ["financial", "clinical", "billing"]:
            print(f"\n[runner] ── Starting {m.upper()} report ──")
            msg = (
                f"Run the weekly {m} report pipeline. "
                "Fetch the metrics data, analyze it, generate charts, build the report, "
                "and email it to the configured admin list."
            )
            combined.append(agent.run("report", msg))
        result = "\n\n".join(combined)
    else:
        msg = (
            f"Run the weekly {args.mode} report pipeline. "
            "Fetch the metrics data, analyze it, generate charts, build the report, "
            "and email it to the configured admin list."
        )

    if args.mode != "all":
        agent = HealthcareAIAgent()
        result = agent.run(automation, msg)

    print("\n" + "=" * 60)
    print("AGENT RESULT:")
    print("=" * 60)
    # Encode to ASCII-safe output for Windows terminals that don't support UTF-8
    safe_result = result.encode("ascii", errors="replace").decode("ascii")
    print(safe_result)


if __name__ == "__main__":
    main()
