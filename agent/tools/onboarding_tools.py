"""
Automation 1 - New Employee Onboarding tools.

Each function is a callable tool registered with the Azure AI Foundry agent.
The agent calls these in sequence to complete the onboarding workflow.
"""

import json
import os
import uuid
from datetime import date, datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

PRIMEHR_URL = os.getenv("PRIMEHR_API_URL", "http://localhost:8001")

# ── Role-based form definitions ───────────────────────────────────────────────

_BASE_FORMS = [
    "I-9 Employment Eligibility",
    "HIPAA Confidentiality Agreement",
    "Employee Handbook Acknowledgment",
    "Background Check Consent",
    "Emergency Contact Form",
    "IT Access Request",
]
_EMPLOYEE_FORMS = [
    "W-4 Federal Withholding",
    "AZ State Tax Form",
    "Direct Deposit Authorization",
    "Benefits Enrollment Form",
    "Drug Screen Authorization",
]
_CONTRACTOR_FORMS = [
    "W-9 Independent Contractor",
    "Drug Screen Authorization",
]
_CLINICAL_FORMS = [
    "BLS/ACLS Certification Verification",
    "Professional License Verification",
]
_PHYSICIAN_FORMS = [
    "DEA Registration Verification",
    "Malpractice Insurance Verification",
    "Medical Staff Credentialing Application",
]

_CLINICAL_DEPARTMENTS = {
    "nursing", "emergency", "icu", "surgery", "clinical",
    "radiology", "pharmacy", "oncology", "cardiology", "pediatrics",
    "neurology", "orthopedic", "mental health", "physical therapy",
    "primary care", "psychiatry",
}
_PHYSICIAN_TITLES = {
    "physician", "doctor", "md", "do", "hospitalist", "surgeon", "specialist",
    "cardiologist", "neurologist", "oncologist", "psychiatrist", "radiologist",
    "pediatrician", "internist",
}


def _select_forms(employment_type: str, position: str, department: str) -> tuple:
    """Return (forms_list, role_label) based on employee attributes."""
    forms = list(_BASE_FORMS)
    emp_lower  = employment_type.lower()
    pos_lower  = position.lower()
    dept_lower = department.lower()

    if any(t in emp_lower for t in ("contract", "prn", "per diem", "temporary")):
        forms += _CONTRACTOR_FORMS
        role_label = "Contractor / PRN"
    else:
        forms += _EMPLOYEE_FORMS
        role_label = "Part-Time Employee" if "part" in emp_lower else "Full-Time Employee"

    if any(kw in dept_lower for kw in _CLINICAL_DEPARTMENTS):
        forms += _CLINICAL_FORMS
        role_label = f"Clinical - {role_label}"

    if any(kw in pos_lower for kw in _PHYSICIAN_TITLES):
        forms += _PHYSICIAN_FORMS
        role_label = f"Physician / Provider - {role_label}"

    seen, unique = set(), []
    for f in forms:
        if f not in seen:
            seen.add(f)
            unique.append(f)
    return unique, role_label


# ── PDF generator ─────────────────────────────────────────────────────────────

def _safe(value) -> str:
    """Return a Latin-1-safe string for fpdf2 Helvetica rendering."""
    return str(value or "").encode("latin-1", errors="replace").decode("latin-1")


