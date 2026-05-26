"""
Automation 3 — Billing Workflow Triage tools.

The agent calls these to classify, validate, auto-resolve, or route
billing claim exceptions from the ClaimBridge queue.
"""

import json
import os

import requests
from dotenv import load_dotenv

load_dotenv()

CLAIMBRIDGE_URL = os.getenv("CLAIMBRIDGE_API_URL", "http://localhost:8002")
NLM_ICD10_URL   = "https://clinicaltables.nlm.nih.gov/api/icd10cm/v3/search"

# Rejection categories that the agent can auto-resolve without human review
AUTO_RESOLVABLE_PATTERNS = [
    "missing prior authorization",
    "invalid icd-10 code",
    "incorrect billing modifier",
    "claim submitted with incomplete patient demographics",
    "duplicate claim submission",
]

# State collected during the current run (cleared at each new run)
_claim_outcomes: list = []
_fetched_queue: dict = {}   # claim_id → summary data at queue-fetch time (before resubmit clears fields)
_classify_cache: dict = {}  # claim_id → {resolution_action, icd10_valid} from classify_claim


# ── Tool 1: Fetch the exception queue ────────────────────────────────────────

def get_billing_exception_queue(limit: int = 20) -> str:
    """
    Fetch the current billing exception queue from the ClaimBridge system.
    Returns claims with status 'exception' or 'denied' that need triage.

    Args:
        limit: Maximum number of claims to return (default 20, max 100).

    Returns:
        JSON string with the list of exception claims and queue depth.
    """
    global _claim_outcomes, _fetched_queue, _classify_cache
    _claim_outcomes = []
    _fetched_queue = {}
    _classify_cache = {}
    try:
        resp = requests.get(
            f"{CLAIMBRIDGE_URL}/claims/exceptions",
            params={"limit": min(limit, 100)},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        for exc in data.get("exceptions", []):
            _fetched_queue[exc["claim_id"]] = exc
        return json.dumps({"success": True, **data})
    except requests.exceptions.ConnectionError:
        return json.dumps({
            "success": False,
            "error": f"Cannot reach ClaimBridge API at {CLAIMBRIDGE_URL}. Ensure it is running.",
        })
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


# ── Tool 2: Get full claim details ────────────────────────────────────────────

def get_claim_details(claim_id: str) -> str:
    """
    Retrieve the full record for a specific billing claim from ClaimBridge,
    including ICD-10 code, procedure code, payer, rejection reason, and amounts.

    Args:
        claim_id: The claim ID in CLM-XXXXXXXX format.

    Returns:
        JSON string with complete claim details.
    """
    try:
        resp = requests.get(f"{CLAIMBRIDGE_URL}/claims/{claim_id}", timeout=5)
        resp.raise_for_status()
        return json.dumps({"success": True, "claim": resp.json()})
    except requests.exceptions.ConnectionError:
        return json.dumps({"success": False, "error": "Cannot reach ClaimBridge API."})
    except requests.HTTPError:
        return json.dumps({"success": False, "error": f"Claim '{claim_id}' not found."})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


# ── Tool 3: Validate ICD-10 code via NLM ─────────────────────────────────────

def validate_icd10_code(code: str) -> str:
    """
    Validate an ICD-10-CM diagnosis code against the NLM public API.
    Returns whether the code is valid and its official description.

    Args:
        code: ICD-10-CM code to validate (e.g. 'E11.9', 'M54.5').

    Returns:
        JSON string with is_valid flag, code, description, and any suggestions.
    """
    try:
        resp = requests.get(
            NLM_ICD10_URL,
            params={"sf": "code,name", "terms": code, "maxList": 5},
            timeout=8,
        )
        resp.raise_for_status()
        data    = resp.json()
        total   = data[0] if data else 0
        matches = data[3] if len(data) > 3 else []

        exact = next(
            (m for m in matches if m[0].upper() == code.strip().upper()), None
        )

        return json.dumps({
            "success":       True,
            "code":          code,
            "is_valid":      exact is not None,
            "description":   exact[1] if exact else None,
            "total_matches": total,
            "suggestions":   [{"code": m[0], "description": m[1]} for m in matches[:5]],
        })
    except requests.exceptions.Timeout:
        return json.dumps({"success": False, "error": "NLM API timed out.", "code": code})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "code": code})


