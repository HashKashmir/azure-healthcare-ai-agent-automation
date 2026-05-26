"""
Automation 2 — Patient Intake & Document Capture tools.

The agent calls these to extract, validate, and store patient intake
data from PDFs using Azure Document Intelligence.
"""

import json
import os
import uuid
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

MOCK_INSURANCE_DB = {
    "INS-001": {"name": "Blue Cross Blue Shield PPO",    "eligible": True},
    "INS-002": {"name": "UnitedHealthcare Choice Plus",  "eligible": True},
    "INS-003": {"name": "Aetna PPO Gold",                "eligible": True},
    "INS-004": {"name": "Cigna Open Access Plus",        "eligible": True},
    "INS-005": {"name": "Humana HMO",                    "eligible": False, "reason": "Plan terminated — contact member services"},
    "INS-006": {"name": "Medicare Part B",               "eligible": True},
    "INS-007": {"name": "Medicaid AHCCCS",               "eligible": True},
    "INS-999": {"name": "Unknown",                       "eligible": False, "reason": "Insurance ID not found in payer database"},
}


# ── Tool 1: Extract fields from intake PDF ───────────────────────────────────

def extract_document_fields(blob_url: str) -> str:
    """
    Extract structured patient fields from an uploaded intake PDF using
    Azure Document Intelligence (Form Recognizer).  Returns patient name,
    DOB, insurance ID, provider, and all extracted key-value pairs.

    Args:
        blob_url: Azure Blob Storage URL of the uploaded PDF
                  (e.g. https://healthcareprojectdata.blob.core.windows.net/intake-pdfs/form.pdf).

    Returns:
        JSON string with extracted_fields dict, field_count, and document_id.
    """
    doc_endpoint = os.getenv("DOC_INTELLIGENCE_ENDPOINT", "")
    doc_key      = os.getenv("DOC_INTELLIGENCE_KEY", "")

    document_id = f"DOC-{uuid.uuid4().hex[:8].upper()}"

    if doc_endpoint and doc_key:
        try:
            from azure.ai.documentintelligence import DocumentIntelligenceClient
            from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
            from azure.core.credentials import AzureKeyCredential

            client = DocumentIntelligenceClient(
                endpoint=doc_endpoint,
                credential=AzureKeyCredential(doc_key),
            )
            poller = client.begin_analyze_document(
                "prebuilt-layout",
                AnalyzeDocumentRequest(url_source=blob_url),
            )
            result = poller.result()

            fields = {}
            if result.key_value_pairs:
                for kv in result.key_value_pairs:
                    if kv.key and kv.value:
                        fields[kv.key.content] = kv.value.content

            if fields:
                return json.dumps({
                    "success":          True,
                    "document_id":      document_id,
                    "blob_url":         blob_url,
                    "extracted_fields": fields,
                    "field_count":      len(fields),
                    "extracted_at":     datetime.now(timezone.utc).isoformat(),
                })
            # Zero fields returned — fall through to mock extraction
        except Exception:
            pass  # Fall through to mock extraction below

    # Bulk mode: local-dev://{key} URLs point to a patient-specific data file
    if blob_url.startswith("local-dev://"):
        key = blob_url[len("local-dev://"):]
        patient_data_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "data", f"intake_patient_{key}.json"
        )
    else:
        patient_data_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "intake_patient_data.json"
        )

    try:
        with open(patient_data_path, encoding="utf-8") as f:
            p = json.load(f)
        mock_fields = {
            "Patient Last Name":       p.get("last_name", ""),
            "Patient First Name":      p.get("first_name", ""),
            "Date of Birth":           p.get("dob", ""),
            "Gender":                  p.get("gender", ""),
            "Address":                 p.get("address", ""),
            "Phone":                   p.get("phone", ""),
            "Email":                   p.get("email", ""),
            "SSN (last 4)":            p.get("ssn_last4", ""),
            "Insurance ID":            p.get("insurance_id", ""),
            "Insurance Plan":          p.get("insurance_name", ""),
            "Group Number":            p.get("group_number", ""),
            "Primary Provider":        p.get("provider", ""),
            "Referring Physician":     p.get("referring", ""),
            "Chief Complaint":         p.get("complaint", ""),
            "Department":              p.get("department", ""),
            "Visit Type":              p.get("visit_type", ""),
            "Date of Service":         datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "Emergency Contact Name":  p.get("ec_name", ""),
            "Emergency Contact Phone": p.get("ec_phone", ""),
        }
        note = "Document Intelligence unavailable — fields read from generated patient data."
    except Exception:
        # Absolute last resort if no patient data file exists
        mock_fields = {
            "Patient Last Name":       "Martinez",
            "Patient First Name":      "Elena",
            "Date of Birth":           "1985-03-12",
            "Gender":                  "Female",
            "Address":                 "4821 W Thomas Rd, Phoenix, AZ 85031",
            "Phone":                   "(602) 555-0193",
            "Email":                   "elena.martinez@email.com",
            "Insurance ID":            "INS-003",
            "Insurance Plan":          "Aetna PPO Gold",
            "Group Number":            "GRP-84721",
            "Primary Provider":        "Dr. Carlos Mendes",
            "Referring Physician":     "Dr. Angela Park",
            "Chief Complaint":         "Persistent lower back pain, 3 weeks duration",
            "Date of Service":         datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "Emergency Contact Name":  "Roberto Martinez",
            "Emergency Contact Phone": "(602) 555-0204",
        }
        note = "Dev fallback — default mock patient used (no patient data file found)."

    return json.dumps({
        "success":          True,
        "document_id":      document_id,
        "blob_url":         blob_url,
        "extracted_fields": mock_fields,
        "field_count":      len(mock_fields),
        "extracted_at":     datetime.now(timezone.utc).isoformat(),
        "note":             note,
    })