def _generate_onboarding_pdf(record: dict) -> bytes:
    """Build a professional onboarding package PDF from form data."""
    from fpdf import FPDF, XPos, YPos

    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Header bar
    pdf.set_fill_color(21, 101, 192)
    pdf.rect(0, 0, 210, 30, "F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_xy(10, 6)
    pdf.cell(0, 9, "Healthcare AI", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_x(10)
    pdf.cell(0, 6, "Employee Onboarding Package", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_text_color(33, 33, 33)
    pdf.set_y(36)

    # Employee information
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(227, 242, 253)
    pdf.cell(0, 8, "  Employee Information", fill=True,
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)

    info_rows = [
        ("Employee ID",      _safe(record.get("employee_id"))),
        ("Name",             _safe(record.get("employee_name"))),
        ("Department",       _safe(record.get("department"))),
        ("Position",         _safe(record.get("position"))),
        ("Role Category",    _safe(record.get("role_category"))),
        ("Employment Type",  _safe(record.get("employment_type"))),
        ("Start Date",       _safe(record.get("start_date"))),
        ("Hire Date",        _safe(record.get("hire_date"))),
        ("Pay Frequency",    _safe(record.get("pay_frequency"))),
        ("Manager",          _safe(record.get("manager_name"))),
        ("Manager Email",    _safe(record.get("manager_email"))),
    ]
    for label, value in info_rows:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(55, 7, f"  {label}:", new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 7, value, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(5)

    # Required forms
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(227, 242, 253)
    pdf.cell(0, 8, "  Required Forms", fill=True,
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 10)
    for i, form in enumerate(record.get("forms_included", []), 1):
        pdf.cell(12, 7, f"  {i}.", new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.cell(0, 7, _safe(form), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(5)

    # Pending documents
    pending = record.get("pending_documents", [])
    if pending:
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_fill_color(255, 243, 224)
        pdf.set_text_color(230, 81, 0)
        pdf.cell(0, 8, "  Pending Documents - Action Required", fill=True,
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(33, 33, 33)
        pdf.ln(2)
        pdf.set_font("Helvetica", "", 10)
        for doc in pending:
            pdf.cell(12, 7, "  -", new_x=XPos.RIGHT, new_y=YPos.TOP)
            pdf.cell(0, 7, _safe(doc), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(5)

    # Footer
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(160, 160, 160)
    pdf.cell(0, 6,
             f"Generated by Healthcare AI HR Agent - {_safe(record.get('generated_at'))}",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 6, f"Document ID: {_safe(record.get('document_id'))}",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    return bytes(pdf.output())


# ── Tool 1: Fetch employee record from PrimeHR ────────────────────────────────

def get_employee_details(employee_id: str) -> str:
    """
    Fetch the full HR record for an employee from the PrimeHR system.

    Args:
        employee_id: The employee ID in EMP-XXXX format (e.g. EMP-0023).

    Returns:
        JSON string with employee details including name, department,
        position, start date, manager info, and onboarding status.
    """
    try:
        resp = requests.get(f"{PRIMEHR_URL}/employees/{employee_id}", timeout=5)
        resp.raise_for_status()
        return json.dumps({"success": True, "employee": resp.json()})
    except requests.exceptions.ConnectionError:
        return json.dumps({
            "success": False,
            "error": f"Cannot reach PrimeHR API at {PRIMEHR_URL}. Ensure it is running.",
        })
    except requests.HTTPError as e:
        return json.dumps({"success": False, "error": str(e)})


# ── Tool 2: Generate role-based onboarding form package ───────────────────────

def fill_onboarding_form(employee_id: str) -> str:
    """
    Generate a role-appropriate onboarding document package for a new hire.
    Selects forms based on employment type, position, and department.

    Args:
        employee_id: The employee ID in EMP-XXXX format.

    Returns:
        JSON string with the pre-filled form data, role category, selected
        forms list, pending documents, and a document reference ID.
    """
    try:
        onboard_resp = requests.get(
            f"{PRIMEHR_URL}/employees/{employee_id}/onboarding", timeout=5
        )
        onboard_resp.raise_for_status()
        onboard = onboard_resp.json()

        emp_resp = requests.get(f"{PRIMEHR_URL}/employees/{employee_id}", timeout=5)
        emp_resp.raise_for_status()
        emp = emp_resp.json()
    except requests.exceptions.ConnectionError:
        return json.dumps({"success": False, "error": "Cannot reach PrimeHR API."})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})

    forms, role_label = _select_forms(
        emp.get("employment_type", ""),
        emp.get("position", ""),
        emp.get("department", ""),
    )

    doc_id = f"FORM-{uuid.uuid4().hex[:8].upper()}"
    form_package = {
        "document_id":      doc_id,
        "employee_id":      employee_id,
        "employee_name":    f"{emp['first_name']} {emp['last_name']}",
        "department":       emp["department"],
        "position":         emp["position"],
        "role_category":    role_label,
        "employment_type":  emp["employment_type"],
        "hire_date":        emp["hire_date"],
        "start_date":       emp["start_date"],
        "pay_frequency":    emp["pay_frequency"],
        "manager_name":     emp["manager_name"],
        "manager_email":    emp["manager_email"],
        "forms_included":   forms,
        "pending_documents": onboard["checklist"]["pending_documents"],
        "generated_at":     datetime.now(timezone.utc).isoformat(),
        "status":           "ready_for_upload",
    }
    return json.dumps({"success": True, "form_package": form_package})


# ── Tool 3: Assess onboarding risk ───────────────────────────────────────────

def assess_onboarding_risk(pending_documents_json: str, start_date: str) -> str:
    """
    Assess the compliance risk of an onboarding based on pending documents
    and days remaining until the employee's start date.

    Args:
        pending_documents_json: JSON array string of outstanding document names.
        start_date:             Employee start date in YYYY-MM-DD format.

    Returns:
        JSON string with risk_level (critical/warning/low/clear),
        notification_type (critical/conditional/standard),
        days_until_start, missing_critical_documents, and an alert_message.
    """
    try:
        pending = json.loads(pending_documents_json)
        if not isinstance(pending, list):
            pending = []
    except Exception:
        pending = []

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        days_until_start = (start - date.today()).days
    except Exception:
        days_until_start = 999

    critical_docs = ["Background Check", "I-9", "Drug Screen"]
    missing_critical = [
        doc for doc in critical_docs
        if any(doc.lower() in p.lower() for p in pending)
    ]

    if missing_critical and 0 <= days_until_start <= 3:
        risk_level         = "critical"
        notification_type  = "critical"
        alert_message      = (
            f"START DATE IN {days_until_start} DAY(S) - "
            f"Missing critical documents: {', '.join(missing_critical)}"
        )
    elif missing_critical:
        risk_level         = "warning"
        notification_type  = "conditional"
        alert_message      = (
            f"Hire is conditional - pending critical documents: {', '.join(missing_critical)}"
        )
    elif pending:
        risk_level         = "low"
        notification_type  = "standard"
        alert_message      = f"Non-critical items still pending: {', '.join(pending)}"
    else:
        risk_level         = "clear"
        notification_type  = "standard"
        alert_message      = "All critical documents complete."

    return json.dumps({
        "success":                    True,
        "risk_level":                 risk_level,
        "notification_type":          notification_type,
        "days_until_start":           days_until_start,
        "missing_critical_documents": missing_critical,
        "all_pending_documents":      pending,
        "alert_message":              alert_message,
    })


# ── Tool 4: Upload onboarding package as PDF to Azure Blob Storage ────────────

def store_document(employee_id: str, document_id: str, document_content_json: str) -> str:
    """
    Generate a PDF onboarding package and upload it to Azure Blob Storage.

    Args:
        employee_id:            The employee ID (EMP-XXXX).
        document_id:            A unique document reference ID (e.g. FORM-XXXXXXXX).
        document_content_json:  The form package as a JSON string.

    Returns:
        JSON string with the blob URL and upload confirmation.
    """
    try:
        raw = json.loads(document_content_json)
        # The agent sometimes passes the full tool response {"success": true, "form_package": {...}}
        # and sometimes just the form_package dict directly — handle both
        record = raw.get("form_package", raw) if isinstance(raw, dict) else {}
    except json.JSONDecodeError:
        record = {}

    # Ensure key identifiers are always populated using the function parameters as fallback
    if not record.get("employee_id"):
        record["employee_id"] = employee_id
    if not record.get("document_id"):
        record["document_id"] = document_id

    try:
        pdf_bytes = _generate_onboarding_pdf(record)
    except Exception as e:
        return json.dumps({"success": False, "error": f"PDF generation failed: {e}"})

    blob_name = f"onboarding/{employee_id}/{document_id}.pdf"
    conn_str  = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
    container = "onboarding-docs"

    if conn_str:
        try:
            from datetime import timedelta
            from azure.storage.blob import (
                BlobServiceClient, ContentSettings,
                generate_blob_sas, BlobSasPermissions,
            )
            svc = BlobServiceClient.from_connection_string(conn_str)
            try:
                svc.create_container(container)
            except Exception:
                pass
            blob_client = svc.get_blob_client(container=container, blob=blob_name)
            blob_client.upload_blob(
                pdf_bytes,
                overwrite=True,
                content_settings=ContentSettings(content_type="application/pdf"),
            )

            # Generate a SAS token valid for 7 days so the manager email link works
            # without requiring public access on the storage account
            account = svc.account_name
            account_key = svc.credential.account_key
            sas_token = generate_blob_sas(
                account_name=account,
                container_name=container,
                blob_name=blob_name,
                account_key=account_key,
                permission=BlobSasPermissions(read=True),
                expiry=datetime.now(timezone.utc) + timedelta(days=7),
            )
            sas_url = f"{blob_client.url}?{sas_token}"

            # Always save a local copy so the dashboard View PDF button works
            local_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data", "onboarding_docs")
            os.makedirs(local_dir, exist_ok=True)
            local_path = os.path.join(local_dir, f"{employee_id}_{document_id}.pdf")
            with open(local_path, "wb") as f:
                f.write(pdf_bytes)

            return json.dumps({
                "success":   True,
                "blob_url":  sas_url,
                "blob_name": blob_name,
                "container": container,
                "file_type": "pdf",
                "message":   f"Onboarding PDF for {employee_id} uploaded. Link valid for 7 days.",
            })
        except Exception as e:
            return json.dumps({"success": False, "error": f"Blob Storage error: {e}"})

    # Dev fallback
    local_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data", "onboarding_docs")
    os.makedirs(local_dir, exist_ok=True)
    local_path = os.path.join(local_dir, f"{employee_id}_{document_id}.pdf")
    with open(local_path, "wb") as f:
        f.write(pdf_bytes)
    return json.dumps({
        "success":   True,
        "blob_url":  f"local://{local_path}",
        "blob_name": blob_name,
        "container": "local",
        "file_type": "pdf",
        "message":   f"(Dev mode) Onboarding PDF saved locally at {local_path}",
    })


# ── Tool 5: Notify employee via email ────────────────────────────────────────

def notify_employee(
    employee_email: str,
    employee_name: str,
    employee_id: str,
    start_date: str,
    forms_required_json: str,
    pending_documents_json: str,
) -> str:
    """
    Send the new hire a welcome email listing all documents they must submit
    before their start date.

    Args:
        employee_email:        New hire's work email address.
        employee_name:         Full name of the new employee.
        employee_id:           Employee ID (EMP-XXXX).
        start_date:            Employee's start date (YYYY-MM-DD).
        forms_required_json:   JSON array string of all required form names.
        pending_documents_json: JSON array string of documents still outstanding.

    Returns:
        JSON string with send status and message ID.
    """
    override = os.getenv("EMPLOYEE_NOTIFICATION_EMAIL", "")
    if override:
        employee_email = override

    try:
        forms = json.loads(forms_required_json)
    except Exception:
        forms = []
    try:
        pending = json.loads(pending_documents_json)
    except Exception:
        pending = []

    forms_rows = "".join(
        f'<tr style="background:{"#F5F5F5" if i%2==0 else "#fff"}">'
        f'<td style="padding:8px 12px">{i+1}. {form}</td></tr>'
        for i, form in enumerate(forms)
    )
    pending_section = ""
    if pending:
        pending_rows = "".join(
            f'<li style="margin:4px 0">{doc}</li>' for doc in pending
        )
        pending_section = f"""
        <div style="background:#FFF3E0;border-left:4px solid #E65100;padding:14px 18px;margin:20px 0;border-radius:4px">
          <strong style="color:#E65100">Action Required — Documents Still Needed:</strong>
          <ul style="margin:8px 0 0;padding-left:20px;color:#424242">{pending_rows}</ul>
          <p style="margin:10px 0 0;font-size:12px;color:#757575">
            Please submit these documents to HR as soon as possible to avoid delays on your start date.
          </p>
        </div>"""

    subject = f"Welcome to Healthcare AI - Your Onboarding Documents - {employee_name}"
    body_html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px">
      <div style="background:#1565C0;color:#fff;padding:20px;border-radius:6px 6px 0 0">
        <h2 style="margin:0">Welcome to Healthcare AI!</h2>
        <p style="margin:4px 0 0;opacity:.8">New Employee Onboarding</p>
      </div>
      <div style="padding:24px;border:1px solid #E0E0E0;border-top:none;border-radius:0 0 6px 6px">
        <p>Dear {employee_name},</p>
        <p>We are excited to have you joining the team. Your start date is <strong>{start_date}</strong>.</p>
        <p>To complete your onboarding, please submit all of the following documents to HR before your first day:</p>

        <table style="border-collapse:collapse;width:100%;margin:16px 0;font-size:13px">
          {forms_rows}
        </table>

        {pending_section}

        <table style="border-collapse:collapse;width:100%;margin:16px 0">
          <tr style="background:#F5F5F5">
            <td style="padding:10px;font-weight:bold;width:40%">Employee ID</td>
            <td style="padding:10px">{employee_id}</td>
          </tr>
          <tr>
            <td style="padding:10px;font-weight:bold">Start Date</td>
            <td style="padding:10px">{start_date}</td>
          </tr>
          <tr style="background:#F5F5F5">
            <td style="padding:10px;font-weight:bold">HR Contact</td>
            <td style="padding:10px">hr@healthcare-ai.com</td>
          </tr>
        </table>

        <p>If you have any questions about any of these forms, please contact HR at hr@healthcare-ai.com.</p>
        <p>We look forward to welcoming you on your first day!</p>
        <p style="color:#9E9E9E;font-size:12px">- Healthcare AI HR Team</p>
      </div>
    </div>"""

    conn_str = os.getenv("AZURE_COMMS_CONNECTION_STRING", "")
    sender   = os.getenv("ADMIN_EMAIL_SENDER", "DoNotReply@healthcare-ai.com")

    if conn_str:
        try:
            from azure.communication.email import EmailClient
            client  = EmailClient.from_connection_string(conn_str)
            message = {
                "senderAddress": sender,
                "recipients":    {"to": [{"address": employee_email}]},
                "content":       {"subject": subject, "html": body_html},
            }
            poller = client.begin_send(message)
            result = poller.result()
            return json.dumps({
                "success":    True,
                "message_id": result.get("id", "unknown"),
                "recipient":  employee_email,
                "subject":    subject,
            })
        except Exception as e:
            return json.dumps({"success": False, "error": f"Email send failed: {e}"})

    print(f"\n[DEV] Employee email to {employee_email}\nSubject: {subject}\n")
    return json.dumps({
        "success":    True,
        "message_id": f"DEV-{uuid.uuid4().hex[:8]}",
        "recipient":  employee_email,
        "subject":    subject,
        "note":       "Dev mode - email printed to console, not sent.",
    })


# ── Tool 6: Notify manager via email ─────────────────────────────────────────

def notify_manager(
    manager_email: str,
    employee_name: str,
    employee_id: str,
    start_date: str,
    document_url: str,
    notification_type: str = "standard",
) -> str:
    """
    Send a new-hire onboarding notification email to the employee's manager.
    Notification type controls urgency: standard, conditional, or critical.

    Args:
        manager_email:      Manager's email address.
        employee_name:      Full name of the new employee.
        employee_id:        Employee ID (EMP-XXXX).
        start_date:         Employee's start date (YYYY-MM-DD).
        document_url:       URL or path to the uploaded onboarding PDF.
        notification_type:  standard | conditional | critical

    Returns:
        JSON string with send status and message ID.
    """
    override = os.getenv("MANAGER_NOTIFICATION_EMAIL", "")
    if override:
        manager_email = override

    if notification_type == "critical":
        subject = f"URGENT - Onboarding Documents Missing, Start Date Approaching - {employee_name} ({employee_id})"
        urgency_banner = """
        <div style="background:#C62828;color:#fff;padding:14px 20px;border-radius:6px;margin-bottom:20px">
          <strong>URGENT ACTION REQUIRED</strong><br/>
          This employee's start date is within 3 days and critical onboarding documents are still missing.
          Immediate follow-up is required to remain compliant.
        </div>"""
    elif notification_type == "conditional":
        subject = f"New Hire Onboarding - Pending Documents Required - {employee_name} ({employee_id})"
        urgency_banner = """
        <div style="background:#E65100;color:#fff;padding:14px 20px;border-radius:6px;margin-bottom:20px">
          <strong>CONDITIONAL HIRE - Documents Pending</strong><br/>
          This hire is contingent on completion of outstanding documents listed below.
          Please follow up with the employee and HR to ensure timely completion.
        </div>"""
    else:
        subject = f"New Hire Onboarding Ready - {employee_name} ({employee_id})"
        urgency_banner = ""

    body_html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px">
      <div style="background:#1565C0;color:#fff;padding:20px;border-radius:6px 6px 0 0">
        <h2 style="margin:0">Healthcare AI HR Agent</h2>
        <p style="margin:4px 0 0;opacity:.8">Employee Onboarding Notification</p>
      </div>
      <div style="padding:24px;border:1px solid #E0E0E0;border-top:none;border-radius:0 0 6px 6px">
        {urgency_banner}
        <p>Hello,</p>
        <p>The onboarding package for your new team member has been prepared.</p>
        <table style="border-collapse:collapse;width:100%;margin:16px 0">
          <tr style="background:#F5F5F5">
            <td style="padding:10px;font-weight:bold;width:40%">Employee</td>
            <td style="padding:10px">{employee_name}</td>
          </tr>
          <tr>
            <td style="padding:10px;font-weight:bold">Employee ID</td>
            <td style="padding:10px">{employee_id}</td>
          </tr>
          <tr style="background:#F5F5F5">
            <td style="padding:10px;font-weight:bold">Start Date</td>
            <td style="padding:10px">{start_date}</td>
          </tr>
          <tr>
            <td style="padding:10px;font-weight:bold">Onboarding Package</td>
            <td style="padding:10px"><a href="{document_url}" style="color:#1565C0">View PDF</a></td>
          </tr>
          <tr style="background:#F5F5F5">
            <td style="padding:10px;font-weight:bold">Status</td>
            <td style="padding:10px">{notification_type.upper()}</td>
          </tr>
        </table>
        <p>Please ensure all pending documents are completed before the employee's start date.</p>
        <p style="color:#9E9E9E;font-size:12px">- Healthcare AI HR Agent</p>
      </div>
    </div>"""

    conn_str = os.getenv("AZURE_COMMS_CONNECTION_STRING", "")
    sender   = os.getenv("ADMIN_EMAIL_SENDER", "DoNotReply@healthcare-ai.com")

    if conn_str:
        try:
            from azure.communication.email import EmailClient
            client  = EmailClient.from_connection_string(conn_str)
            message = {
                "senderAddress": sender,
                "recipients":    {"to": [{"address": manager_email}]},
                "content":       {"subject": subject, "html": body_html},
            }
            poller = client.begin_send(message)
            result = poller.result()
            return json.dumps({
                "success":    True,
                "message_id": result.get("id", "unknown"),
                "recipient":  manager_email,
                "subject":    subject,
                "notification_type": notification_type,
            })
        except Exception as e:
            return json.dumps({"success": False, "error": f"Email send failed: {e}"})

    # Dev fallback
    print(f"\n[DEV] Email to {manager_email}\nSubject: {subject}\n")
    return json.dumps({
        "success":          True,
        "message_id":       f"DEV-{uuid.uuid4().hex[:8]}",
        "recipient":        manager_email,
        "subject":          subject,
        "notification_type": notification_type,
        "note":             "Dev mode - email printed to console, not sent.",
    })