# ── Tool 4: Classify the claim rejection ─────────────────────────────────────

def classify_claim(claim_id: str, rejection_reason: str, icd10_valid: bool) -> str:
    """
    Classify a billing claim exception and determine whether it can be
    auto-resolved by the agent or must be escalated to a human reviewer.

    Args:
        claim_id:         The claim ID.
        rejection_reason: The rejection reason string from ClaimBridge.
        icd10_valid:      Whether the claim's ICD-10 code passed NLM validation.

    Returns:
        JSON string with classification (auto_resolvable | needs_human_review),
        resolution_action, and recommended_priority.
    """
    reason_lower    = rejection_reason.lower()
    auto_resolvable = any(pat in reason_lower for pat in AUTO_RESOLVABLE_PATTERNS)

    if not icd10_valid and "invalid icd-10" not in reason_lower:
        auto_resolvable   = False
        resolution_action = "validate_and_correct_icd10_code"
        priority          = "high"
    elif "missing prior authorization" in reason_lower:
        resolution_action = "obtain_and_resubmit_with_prior_auth"
        priority          = "medium"
    elif "invalid icd-10" in reason_lower:
        resolution_action = "correct_icd10_code_and_resubmit"
        priority          = "medium"
    elif "duplicate claim" in reason_lower:
        resolution_action = "verify_and_close_duplicate"
        priority          = "low"
    elif "incorrect billing modifier" in reason_lower:
        resolution_action = "correct_modifier_and_resubmit"
        priority          = "medium"
    elif "incomplete patient demographics" in reason_lower:
        resolution_action = "complete_demographics_and_resubmit"
        priority          = "medium"
    elif "not eligible" in reason_lower:
        auto_resolvable   = False
        resolution_action = "verify_eligibility_with_payer"
        priority          = "high"
    elif "not in network" in reason_lower:
        auto_resolvable   = False
        resolution_action = "review_network_status_with_payer_relations"
        priority          = "high"
    elif "maximum" in reason_lower or "benefit" in reason_lower:
        auto_resolvable   = False
        resolution_action = "review_benefit_limits_and_notify_patient"
        priority          = "medium"
    elif "timely filing" in reason_lower:
        auto_resolvable   = False
        resolution_action = "submit_timely_filing_appeal"
        priority          = "critical"
    else:
        auto_resolvable   = False
        resolution_action = "manual_review_required"
        priority          = "high"

    global _classify_cache
    _classify_cache[claim_id] = {"resolution_action": resolution_action, "icd10_valid": icd10_valid}

    return json.dumps({
        "success":               True,
        "claim_id":              claim_id,
        "classification":        "auto_resolvable" if auto_resolvable else "needs_human_review",
        "resolution_action":     resolution_action,
        "recommended_priority":  priority,
        "rejection_reason":      rejection_reason,
    })


# ── Tool 5: Auto-resubmit a claim ─────────────────────────────────────────────

def resubmit_claim(
    claim_id: str,
    corrected_icd10_code: str = "",
    prior_auth_number: str = "",
    resolution_notes: str = "",
) -> str:
    """
    Auto-resolve a billing exception by resubmitting it with corrected data.
    Use this when classify_claim returns classification='auto_resolvable'.

    Args:
        claim_id:              The claim ID to resubmit.
        corrected_icd10_code:  New ICD-10 code if the original was invalid.
        prior_auth_number:     Prior authorization number if that was missing.
        resolution_notes:      Free-text notes describing what was corrected.

    Returns:
        JSON string with resubmission confirmation and updated claim status.
    """
    payload = {
        "resubmitted_by":   "healthcare-ai-billing-agent",
        "resolution_notes": resolution_notes or "Auto-resolved by agent.",
    }
    if corrected_icd10_code:
        payload["corrected_icd10_code"] = corrected_icd10_code
    if prior_auth_number:
        payload["prior_auth_number"] = prior_auth_number

    try:
        resp = requests.post(
            f"{CLAIMBRIDGE_URL}/claims/{claim_id}/resubmit",
            json=payload,
            timeout=5,
        )
        resp.raise_for_status()
        return json.dumps({"success": True, **resp.json()})
    except requests.exceptions.ConnectionError:
        return json.dumps({"success": False, "error": "Cannot reach ClaimBridge API."})
    except requests.HTTPError as e:
        detail = e.response.json().get("detail", str(e)) if e.response else str(e)
        return json.dumps({"success": False, "error": detail})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


