"""
ClaimBridge Billing API — mock FastAPI stub (Automation 3: Billing Workflow Triage)

Runs on port 8002.  Start with:
    uvicorn mock_apis.claimbridge_api:app --port 8002 --reload

Endpoints:
    GET   /                                — API info
    GET   /health                          — health check
    GET   /claims                          — list claims (filterable + paginated)
    GET   /claims/exceptions               — exception / denial queue
    GET   /claims/stats                    — billing stats summary
    GET   /claims/{claim_id}               — claim detail
    POST  /claims/{claim_id}/resubmit      — resubmit a claim
    POST  /claims/{claim_id}/route         — route claim to staff
    PATCH /claims/{claim_id}/status        — update claim status
"""

import csv
import os
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

# ── Bootstrap ─────────────────────────────────────────────────────────────────

DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "claims.csv")

def _load_claims() -> list[dict]:
    with open(DATA_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            # Cast numeric fields back from string
            row["billed_amount"]  = float(row["billed_amount"])
            row["allowed_amount"] = float(row["allowed_amount"])
            row["days_in_queue"]  = int(row["days_in_queue"])
            rows.append(row)
        return rows

_claims: list[dict] = _load_claims()
_by_id: dict[str, dict] = {c["claim_id"]: c for c in _claims}

app = FastAPI(
    title="ClaimBridge Billing API (Mock)",
    description="Mock REST API mirroring ClaimBridge billing schemas for Healthcare AI agent triage.",
    version="1.0.0",
)

# ── Models ────────────────────────────────────────────────────────────────────

class ResubmitRequest(BaseModel):
    corrected_icd10_code: Optional[str] = None
    corrected_procedure_code: Optional[str] = None
    prior_auth_number: Optional[str] = None
    resolution_notes: Optional[str] = None
    resubmitted_by: Optional[str] = "agent"


class RouteRequest(BaseModel):
    assigned_to: str
    priority: Optional[str] = "medium"
    routing_reason: Optional[str] = None
    routed_by: Optional[str] = "agent"


class StatusUpdateRequest(BaseModel):
    claim_status: str
    resolution_notes: Optional[str] = None
    updated_by: Optional[str] = "agent"


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "api": "ClaimBridge Billing API (Mock)",
        "version": "1.0.0",
        "description": "Serves synthetic billing claims for Healthcare AI agent triage workflow.",
        "docs": "/docs",
    }


@app.get("/health")
def health():
    status_counts: dict[str, int] = {}
    for c in _claims:
        s = c["claim_status"]
        status_counts[s] = status_counts.get(s, 0) + 1

    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "total_claims": len(_claims),
        "status_breakdown": status_counts,
    }


