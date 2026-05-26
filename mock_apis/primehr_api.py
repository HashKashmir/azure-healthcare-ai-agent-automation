"""
PrimeHR HR API — mock FastAPI stub (Automation 1: New Employee Onboarding)

Runs on port 8001.  Start with:
    uvicorn mock_apis.primehr_api:app --port 8001 --reload

Endpoints:
    GET  /                                      — API info
    GET  /health                                — health check
    GET  /employees                             — list employees (filterable)
    GET  /employees/{employee_id}               — employee detail
    GET  /employees/{employee_id}/onboarding    — onboarding checklist + status
    POST /employees/{employee_id}/onboarding/complete  — mark a doc step done
"""

import json
import os
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ── Bootstrap ─────────────────────────────────────────────────────────────────

DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "employees.json")

def _load_employees() -> list[dict]:
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)

# In-memory store (rebuilt on each server start — fine for a mock)
_employees: list[dict] = _load_employees()
_by_id: dict[str, dict] = {e["employee_id"]: e for e in _employees}

app = FastAPI(
    title="PrimeHR HR API (Mock)",
    description="Mock REST API mirroring PrimeHR HR schemas for Healthcare AI agent integration.",
    version="1.0.0",
)

# ── Models ────────────────────────────────────────────────────────────────────

class CompleteStepRequest(BaseModel):
    document_name: str
    completed_by: Optional[str] = None
    notes: Optional[str] = None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "api": "PrimeHR HR API (Mock)",
        "version": "1.0.0",
        "description": "Serves synthetic employee records for Healthcare AI agent integration.",
        "docs": "/docs",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "employee_count": len(_employees),
    }


@app.get("/employees")
def list_employees(
    department: Optional[str] = Query(None, description="Filter by department name"),
    status: Optional[str] = Query(None, description="Filter by status (Active | Inactive)"),
    onboarding_status: Optional[str] = Query(None, description="Filter by onboarding status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """
    Return a paginated list of employees.  Supports optional filters on
    department, employment status, and onboarding status.
    """
    results = list(_employees)

    if department:
        results = [e for e in results if e["department"].lower() == department.lower()]
    if status:
        results = [e for e in results if e["status"].lower() == status.lower()]
    if onboarding_status:
        results = [e for e in results
                   if e["onboarding_status"].lower() == onboarding_status.lower()]

    total = len(results)
    page  = results[offset : offset + limit]

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "employees": [_summary(e) for e in page],
    }


@app.get("/employees/{employee_id}")
def get_employee(employee_id: str):
    """Return full details for a single employee."""
    emp = _by_id.get(employee_id.upper())
    if not emp:
        raise HTTPException(status_code=404, detail=f"Employee '{employee_id}' not found.")
    return emp


@app.get("/employees/{employee_id}/onboarding")
def get_onboarding(employee_id: str):
    """
    Return the onboarding checklist for an employee — which documents are
    completed vs. still pending — plus manager contact for routing.
    """
    emp = _by_id.get(employee_id.upper())
    if not emp:
        raise HTTPException(status_code=404, detail=f"Employee '{employee_id}' not found.")

    all_docs = [
        "W-4 Federal Withholding",
        "AZ State Tax Form",
        "I-9 Employment Eligibility",
        "Direct Deposit Authorization",
        "Benefits Enrollment Form",
        "HIPAA Confidentiality Agreement",
        "Employee Handbook Acknowledgment",
        "Background Check Consent",
        "Drug Screen Authorization",
        "Emergency Contact Form",
        "IT Access Request",
    ]
    pending  = emp.get("documents_pending", [])
    completed = [d for d in all_docs if d not in pending]

    return {
        "employee_id":       emp["employee_id"],
        "employee_name":     f"{emp['first_name']} {emp['last_name']}",
        "department":        emp["department"],
        "position":          emp["position"],
        "hire_date":         emp["hire_date"],
        "start_date":        emp["start_date"],
        "onboarding_status": emp["onboarding_status"],
        "manager_id":        emp["manager_id"],
        "manager_name":      emp["manager_name"],
        "manager_email":     emp["manager_email"],
        "checklist": {
            "total_documents":     len(all_docs),
            "completed_count":     len(completed),
            "pending_count":       len(pending),
            "completed_documents": completed,
            "pending_documents":   pending,
        },
        "completion_percentage": round(len(completed) / len(all_docs) * 100, 1),
    }


@app.post("/employees/{employee_id}/onboarding/complete")
def complete_onboarding_step(employee_id: str, body: CompleteStepRequest):
    """
    Mark a specific onboarding document as completed for an employee.
    Updates in-memory state (simulating a real HR system write).
    """
    emp = _by_id.get(employee_id.upper())
    if not emp:
        raise HTTPException(status_code=404, detail=f"Employee '{employee_id}' not found.")

    doc = body.document_name
    pending = emp.get("documents_pending", [])

    if doc not in pending:
        # Either already completed or document name doesn't exist
        all_docs = [
            "W-4 Federal Withholding", "AZ State Tax Form", "I-9 Employment Eligibility",
            "Direct Deposit Authorization", "Benefits Enrollment Form",
            "HIPAA Confidentiality Agreement", "Employee Handbook Acknowledgment",
            "Background Check Consent", "Drug Screen Authorization",
            "Emergency Contact Form", "IT Access Request",
        ]
        if doc not in all_docs:
            raise HTTPException(status_code=400, detail=f"Unknown document: '{doc}'")
        return {"message": f"'{doc}' was already completed.", "employee_id": employee_id}

    pending.remove(doc)
    emp["documents_pending"] = pending

    if not pending:
        emp["onboarding_status"] = "Completed"
    elif len(pending) < 11:
        emp["onboarding_status"] = "In Progress"

    return {
        "message":           f"'{doc}' marked as completed.",
        "employee_id":       employee_id,
        "completed_by":      body.completed_by,
        "timestamp":         datetime.utcnow().isoformat() + "Z",
        "onboarding_status": emp["onboarding_status"],
        "remaining_pending": len(pending),
        "notes":             body.notes,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _summary(emp: dict) -> dict:
    """Lightweight employee summary for list responses."""
    return {
        "employee_id":       emp["employee_id"],
        "name":              f"{emp['first_name']} {emp['last_name']}",
        "email":             emp["email"],
        "department":        emp["department"],
        "position":          emp["position"],
        "employment_type":   emp["employment_type"],
        "hire_date":         emp["hire_date"],
        "status":            emp["status"],
        "onboarding_status": emp["onboarding_status"],
        "manager_name":      emp["manager_name"],
    }