# ── Tool 6: Route claim to staff ──────────────────────────────────────────────

def route_to_staff(
    claim_id: str,
    assigned_to: str,
    priority: str,
    routing_reason: str,
) -> str:
    """
    Route a billing claim that requires human review to the appropriate
    staff member with a priority flag.  Use when classify_claim returns
    classification='needs_human_review'.

    Args:
        claim_id:       The claim ID to route.
        assigned_to:    Name or email of the billing specialist to assign.
        priority:       Priority level: low | medium | high | critical.
        routing_reason: Brief explanation of why human review is needed.

    Returns:
        JSON string with routing confirmation and assignment details.
    """
    payload = {
        "assigned_to":    assigned_to,
        "priority":       priority,
        "routing_reason": routing_reason,
        "routed_by":      "healthcare-ai-billing-agent",
    }
    try:
        resp = requests.post(
            f"{CLAIMBRIDGE_URL}/claims/{claim_id}/route",
            json=payload,
            timeout=5,
        )
        resp.raise_for_status()
        return json.dumps({"success": True, **resp.json()})
    except requests.exceptions.ConnectionError:
        return json.dumps({"success": False, "error": "Cannot reach ClaimBridge API."})
    except requests.HTTPError as e:
        detail = e.response.json().get("detail", str(e)) if e.response else str(e)
        return json.dumps({"success": False, "error": detail})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


# ── Tool 7: Record claim outcome (emits live dashboard card) ──────────────────

def record_claim_outcome(
    claim_id: str,
    patient_name: str,
    rejection_reason: str,
    icd10_valid: bool,
    outcome: str,
    priority: str,
    billed_amount: float,
    resolution_action: str,
) -> str:
    """
    Record the final outcome of a processed claim and emit a live dashboard update.
    Call this after every claim is either resubmitted or routed to staff.

    Args:
        claim_id:          The claim ID.
        patient_name:      Patient full name from get_claim_details.
        rejection_reason:  Original rejection reason.
        icd10_valid:       Whether the ICD-10 code passed NLM validation.
        outcome:           'auto_resolved' or 'escalated'.
        priority:          Claim priority level.
        billed_amount:     Dollar amount billed, from get_claim_details.
        resolution_action: What was done to resolve or route the claim.

    Returns:
        JSON string confirming the outcome was recorded.
    """
    global _claim_outcomes
    record = {
        "claim_id":          claim_id,
        "patient_name":      patient_name,
        "rejection_reason":  rejection_reason,
        "icd10_valid":       icd10_valid,
        "outcome":           outcome,
        "priority":          priority,
        "billed_amount":     float(billed_amount),
        "resolution_action": resolution_action,
    }
    _claim_outcomes.append(record)
    print(f"CLAIM_CARD:{json.dumps(record)}", flush=True)
    return json.dumps({"success": True, **record})


# ── Tool 8: Email staff when a claim is routed ────────────────────────────────

_ACTION_LABELS = {
    "validate_and_correct_icd10_code":            "ICD-10 code is invalid — validate and correct before resubmitting",
    "correct_icd10_code_and_resubmit":            "Correct the ICD-10 diagnosis code and resubmit",
    "obtain_and_resubmit_with_prior_auth":        "Obtain prior authorization from payer and resubmit claim",
    "correct_modifier_and_resubmit":              "Correct the billing modifier and resubmit",
    "complete_demographics_and_resubmit":         "Complete missing patient demographics and resubmit",
    "verify_and_close_duplicate":                 "Verify this is a duplicate and close the extra submission",
    "verify_eligibility_with_payer":              "Verify patient eligibility on date of service with the payer",
    "review_network_status_with_payer_relations": "Review provider network status with payer relations team",
    "review_benefit_limits_and_notify_patient":   "Review benefit limits and notify patient of financial responsibility",
    "submit_timely_filing_appeal":                "Submit a timely filing appeal immediately — deadline may be at risk",
    "manual_review_required":                     "Manual review required — rejection reason needs specialist assessment",
}