@app.get("/claims")
def list_claims(
    status: Optional[str] = Query(None, description="Filter by claim status"),
    department: Optional[str] = Query(None, description="Filter by department"),
    payer: Optional[str] = Query(None, description="Filter by payer name"),
    priority: Optional[str] = Query(None, description="Filter by priority (low|medium|high|critical)"),
    limit: int = Query(25, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """
    Return a paginated list of billing claims with optional filters.
    """
    results = list(_claims)

    if status:
        results = [c for c in results if c["claim_status"].lower() == status.lower()]
    if department:
        results = [c for c in results if c["department"].lower() == department.lower()]
    if payer:
        results = [c for c in results if payer.lower() in c["payer"].lower()]
    if priority:
        results = [c for c in results if c["priority"].lower() == priority.lower()]

    # Sort: exceptions and denials first, then by days_in_queue descending
    results.sort(
        key=lambda c: (
            0 if c["claim_status"] in ("exception", "denied") else 1,
            -c["days_in_queue"],
        )
    )

    total = len(results)
    page  = results[offset : offset + limit]

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "claims": [_summary(c) for c in page],
    }


@app.get("/claims/exceptions")
def get_exception_queue(
    priority: Optional[str] = Query(None),
    department: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """
    Return the active billing exception / denial queue — the set of claims
    that require agent triage or human review.  Sorted by priority then age.
    """
    results = [
        c for c in _claims
        if c["claim_status"] in ("exception", "denied") and not c["assigned_to"]
    ]

    if priority:
        results = [c for c in results if c["priority"].lower() == priority.lower()]
    if department:
        results = [c for c in results if c["department"].lower() == department.lower()]

    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "": 4}
    results.sort(key=lambda c: (priority_order.get(c["priority"], 4), -c["days_in_queue"]))

    return {
        "queue_depth": len(results),
        "returned":    min(limit, len(results)),
        "exceptions":  [_summary(c) for c in results[:limit]],
    }


@app.get("/claims/stats")
def get_stats():
    """
    Aggregate billing statistics — used by the reporting pipeline and
    the agent's weekly insight module.
    """
    total_billed   = sum(c["billed_amount"] for c in _claims)
    total_allowed  = sum(c["allowed_amount"] for c in _claims)

    status_counts: dict[str, int] = {}
    dept_totals: dict[str, float] = {}
    payer_counts: dict[str, int]  = {}
    rejection_counts: dict[str, int] = {}

    for c in _claims:
        s = c["claim_status"]
        status_counts[s] = status_counts.get(s, 0) + 1

        dept = c["department"]
        dept_totals[dept] = dept_totals.get(dept, 0.0) + c["billed_amount"]

        payer = c["payer"]
        payer_counts[payer] = payer_counts.get(payer, 0) + 1

        reason = c["rejection_reason"]
        if reason:
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

    approved  = status_counts.get("approved", 0)
    denied    = status_counts.get("denied", 0)
    exception = status_counts.get("exception", 0)

    return {
        "summary": {
            "total_claims":        len(_claims),
            "total_billed":        round(total_billed, 2),
            "total_allowed":       round(total_allowed, 2),
            "collection_rate_pct": round(total_allowed / total_billed * 100, 1) if total_billed else 0,
            "approval_rate_pct":   round(approved / len(_claims) * 100, 1) if _claims else 0,
            "denial_rate_pct":     round(denied / len(_claims) * 100, 1) if _claims else 0,
            "exception_queue_depth": exception,
        },
        "by_status":     status_counts,
        "by_department": {k: round(v, 2) for k, v in sorted(dept_totals.items(), key=lambda x: -x[1])},
        "by_payer":      payer_counts,
        "rejection_reasons": dict(sorted(rejection_counts.items(), key=lambda x: -x[1])),
    }


@app.get("/claims/{claim_id}")
def get_claim(claim_id: str):
    """Return full details for a single billing claim."""
    claim = _by_id.get(claim_id.upper())
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim '{claim_id}' not found.")
    return claim


@app.post("/claims/{claim_id}/resubmit")
def resubmit_claim(claim_id: str, body: ResubmitRequest):
    """
    Auto-resolve a claim by resubmitting it with corrected fields.
    Used by the agent when the rejection reason is auto-resolvable
    (e.g., missing prior-auth, invalid code that the agent has corrected).
    """
    claim = _by_id.get(claim_id.upper())
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim '{claim_id}' not found.")

    if claim["claim_status"] not in ("exception", "denied"):
        raise HTTPException(
            status_code=400,
            detail=f"Claim '{claim_id}' has status '{claim['claim_status']}' — only exception/denied claims can be resubmitted.",
        )

    previous_status = claim["claim_status"]

    if body.corrected_icd10_code:
        claim["icd10_code"] = body.corrected_icd10_code
    if body.corrected_procedure_code:
        claim["procedure_code"] = body.corrected_procedure_code
    if body.prior_auth_number:
        claim["resolution_notes"] = f"Prior auth: {body.prior_auth_number}"
    if body.resolution_notes:
        claim["resolution_notes"] = body.resolution_notes

    claim["claim_status"]     = "resubmitted"
    claim["rejection_reason"] = ""
    claim["priority"]         = ""

    return {
        "message":          f"Claim '{claim_id}' resubmitted successfully.",
        "claim_id":         claim_id,
        "previous_status":  previous_status,
        "new_status":       "resubmitted",
        "resubmitted_by":   body.resubmitted_by,
        "timestamp":        datetime.utcnow().isoformat() + "Z",
        "resolution_notes": claim["resolution_notes"],
    }


@app.post("/claims/{claim_id}/route")
def route_claim(claim_id: str, body: RouteRequest):
    """
    Route a claim that requires human review to a specific staff member.
    The agent calls this when the rejection reason is complex or ambiguous.
    """
    claim = _by_id.get(claim_id.upper())
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim '{claim_id}' not found.")

    if claim["claim_status"] not in ("exception", "denied", "pending"):
        raise HTTPException(
            status_code=400,
            detail=f"Claim '{claim_id}' cannot be routed — current status: '{claim['claim_status']}'.",
        )

    claim["assigned_to"]      = body.assigned_to
    claim["priority"]         = body.priority or claim["priority"] or "medium"
    claim["resolution_notes"] = body.routing_reason or ""

    return {
        "message":       f"Claim '{claim_id}' routed to '{body.assigned_to}'.",
        "claim_id":      claim_id,
        "assigned_to":   body.assigned_to,
        "priority":      claim["priority"],
        "routed_by":     body.routed_by,
        "routing_reason": body.routing_reason,
        "timestamp":     datetime.utcnow().isoformat() + "Z",
    }


@app.patch("/claims/{claim_id}/status")
def update_status(claim_id: str, body: StatusUpdateRequest):
    """Update the status of a claim (general-purpose status write)."""
    claim = _by_id.get(claim_id.upper())
    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim '{claim_id}' not found.")

    valid_statuses = {"pending", "approved", "denied", "exception", "resubmitted", "closed"}
    if body.claim_status.lower() not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{body.claim_status}'. Must be one of: {sorted(valid_statuses)}",
        )

    previous = claim["claim_status"]
    claim["claim_status"] = body.claim_status.lower()
    if body.resolution_notes:
        claim["resolution_notes"] = body.resolution_notes

    return {
        "message":          f"Claim '{claim_id}' status updated.",
        "claim_id":         claim_id,
        "previous_status":  previous,
        "new_status":       claim["claim_status"],
        "updated_by":       body.updated_by,
        "timestamp":        datetime.utcnow().isoformat() + "Z",
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _summary(claim: dict) -> dict:
    return {
        "claim_id":         claim["claim_id"],
        "patient_name":     claim["patient_name"],
        "patient_id":       claim["patient_id"],
        "department":       claim["department"],
        "icd10_code":       claim["icd10_code"],
        "procedure_code":   claim["procedure_code"],
        "billed_amount":    claim["billed_amount"],
        "payer":            claim["payer"],
        "claim_status":     claim["claim_status"],
        "rejection_reason": claim["rejection_reason"],
        "priority":         claim["priority"],
        "days_in_queue":    claim["days_in_queue"],
        "assigned_to":      claim["assigned_to"],
    }

