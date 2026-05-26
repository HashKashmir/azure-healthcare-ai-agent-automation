"""
Generate a synthetic patient intake PDF and upload it to Azure Blob Storage.
Returns the blob URL with a 24-hour SAS token so Azure Document Intelligence
can access it directly.

Generates a different random patient each run using Faker.
"""

import os
import random
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
load_dotenv()

# ── Patient data pools ────────────────────────────────────────────────────────

INSURANCE_PLANS = [
    {"id": "INS-001", "name": "Blue Cross Blue Shield PPO"},
    {"id": "INS-002", "name": "UnitedHealthcare Choice Plus"},
    {"id": "INS-003", "name": "Aetna PPO Gold"},
    {"id": "INS-004", "name": "Cigna Open Access Plus"},
    {"id": "INS-005", "name": "Humana HMO"},
    {"id": "INS-006", "name": "Medicare Part B"},
    {"id": "INS-007", "name": "Medicaid AHCCCS"},
]

PROVIDERS = [
    "Dr. Carlos Mendes", "Dr. Angela Park", "Dr. Sarah Kim",
    "Dr. James Okafor", "Dr. Priya Nair", "Dr. Robert Chen",
    "Dr. Maria Gonzalez", "Dr. David Patel",
]

COMPLAINTS = [
    "Persistent lower back pain, 3 weeks duration",
    "Shortness of breath on exertion, onset 2 days ago",
    "Chest pain radiating to left arm, intermittent",
    "Severe headache with sensitivity to light, 12 hours",
    "Knee swelling and pain following sports injury",
    "Recurring abdominal pain, worsening after meals",
    "Dizziness and fatigue, 1 week duration",
    "Skin rash on forearms, spreading over 4 days",
    "High fever 103F, sore throat, difficulty swallowing",
    "Numbness and tingling in right hand, 2 weeks",
]

DEPARTMENTS = [
    "Orthopedics", "Cardiology", "Primary Care", "Neurology",
    "Emergency Medicine", "Oncology", "Pediatrics", "Radiology",
]

VISIT_TYPES = ["New Patient", "Follow-Up", "Urgent Care", "Consultation"]

FIRST_NAMES_M = ["James", "Robert", "Michael", "William", "David", "Carlos",
                  "Marcus", "Kevin", "Brian", "Anthony", "Daniel", "Ryan"]
FIRST_NAMES_F = ["Mary", "Patricia", "Jennifer", "Linda", "Barbara", "Elena",
                  "Sandra", "Ashley", "Rachel", "Michelle", "Laura", "Sara"]
LAST_NAMES    = ["Johnson", "Williams", "Martinez", "Brown", "Davis", "Wilson",
                  "Anderson", "Taylor", "Thomas", "Garcia", "Jackson", "White",
                  "Harris", "Thompson", "Lewis", "Robinson", "Walker", "Hall"]

AZ_CITIES = ["Phoenix", "Tucson", "Mesa", "Chandler", "Scottsdale", "Tempe"]


def _random_patient() -> dict:
    gender = random.choice(["Male", "Female"])
    first  = random.choice(FIRST_NAMES_M if gender == "Male" else FIRST_NAMES_F)
    last   = random.choice(LAST_NAMES)
    birth_year = random.randint(1950, 2005)
    birth_month = random.randint(1, 12)
    birth_day   = random.randint(1, 28)
    dob    = f"{birth_year}-{birth_month:02d}-{birth_day:02d}"
    street_num = random.randint(100, 9999)
    street = random.choice(["W Thomas Rd", "E McDowell Rd", "N 7th St",
                             "S Central Ave", "W Camelback Rd", "E Van Buren St"])
    city   = random.choice(AZ_CITIES)
    zipcode = f"850{random.randint(10, 99)}"
    phone  = f"({random.randint(480,623)}) 555-{random.randint(1000,9999)}"
    ssn    = f"{random.randint(1000,9999)}"
    insurance = random.choice(INSURANCE_PLANS)
    group  = f"GRP-{random.randint(10000,99999)}"
    provider = random.choice(PROVIDERS)
    referring = random.choice([p for p in PROVIDERS if p != provider])
    complaint = random.choice(COMPLAINTS)
    dept   = random.choice(DEPARTMENTS)
    visit  = random.choice(VISIT_TYPES)
    ec_first = random.choice(FIRST_NAMES_M + FIRST_NAMES_F)
    ec_last  = random.choice(LAST_NAMES)
    ec_rel   = random.choice(["Spouse", "Parent", "Sibling", "Child", "Friend"])
    ec_phone = f"({random.randint(480,623)}) 555-{random.randint(1000,9999)}"

    return {
        "first_name":       first,
        "last_name":        last,
        "dob":              dob,
        "gender":           gender,
        "address":          f"{street_num} {street}, {city}, AZ {zipcode}",
        "phone":            phone,
        "email":            f"{first.lower()}.{last.lower()}@email.com",
        "ssn_last4":        ssn,
        "insurance_id":     insurance["id"],
        "insurance_name":   insurance["name"],
        "group_number":     group,
        "provider":         provider,
        "referring":        referring,
        "complaint":        complaint,
        "department":       dept,
        "visit_type":       visit,
        "ec_name":          f"{ec_first} {ec_last}",
        "ec_phone":         ec_phone,
        "ec_relationship":  ec_rel,
    }


# ── 1. Generate PDF ───────────────────────────────────────────────────────────