def notify_staff_claim_routed(
    claim_id: str,
    patient_name: str,
    rejection_reason: str,
    priority: str,
    billed_amount: float,
    assigned_to: str,
    routing_reason: str,
) -> str:
    """
    Send an email alert to billing staff when a claim is routed for human review.
    Call this immediately after route_to_staff for every escalated claim.

    Args:
        claim_id:         The claim ID being escalated.
        patient_name:     Patient full name.
        rejection_reason: The rejection reason from ClaimBridge.
        priority:         Priority level: low | medium | high | critical.
        billed_amount:    Dollar amount at stake.
        assigned_to:      Staff email the claim was routed to.
        routing_reason:   Why human review is needed.

    Returns:
        JSON string confirming the email was sent.
    """
    conn_str  = os.getenv("AZURE_COMMS_CONNECTION_STRING", "")
    sender    = os.getenv("ADMIN_EMAIL_SENDER", "")
    recipient = os.getenv("BILLING_STAFF_EMAIL", os.getenv("ADMIN_EMAIL_RECIPIENTS", ""))

    if not conn_str or not sender or not recipient:
        return json.dumps({"success": False, "error": "Email not configured."})

    readable_action = _ACTION_LABELS.get(
        routing_reason, routing_reason.replace("_", " ").capitalize()
    )

    priority_colors = {
        "critical": "#C62828", "high": "#E65100",
        "medium":   "#F9A825", "low":  "#2E7D32",
    }
    p_color = priority_colors.get(priority.lower(), "#757575")

    html = (
        f'<html><body style="font-family:\'Segoe UI\',Arial,sans-serif;background:#F5F5F5;margin:0;padding:20px">'
        f'<div style="max-width:580px;margin:0 auto;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 2px 10px rgba(0,0,0,.1)">'
        f'<div style="background:{p_color};color:#fff;padding:20px 28px">'
        f'<div style="font-size:11px;text-transform:uppercase;letter-spacing:1px;opacity:.85">Healthcare AI &mdash; Billing Triage</div>'
        f'<div style="font-size:22px;font-weight:700;margin-top:6px">Claim Requires Your Review</div>'
        f'<div style="font-size:13px;margin-top:4px;opacity:.9">Priority: {priority.upper()}</div></div>'
        f'<div style="padding:24px 28px">'
        f'<table style="width:100%;border-collapse:collapse;font-size:13px">'
        f'<tr><td style="padding:8px 0;color:#9E9E9E;width:160px">Claim ID</td><td style="font-weight:700;color:#212121">{claim_id}</td></tr>'
        f'<tr><td style="padding:8px 0;color:#9E9E9E">Patient</td><td style="color:#212121">{patient_name}</td></tr>'
        f'<tr><td style="padding:8px 0;color:#9E9E9E">Rejection Reason</td><td style="color:#212121">{rejection_reason}</td></tr>'
        f'<tr><td style="padding:8px 0;color:#9E9E9E">Billed Amount</td><td style="font-weight:700;color:#212121">${float(billed_amount):,.2f}</td></tr>'
        f'<tr><td style="padding:8px 0;color:#9E9E9E">Assigned To</td><td style="color:#212121">{assigned_to}</td></tr>'
        f'<tr style="background:#FFF8E1"><td style="padding:10px 8px;color:#E65100;font-weight:700" colspan="2">&#9888; Action Required: {readable_action}</td></tr>'
        f'</table></div>'
        f'<div style="padding:14px 28px;background:#F9F9F9;font-size:11px;color:#9E9E9E">Healthcare AI Billing Triage Agent &mdash; automated routing</div>'
        f'</div></body></html>'
    )

    try:
        from azure.communication.email import EmailClient
        client = EmailClient.from_connection_string(conn_str)
        poller = client.begin_send({
            "senderAddress": sender,
            "recipients": {"to": [{"address": recipient}]},
            "content": {
                "subject": f"[{priority.upper()}] Claim {claim_id} Needs Review — ${float(billed_amount):,.2f}",
                "html": html,
            },
        })
        poller.result()
        return json.dumps({"success": True, "message": f"Staff alert sent for {claim_id}."})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