# ── Tool 2: Validate insurance eligibility ───────────────────────────────────

def validate_insurance(insurance_id: str, patient_name: str, date_of_service: str) -> str:
    """
    Check patient insurance eligibility against the payer eligibility system.
    In production this would call a real-time eligibility API (270/271 EDI).

    Args:
        insurance_id:    The patient's insurance member ID extracted from the intake form.
        patient_name:    Patient's full name (for the eligibility request).
        date_of_service: Date of service in YYYY-MM-DD format.

    Returns:
        JSON string with is_eligible flag, plan name, and any ineligibility reason.
    """
    record = MOCK_INSURANCE_DB.get(insurance_id, MOCK_INSURANCE_DB["INS-999"])
    return json.dumps({
        "success":        True,
        "insurance_id":   insurance_id,
        "patient_name":   patient_name,
        "date_of_service": date_of_service,
        "is_eligible":    record["eligible"],
        "plan_name":      record["name"],
        "reason":         record.get("reason", ""),
        "checked_at":     datetime.now(timezone.utc).isoformat(),
    })


# ── Tool 3: Store indexed patient record ─────────────────────────────────────

def store_indexed_record(
    document_id: str,
    extracted_fields_json: str,
    blob_url: str,
    is_eligible: bool,
) -> str:
    """
    Save the structured patient intake record (extracted fields + eligibility
    result) to Azure Blob Storage as an indexed JSON record, mirroring
    a DocVault document management system.

    Args:
        document_id:            Unique document reference ID (DOC-XXXXXXXX).
        extracted_fields_json:  JSON string of fields extracted from the PDF.
        blob_url:               Original PDF blob URL for the source reference.
        is_eligible:            Whether insurance eligibility was confirmed.

    Returns:
        JSON string with the stored record URL and confirmation.
    """
    try:
        fields = json.loads(extracted_fields_json)
    except json.JSONDecodeError:
        fields = {"raw": extracted_fields_json}

    patient_id = f"PAT-{uuid.uuid4().hex[:6].upper()}"
    record = {
        "patient_id":     patient_id,
        "document_id":    document_id,
        "source_pdf_url": blob_url,
        "is_eligible":    is_eligible,
        "extracted_fields": fields,
        "indexed_at":     datetime.now(timezone.utc).isoformat(),
        "indexed_by":     "healthcare-ai-intake-agent",
    }
    record_json = json.dumps(record, indent=2)
    blob_name   = f"indexed/{patient_id}/{document_id}.json"
    conn_str    = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
    container   = os.getenv("AZURE_STORAGE_CONTAINER_INTAKE", "intake-pdfs")

    if conn_str:
        try:
            from azure.storage.blob import BlobServiceClient
            svc    = BlobServiceClient.from_connection_string(conn_str)
            client = svc.get_blob_client(container=container, blob=blob_name)
            client.upload_blob(record_json, overwrite=True)
            return json.dumps({
                "success":    True,
                "patient_id": patient_id,
                "record_url": client.url,
                "blob_name":  blob_name,
            })
        except Exception as e:
            return json.dumps({"success": False, "error": f"Blob Storage error: {e}"})

    # Dev fallback
    local_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data", "indexed_records")
    os.makedirs(local_dir, exist_ok=True)
    local_path = os.path.join(local_dir, f"{patient_id}_{document_id}.json")
    with open(local_path, "w", encoding="utf-8") as f:
        f.write(record_json)
    return json.dumps({
        "success":    True,
        "patient_id": patient_id,
        "record_url": f"local://{local_path}",
        "blob_name":  blob_name,
        "note":       "Dev mode — saved locally.",
    })