def generate_pdf(output_path: str, patient: dict = None) -> dict:
    """Generate a patient intake PDF. Accepts an existing patient dict or generates a random one."""
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    if patient is None:
        patient = _random_patient()
    today   = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    pdf = FPDF()
    pdf.add_page()
    pdf.set_margins(20, 20, 20)

    def section(title: str) -> None:
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_fill_color(227, 242, 253)
        pdf.set_text_color(33, 33, 33)
        pdf.cell(0, 8, title, fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(1)

    def field(label: str, value: str) -> None:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(33, 33, 33)
        pdf.cell(65, 7, label + ":", new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.set_font("Helvetica", "", 9)
        v = str(value or "").encode("latin-1", errors="replace").decode("latin-1")
        pdf.cell(0, 7, v, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Header
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_fill_color(21, 101, 192)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 14, "Healthcare AI - Patient Intake Form", fill=True,
             new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.ln(4)

    # Patient Demographics
    section("Patient Demographics")
    field("Patient Last Name",  patient["last_name"])
    field("Patient First Name", patient["first_name"])
    field("Date of Birth",      patient["dob"])
    field("Gender",             patient["gender"])
    field("Address",            patient["address"])
    field("Phone",              patient["phone"])
    field("Email",              patient["email"])
    field("SSN (last 4)",       patient["ssn_last4"])
    pdf.ln(3)

    # Insurance Information
    section("Insurance Information")
    field("Insurance ID",    patient["insurance_id"])
    field("Insurance Plan",  patient["insurance_name"])
    field("Group Number",    patient["group_number"])
    field("Subscriber Name", f"{patient['first_name']} {patient['last_name']}")
    field("Relationship",    "Self")
    pdf.ln(3)

    # Visit Information
    section("Visit Information")
    field("Date of Service",     today)
    field("Primary Provider",    patient["provider"])
    field("Referring Physician", patient["referring"])
    field("Chief Complaint",     patient["complaint"])
    field("Visit Type",          patient["visit_type"])
    field("Department",          patient["department"])
    pdf.ln(3)

    # Emergency Contact
    section("Emergency Contact")
    field("Emergency Contact Name",  patient["ec_name"])
    field("Emergency Contact Phone", patient["ec_phone"])
    field("Relationship",            patient["ec_relationship"])
    pdf.ln(3)

    # Consent
    section("Consent & Signatures")
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(33, 33, 33)
    pdf.multi_cell(0, 5,
        "I authorize Healthcare AI and its affiliates to use and disclose my protected health "
        "information for treatment, payment, and healthcare operations as described in the Notice "
        "of Privacy Practices. I have received a copy of the Notice of Privacy Practices."
    )
    pdf.ln(2)
    field("Patient Signature", f"{patient['first_name']} {patient['last_name']}")
    field("Signature Date",    today)

    # Footer
    pdf.set_y(-20)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(0, 5,
        f"Generated by Healthcare AI Intake System - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        align="C"
    )

    pdf.output(output_path)
    print(f"[intake] PDF generated: {output_path}")
    print(f"[intake] Patient: {patient['first_name']} {patient['last_name']} | "
          f"Insurance: {patient['insurance_id']} ({patient['insurance_name']})")
    return patient


# ── 2. Upload to Azure Blob Storage with SAS token ────────────────────────────

def upload_and_get_sas_url(pdf_path: str, blob_name: str = None) -> str:
    conn_str  = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
    container = os.getenv("AZURE_STORAGE_CONTAINER_INTAKE", "intake-pdfs")

    if blob_name is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        blob_name = f"intake_{timestamp}.pdf"

    if not conn_str:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING not set in .env")

    from azure.storage.blob import (
        BlobServiceClient, ContentSettings,
        generate_blob_sas, BlobSasPermissions,
    )

    svc = BlobServiceClient.from_connection_string(conn_str)
    try:
        svc.create_container(container)
    except Exception:
        pass

    client = svc.get_blob_client(container=container, blob=blob_name)
    with open(pdf_path, "rb") as f:
        client.upload_blob(
            f, overwrite=True,
            content_settings=ContentSettings(content_type="application/pdf"),
        )
    print(f"[intake] Uploaded to blob: {container}/{blob_name}")

    parts        = dict(p.split("=", 1) for p in conn_str.split(";") if "=" in p)
    account_name = parts.get("AccountName", "")
    account_key  = parts.get("AccountKey", "")

    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=container,
        blob_name=blob_name,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(hours=24),
    )

    url = f"https://{account_name}.blob.core.windows.net/{container}/{blob_name}?{sas_token}"
    print(f"[intake] SAS URL generated (valid 24h)")
    return url


if __name__ == "__main__":
    import json as _json

    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    os.makedirs(data_dir, exist_ok=True)

    pdf_path = os.path.join(data_dir, "sample_intake.pdf")
    patient  = generate_pdf(pdf_path)

    # Save patient data so the intake agent can use it if Document Intelligence falls through
    patient_data_path = os.path.join(data_dir, "intake_patient_data.json")
    with open(patient_data_path, "w", encoding="utf-8") as f:
        _json.dump(patient, f, indent=2)
    print(f"[intake] Patient data saved to intake_patient_data.json")

    url = upload_and_get_sas_url(pdf_path)

    print(f"\nBLOB_URL={url}")
    out = os.path.join(data_dir, "intake_blob_url.txt")
    with open(out, "w") as f:
        f.write(url)