# ── Tool 9: Generate HTML billing triage report ───────────────────────────────

def generate_billing_report() -> str:
    """
    Generate an HTML billing triage report from all claims processed this run.
    Saves locally and uploads to Azure Blob Storage with a 7-day SAS URL.
    Call this after all claims have been processed via record_claim_outcome.

    Returns:
        JSON string with report_url, auto_resolved, escalated, dollars_recovered, dollars_at_risk.
    """
    from datetime import datetime, timezone, timedelta

    claims = list(_claim_outcomes)

    # Pick up any claims that were processed but whose record_claim_outcome was called
    # after generate_billing_report (agent out-of-order execution).
    recorded_ids = {c["claim_id"] for c in claims}
    for cid, saved in _fetched_queue.items():
        if cid in recorded_ids:
            continue
        try:
            resp = requests.get(f"{CLAIMBRIDGE_URL}/claims/{cid}", timeout=5)
            resp.raise_for_status()
            current = resp.json()
        except Exception:
            continue
        if current["claim_status"] == "resubmitted":
            outcome = "auto_resolved"
        elif current.get("assigned_to"):
            outcome = "escalated"
        else:
            continue
        cached = _classify_cache.get(cid, {})
        claims.append({
            "claim_id":          cid,
            "patient_name":      saved.get("patient_name", ""),
            "rejection_reason":  saved.get("rejection_reason", ""),
            "icd10_valid":       cached.get("icd10_valid", True),
            "outcome":           outcome,
            "priority":          saved.get("priority", ""),
            "billed_amount":     float(saved.get("billed_amount", 0)),
            "resolution_action": cached.get("resolution_action", ""),
        })
    total             = len(claims)
    auto_resolved     = [c for c in claims if c.get("outcome") == "auto_resolved"]
    escalated         = [c for c in claims if c.get("outcome") != "auto_resolved"]
    dollars_recovered = sum(c.get("billed_amount", 0) for c in auto_resolved)
    dollars_at_risk   = sum(c.get("billed_amount", 0) for c in escalated)
    resolve_pct       = round(len(auto_resolved) / total * 100) if total else 0
    time_saved        = len(auto_resolved) * 18
    now               = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def _card(c):
        is_res  = c.get("outcome") == "auto_resolved"
        border  = "#2E7D32" if is_res else "#C62828"
        bbg     = "#E8F5E9" if is_res else "#FFEBEE"
        bclr    = "#2E7D32" if is_res else "#C62828"
        btxt    = "AUTO-RESOLVED" if is_res else "ESCALATED"
        icd_clr = "#2E7D32" if c.get("icd10_valid") else "#C62828"
        icd     = "&#10003; Valid" if c.get("icd10_valid") else "&#10007; Invalid"
        pc      = {"critical": "#C62828", "high": "#E65100",
                   "medium": "#F9A825", "low": "#2E7D32"}.get(
                   (c.get("priority") or "").lower(), "#757575")
        act     = _ACTION_LABELS.get(
                   c.get("resolution_action", ""),
                   (c.get("resolution_action") or "").replace("_", " ").capitalize())
        return (
            f'<div style="background:#fff;border-radius:8px;border-left:5px solid {border};'
            f'padding:16px 20px;box-shadow:0 1px 6px rgba(0,0,0,.07);margin-bottom:12px">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">'
            f'<div><span style="font-weight:700;font-size:14px;color:#1565C0">{c.get("claim_id","")}</span>'
            f'<span style="margin-left:12px;font-size:13px;color:#424242">{c.get("patient_name","")}</span></div>'
            f'<span style="background:{bbg};color:{bclr};font-size:11px;font-weight:700;'
            f'padding:3px 10px;border-radius:12px">{btxt}</span></div>'
            f'<table style="width:100%;font-size:12px;border-collapse:collapse">'
            f'<tr><td style="color:#9E9E9E;padding:4px 0;width:150px">Rejection Reason</td>'
            f'<td style="color:#212121">{c.get("rejection_reason","")}</td></tr>'
            f'<tr><td style="color:#9E9E9E;padding:4px 0">ICD-10 Code</td>'
            f'<td style="color:{icd_clr};font-weight:700">{icd}</td></tr>'
            f'<tr><td style="color:#9E9E9E;padding:4px 0">Priority</td>'
            f'<td style="color:{pc};font-weight:700">{(c.get("priority") or "").upper()}</td></tr>'
            f'<tr><td style="color:#9E9E9E;padding:4px 0">Billed Amount</td>'
            f'<td style="font-weight:700;color:#212121">${c.get("billed_amount", 0):,.2f}</td></tr>'
            f'<tr><td style="color:#9E9E9E;padding:4px 0">Resolution</td>'
            f'<td style="color:#424242">{act}</td></tr>'
            f'</table></div>'
        )

    cards_html = "\n".join(_card(c) for c in claims)

    report_html = (
        f'<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/>'
        f'<title>Billing Triage Report</title>'
        f'<style>body{{font-family:"Segoe UI",Arial,sans-serif;background:#F0F4F8;margin:0;padding:24px}}'
        f'.wrap{{max-width:820px;margin:0 auto}}</style></head>'
        f'<body><div class="wrap">'
        f'<div style="background:#1565C0;color:#fff;border-radius:10px;padding:28px 32px;margin-bottom:20px">'
        f'<div style="font-size:11px;text-transform:uppercase;letter-spacing:1.5px;opacity:.75;margin-bottom:8px">Healthcare AI &mdash; Automation 3</div>'
        f'<div style="font-size:26px;font-weight:700;margin-bottom:4px">Billing Triage Report</div>'
        f'<div style="font-size:13px;opacity:.8">Generated {now}</div></div>'
        f'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:20px">'
        f'<div style="background:#fff;border-radius:8px;padding:16px 18px;text-align:center;box-shadow:0 1px 6px rgba(0,0,0,.07)">'
        f'<div style="font-size:28px;font-weight:700;color:#1565C0">{total}</div>'
        f'<div style="font-size:11px;color:#9E9E9E;text-transform:uppercase;margin-top:4px">Claims Processed</div></div>'
        f'<div style="background:#fff;border-radius:8px;padding:16px 18px;text-align:center;box-shadow:0 1px 6px rgba(0,0,0,.07)">'
        f'<div style="font-size:28px;font-weight:700;color:#2E7D32">{len(auto_resolved)}</div>'
        f'<div style="font-size:11px;color:#9E9E9E;text-transform:uppercase;margin-top:4px">Auto-Resolved ({resolve_pct}%)</div></div>'
        f'<div style="background:#fff;border-radius:8px;padding:16px 18px;text-align:center;box-shadow:0 1px 6px rgba(0,0,0,.07)">'
        f'<div style="font-size:28px;font-weight:700;color:#C62828">{len(escalated)}</div>'
        f'<div style="font-size:11px;color:#9E9E9E;text-transform:uppercase;margin-top:4px">Escalated</div></div>'
        f'<div style="background:#fff;border-radius:8px;padding:16px 18px;text-align:center;box-shadow:0 1px 6px rgba(0,0,0,.07)">'
        f'<div style="font-size:28px;font-weight:700;color:#1565C0">{time_saved}m</div>'
        f'<div style="font-size:11px;color:#9E9E9E;text-transform:uppercase;margin-top:4px">Est. Time Saved</div></div></div>'
        f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:22px">'
        f'<div style="background:#E8F5E9;border-radius:8px;padding:18px 22px">'
        f'<div style="font-size:11px;color:#2E7D32;text-transform:uppercase;font-weight:700;margin-bottom:6px">Dollars Auto-Recovered</div>'
        f'<div style="font-size:28px;font-weight:700;color:#2E7D32">${dollars_recovered:,.2f}</div></div>'
        f'<div style="background:#FFEBEE;border-radius:8px;padding:18px 22px">'
        f'<div style="font-size:11px;color:#C62828;text-transform:uppercase;font-weight:700;margin-bottom:6px">Dollars Still at Risk</div>'
        f'<div style="font-size:28px;font-weight:700;color:#C62828">${dollars_at_risk:,.2f}</div></div></div>'
        f'<div style="font-size:13px;font-weight:700;color:#9E9E9E;text-transform:uppercase;letter-spacing:.8px;margin-bottom:14px">Claim Details</div>'
        f'{cards_html}'
        f'<div style="text-align:center;padding:20px;font-size:11px;color:#BDBDBD;margin-top:8px">'
        f'Healthcare AI Billing Triage Agent &mdash; {now}</div>'
        f'</div></body></html>'
    )

    reports_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data", "reports")
    os.makedirs(reports_dir, exist_ok=True)
    ts         = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    local_path = os.path.join(reports_dir, f"billing_triage_{ts}.html")
    with open(local_path, "w", encoding="utf-8") as f:
        f.write(report_html)
    print(f"[billing] Report saved: billing_triage_{ts}.html")

    base_result = {
        "success": True, "local_path": local_path,
        "total": total,
        "auto_resolved": len(auto_resolved),
        "escalated": len(escalated),
        "dollars_recovered": round(dollars_recovered, 2),
        "dollars_at_risk":   round(dollars_at_risk, 2),
    }

    conn_str  = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
    container = os.getenv("AZURE_STORAGE_CONTAINER_REPORTS", "reports")
    blob_name = f"billing_triage_{ts}.html"
    if not conn_str:
        return json.dumps({**base_result, "report_url": ""})

    try:
        from azure.storage.blob import (
            BlobServiceClient, ContentSettings,
            generate_blob_sas, BlobSasPermissions,
        )
        svc = BlobServiceClient.from_connection_string(conn_str)
        try:
            svc.create_container(container)
        except Exception:
            pass
        bc = svc.get_blob_client(container=container, blob=blob_name)
        with open(local_path, "rb") as f:
            bc.upload_blob(f, overwrite=True,
                           content_settings=ContentSettings(content_type="text/html"))
        parts        = dict(p.split("=", 1) for p in conn_str.split(";") if "=" in p)
        account_name = parts.get("AccountName", "")
        account_key  = parts.get("AccountKey", "")
        sas_token    = generate_blob_sas(
            account_name=account_name, container_name=container, blob_name=blob_name,
            account_key=account_key, permission=BlobSasPermissions(read=True),
            expiry=datetime.now(timezone.utc) + timedelta(days=7),
        )
        url = f"https://{account_name}.blob.core.windows.net/{container}/{blob_name}?{sas_token}"
        print("[billing] Report uploaded to Azure Blob.")
        return json.dumps({**base_result, "report_url": url})
    except Exception as e:
        return json.dumps({**base_result, "report_url": "", "upload_error": str(e)})