# ── Tool 4: Send confirmation email to patient ────────────────────────────────

def notify_patient(
    patient_name: str,
    patient_email: str,
    patient_id: str,
    document_id: str,
    date_of_service: str,
    provider: str,
    is_eligible: bool,
    plan_name: str,
) -> str:
    """
    Send the patient a confirmation email that their intake form was received,
    their insurance was verified, and their record has been created.

    Args:
        patient_name:    Full name of the patient.
        patient_email:   Patient's email address.
        patient_id:      Generated patient ID (PAT-XXXXXX).
        document_id:     Document reference ID (DOC-XXXXXXXX).
        date_of_service: Date of service in YYYY-MM-DD format.
        provider:        Primary provider name.
        is_eligible:     Whether insurance eligibility was confirmed.
        plan_name:       Insurance plan name.

    Returns:
        JSON string with send status and message ID.
    """
    override = os.getenv("PATIENT_NOTIFICATION_EMAIL", "")
    if override:
        patient_email = override

    eligibility_row = (
        '<tr style="background:#E8F5E9"><td style="padding:10px;font-weight:bold">Insurance Status</td>'
        '<td style="padding:10px;color:#2E7D32">Verified - Eligible</td></tr>'
        if is_eligible else
        '<tr style="background:#FFEBEE"><td style="padding:10px;font-weight:bold">Insurance Status</td>'
        '<td style="padding:10px;color:#C62828">Not Eligible - Please contact our billing office</td></tr>'
    )

    subject = f"Your Intake Form Has Been Received - Healthcare AI"
    body_html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px">
      <div style="background:#1565C0;color:#fff;padding:20px;border-radius:6px 6px 0 0">
        <h2 style="margin:0">Healthcare AI</h2>
        <p style="margin:4px 0 0;opacity:.8">Patient Intake Confirmation</p>
      </div>
      <div style="padding:24px;border:1px solid #E0E0E0;border-top:none;border-radius:0 0 6px 6px">
        <p>Dear {patient_name},</p>
        <p>Your patient intake form has been received and processed. Your information
        has been verified and your record has been created in our system.</p>
        <table style="border-collapse:collapse;width:100%;margin:16px 0">
          <tr style="background:#F5F5F5">
            <td style="padding:10px;font-weight:bold;width:40%">Patient Name</td>
            <td style="padding:10px">{patient_name}</td>
          </tr>
          <tr>
            <td style="padding:10px;font-weight:bold">Patient ID</td>
            <td style="padding:10px">{patient_id}</td>
          </tr>
          <tr style="background:#F5F5F5">
            <td style="padding:10px;font-weight:bold">Reference Number</td>
            <td style="padding:10px">{document_id}</td>
          </tr>
          <tr>
            <td style="padding:10px;font-weight:bold">Date of Service</td>
            <td style="padding:10px">{date_of_service}</td>
          </tr>
          <tr style="background:#F5F5F5">
            <td style="padding:10px;font-weight:bold">Provider</td>
            <td style="padding:10px">{provider}</td>
          </tr>
          <tr>
            <td style="padding:10px;font-weight:bold">Insurance Plan</td>
            <td style="padding:10px">{plan_name}</td>
          </tr>
          {eligibility_row}
        </table>
        <p>Please arrive 15 minutes before your appointment. Bring a valid photo ID
        and your insurance card.</p>
        <p>If you have any questions, contact us at <strong>intake@healthcare-ai.com</strong>
        or call <strong>(602) 555-0100</strong>.</p>
        <p style="color:#9E9E9E;font-size:12px">- Healthcare AI Patient Services</p>
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
                "recipients":    {"to": [{"address": patient_email}]},
                "content":       {"subject": subject, "html": body_html},
            }
            poller = client.begin_send(message)
            result = poller.result()
            return json.dumps({
                "success":    True,
                "message_id": result.get("id", "unknown"),
                "recipient":  patient_email,
                "subject":    subject,
            })
        except Exception as e:
            return json.dumps({"success": False, "error": f"Email send failed: {e}"})

    print(f"\n[DEV] Patient email to {patient_email}\nSubject: {subject}\n")
    return json.dumps({
        "success":    True,
        "message_id": f"DEV-{uuid.uuid4().hex[:8]}",
        "recipient":  patient_email,
        "subject":    subject,
        "note":       "Dev mode - email printed to console.",
    })