# ── Tool 10: Email billing manager summary ────────────────────────────────────

def send_billing_summary_email(
    total_processed: int,
    auto_resolved: int,
    escalated: int,
    dollars_recovered: float,
    dollars_at_risk: float,
    report_url: str = "",
) -> str:
    """
    Send a billing triage summary email to the billing manager after all claims are processed.
    Pass the report_url returned by generate_billing_report so the manager can open it.

    Args:
        total_processed:   Total claims processed this run.
        auto_resolved:     Number auto-resolved by the agent.
        escalated:         Number routed to human review.
        dollars_recovered: Sum of billed_amount for auto-resolved claims.
        dollars_at_risk:   Sum of billed_amount for escalated claims.
        report_url:        SAS URL from generate_billing_report (pass empty string if unavailable).

    Returns:
        JSON string confirming the email was sent.
    """
    from datetime import datetime, timezone

    conn_str  = os.getenv("AZURE_COMMS_CONNECTION_STRING", "")
    sender    = os.getenv("ADMIN_EMAIL_SENDER", "")
    recipient = os.getenv("MANAGER_NOTIFICATION_EMAIL", os.getenv("ADMIN_EMAIL_RECIPIENTS", ""))

    if not conn_str or not sender or not recipient:
        return json.dumps({"success": False, "error": "Email not configured."})

    now         = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    resolve_pct = round(auto_resolved / total_processed * 100) if total_processed else 0
    time_saved  = auto_resolved * 18
    report_link = (
        f'<a href="{report_url}" style="display:inline-block;background:#1565C0;color:#fff;'
        f'padding:10px 22px;border-radius:6px;text-decoration:none;font-weight:700;margin-top:16px">'
        f'View Full Billing Report &rarr;</a>'
    ) if report_url else ""

    html = (
        f'<html><body style="font-family:\'Segoe UI\',Arial,sans-serif;background:#F5F5F5;margin:0;padding:20px">'
        f'<div style="max-width:580px;margin:0 auto;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 2px 10px rgba(0,0,0,.1)">'
        f'<div style="background:#1565C0;color:#fff;padding:24px 28px">'
        f'<div style="font-size:11px;text-transform:uppercase;letter-spacing:1.5px;opacity:.75">Healthcare AI &mdash; Automation 3</div>'
        f'<div style="font-size:22px;font-weight:700;margin-top:6px">Billing Triage Complete</div>'
        f'<div style="font-size:13px;margin-top:4px;opacity:.85">{now}</div></div>'
        f'<div style="padding:24px 28px">'
        f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px">'
        f'<div style="background:#F0F4F8;border-radius:8px;padding:14px 16px;text-align:center">'
        f'<div style="font-size:26px;font-weight:700;color:#1565C0">{total_processed}</div>'
        f'<div style="font-size:11px;color:#9E9E9E;text-transform:uppercase;margin-top:3px">Claims Processed</div></div>'
        f'<div style="background:#F0F4F8;border-radius:8px;padding:14px 16px;text-align:center">'
        f'<div style="font-size:26px;font-weight:700;color:#2E7D32">{auto_resolved} ({resolve_pct}%)</div>'
        f'<div style="font-size:11px;color:#9E9E9E;text-transform:uppercase;margin-top:3px">Auto-Resolved</div></div></div>'
        f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:18px">'
        f'<div style="background:#E8F5E9;border-radius:8px;padding:14px 16px">'
        f'<div style="font-size:11px;color:#2E7D32;font-weight:700;text-transform:uppercase;margin-bottom:4px">Auto-Recovered</div>'
        f'<div style="font-size:22px;font-weight:700;color:#2E7D32">${float(dollars_recovered):,.2f}</div></div>'
        f'<div style="background:#FFEBEE;border-radius:8px;padding:14px 16px">'
        f'<div style="font-size:11px;color:#C62828;font-weight:700;text-transform:uppercase;margin-bottom:4px">Still at Risk</div>'
        f'<div style="font-size:22px;font-weight:700;color:#C62828">${float(dollars_at_risk):,.2f}</div></div></div>'
        f'<div style="font-size:13px;color:#424242;background:#F9F9F9;padding:12px 16px;border-radius:6px;margin-bottom:4px">'
        f'Estimated time saved: <strong>{time_saved} minutes</strong> &mdash; '
        f'{escalated} claim(s) routed to billing staff for human review.</div>'
        f'{report_link}</div>'
        f'<div style="padding:14px 28px;background:#F9F9F9;font-size:11px;color:#9E9E9E">Healthcare AI Billing Triage Agent &mdash; automated run</div>'
        f'</div></body></html>'
    )

    try:
        from azure.communication.email import EmailClient
        client = EmailClient.from_connection_string(conn_str)
        poller = client.begin_send({
            "senderAddress": sender,
            "recipients": {"to": [{"address": recipient}]},
            "content": {
                "subject": (
                    f"Billing Triage Complete — {auto_resolved}/{total_processed} "
                    f"Auto-Resolved | ${float(dollars_recovered):,.2f} Recovered"
                ),
                "html": html,
            },
        })
        poller.result()
        return json.dumps({"success": True, "message": "Manager summary email sent."})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})