# ── Tool 5: Alert front desk on ineligible insurance ─────────────────────────

def notify_staff_ineligible(
    patient_name: str,
    patient_id: str,
    document_id: str,
    insurance_id: str,
    plan_name: str,
    ineligibility_reason: str,
    date_of_service: str,
) -> str:
    """
    Alert the front desk / billing team when a patient's insurance is found
    to be ineligible during intake processing. Requires immediate follow-up
    before the appointment proceeds.

    Args:
        patient_name:          Full name of the patient.
        patient_id:            Generated patient ID (PAT-XXXXXX).
        document_id:           Document reference ID (DOC-XXXXXXXX).
        insurance_id:          The insurance member ID that failed eligibility.
        plan_name:             Insurance plan name.
        ineligibility_reason:  Reason returned by the eligibility check.
        date_of_service:       Date of service in YYYY-MM-DD format.

    Returns:
        JSON string with send status and message ID.
    """
    staff_email = os.getenv("INTAKE_STAFF_EMAIL", "")
    if not staff_email:
        staff_email = os.getenv("ADMIN_EMAIL_RECIPIENTS", "")

    subject = f"INTAKE ALERT - Insurance Not Eligible - {patient_name} ({patient_id})"
    body_html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px">
      <div style="background:#C62828;color:#fff;padding:20px;border-radius:6px 6px 0 0">
        <h2 style="margin:0">Insurance Eligibility Alert</h2>
        <p style="margin:4px 0 0;opacity:.8">Immediate Action Required</p>
      </div>
      <div style="padding:24px;border:1px solid #E0E0E0;border-top:none;border-radius:0 0 6px 6px">
        <div style="background:#FFEBEE;border-left:4px solid #C62828;padding:14px;margin-bottom:20px;border-radius:4px">
          <strong>A patient's insurance has been flagged as ineligible during automated intake processing.</strong>
          Please contact the patient to resolve their coverage before their appointment proceeds.
        </div>
        <table style="border-collapse:collapse;width:100%;margin:16px 0">
          <tr style="background:#F5F5F5">
            <td style="padding:10px;font-weight:bold;width:40%">Patient Name</td>
            <td style="padding:10px">{patient_name}</td>
          </tr>
          <tr>
            <td style="padding:10px;font-weight:bold">Patient ID</td>
            <td style="padding:10px">{patient_id}</td>
          </tr>
          <tr style="background:#F5F5F5">
            <td style="padding:10px;font-weight:bold">Reference Number</td>
            <td style="padding:10px">{document_id}</td>
          </tr>
          <tr>
            <td style="padding:10px;font-weight:bold">Insurance ID</td>
            <td style="padding:10px">{insurance_id}</td>
          </tr>
          <tr style="background:#F5F5F5">
            <td style="padding:10px;font-weight:bold">Plan Name</td>
            <td style="padding:10px">{plan_name}</td>
          </tr>
          <tr>
            <td style="padding:10px;font-weight:bold">Reason</td>
            <td style="padding:10px;color:#C62828"><strong>{ineligibility_reason}</strong></td>
          </tr>
          <tr style="background:#F5F5F5">
            <td style="padding:10px;font-weight:bold">Date of Service</td>
            <td style="padding:10px">{date_of_service}</td>
          </tr>
        </table>
        <p><strong>Next steps:</strong> Contact the patient to obtain updated insurance information
        or arrange self-pay options before the appointment date.</p>
        <p style="color:#9E9E9E;font-size:12px">- Healthcare AI Intake Agent</p>
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
                "recipients":    {"to": [{"address": staff_email}]},
                "content":       {"subject": subject, "html": body_html},
            }
            poller = client.begin_send(message)
            result = poller.result()
            return json.dumps({
                "success":    True,
                "message_id": result.get("id", "unknown"),
                "recipient":  staff_email,
                "subject":    subject,
            })
        except Exception as e:
            return json.dumps({"success": False, "error": f"Email send failed: {e}"})

    print(f"\n[DEV] Staff alert to {staff_email}\nSubject: {subject}\n")
    return json.dumps({
        "success":    True,
        "message_id": f"DEV-{uuid.uuid4().hex[:8]}",
        "recipient":  staff_email,
        "subject":    subject,
        "note":       "Dev mode - alert printed to console.",
    })
