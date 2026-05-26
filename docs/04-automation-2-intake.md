# Automation 2 — Patient Intake & Document Capture

This document explains every file, function, and design decision in Automation 2. You will understand exactly how a patient's PDF intake form becomes a structured, validated, stored digital record — and how the agent handles insurance eligibility failures.

---

## What Problem Does This Solve?

When a patient arrives at a healthcare facility or submits a form ahead of their visit, intake staff must:

1. Take the physical or electronic intake form
2. Manually read and type out each field into the patient management system
3. Verify whether the patient's insurance is currently active and eligible
4. Create a patient record in the document management system
5. Confirm with the patient that their intake was received
6. Alert front desk staff if the insurance is invalid

This is tedious, error-prone (manual data entry mistakes are common), and creates delays. Automation 2 does all of it in seconds using Azure Document Intelligence to read the PDF and AI to orchestrate the workflow.

---

## Files Involved

| File | Role |
|---|---|
| `agent/tools/intake_tools.py` | All 5 tool functions the agent calls |
| `agent/prompts/intake_prompt.txt` | System instructions for the intake agent |
| `agent/agent_runner.py` | The core agent loop |
| `agent/bulk_intake_runner.py` | Batch processor for multiple patients |
| `scripts/generate_intake_pdf.py` | Generates synthetic patient PDFs and uploads to Blob |
| `data/sample_intake.pdf` | Pre-generated intake PDF (used in single-run mode) |
| `data/bulk_intake_template.xlsx` | Excel template for bulk intake |

---

## How a Single Intake Run Starts

The intake automation is slightly more complex to start than the others because it requires a PDF to already exist in Azure Blob Storage before the agent can do anything.

**From the dashboard "Run Intake" button:**

1. The dashboard calls `GET /run/intake`
2. The server first runs `scripts/generate_intake_pdf.py` as a subprocess
3. That script generates a random patient PDF and uploads it to Azure Blob Storage
4. It writes the Blob SAS URL to `data/intake_blob_url.txt`
5. The dashboard reads that URL
6. The dashboard then starts the agent: `agent_runner --automation intake --blob-url {url}`

**Why this two-step approach?** The agent needs a real URL to pass to Azure Document Intelligence. The intake PDF must exist and be accessible before the agent can start. Generating it first and passing the URL as an argument keeps the agent's responsibility clean — it doesn't need to know how to generate PDFs, only how to process them.

---

## `scripts/generate_intake_pdf.py` — Generating Patient PDFs

### What It Does

This script has two jobs:
1. Generate a synthetic patient intake PDF using `fpdf2`
2. Upload that PDF to Azure Blob Storage and return a 24-hour SAS URL

### Patient Data Generation

Every run generates a different random patient using pre-defined pools of names, addresses, insurance plans, providers, and medical complaints:

```python
def _random_patient() -> dict:
    gender = random.choice(["Male", "Female"])
    first  = random.choice(FIRST_NAMES_M if gender == "Male" else FIRST_NAMES_F)
    last   = random.choice(LAST_NAMES)
    insurance = random.choice(INSURANCE_PLANS)  # one of INS-001 through INS-007
    ...
```

The insurance ID pool intentionally includes `INS-005` (Humana HMO — terminated plan) which the intake tool will flag as ineligible. This means roughly 1 in 7 random patients will trigger the ineligibility notification workflow.

### PDF Structure

The generated PDF (`data/sample_intake.pdf`) looks like a real patient intake form with four sections:

1. **Patient Demographics** — last name, first name, date of birth, gender, address, phone, email, SSN last 4
2. **Insurance Information** — insurance ID, plan name, group number, subscriber name, relationship
3. **Visit Information** — date of service, primary provider, referring physician, chief complaint, visit type, department
4. **Emergency Contact** — name, phone, relationship
5. **Consent & Signatures** — HIPAA consent language, patient signature, date

This is the document Azure Document Intelligence will read. The field labels in the PDF match exactly what the fallback mock extraction returns, which is intentional — both Document Intelligence and the mock fallback should produce the same field structure.

### Patient Data File

When the PDF is generated, the script also saves the patient's data as JSON:

```python
patient_data_path = os.path.join(data_dir, "intake_patient_data.json")
with open(patient_data_path, "w") as f:
    json.dump(patient, f, indent=2)
```

This file is the fallback source when Azure Document Intelligence is unavailable. The `extract_document_fields` tool reads from this file when the Document Intelligence API either isn't configured or returns zero fields.

### Azure Blob Upload

```python
def upload_and_get_sas_url(pdf_path: str, blob_name: str = None) -> str:
    svc = BlobServiceClient.from_connection_string(conn_str)
    svc.create_container(container)  # safe if already exists
    client = svc.get_blob_client(container=container, blob=blob_name)
    with open(pdf_path, "rb") as f:
        client.upload_blob(f, overwrite=True, content_settings=ContentSettings(content_type="application/pdf"))

    sas_token = generate_blob_sas(
        ...,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(hours=24),  # 24-hour window
    )
    url = f"https://{account_name}.blob.core.windows.net/{container}/{blob_name}?{sas_token}"
    return url
```

The 24-hour SAS token is enough for Document Intelligence to fetch the PDF. Intake PDFs don't need to be accessible long-term like onboarding PDFs, which is why the expiry is shorter.

---

## Tool 1 — `extract_document_fields`

### What It Does

Sends the PDF (via its Blob URL) to Azure Document Intelligence and gets back a dictionary of extracted field-value pairs. Falls back to reading the local patient data file if Document Intelligence isn't available.

### Azure Document Intelligence Call

```python
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest

client = DocumentIntelligenceClient(
    endpoint=doc_endpoint,
    credential=AzureKeyCredential(doc_key),
)
poller = client.begin_analyze_document(
    "prebuilt-layout",                           # no custom training needed
    AnalyzeDocumentRequest(url_source=blob_url), # Document Intelligence fetches the PDF itself
)
result = poller.result()

fields = {}
if result.key_value_pairs:
    for kv in result.key_value_pairs:
        if kv.key and kv.value:
            fields[kv.key.content] = kv.value.content
```

`begin_analyze_document` is asynchronous — it submits the job and returns a poller. `poller.result()` blocks until the analysis is complete (typically 2–8 seconds for a simple 1-page form).

The `key_value_pairs` property contains all the field labels and their values that Document Intelligence found in the document. For our structured intake form, this produces accurate results because the form layout is clear.

### The `prebuilt-layout` Model

Azure Document Intelligence offers several models:
- `prebuilt-layout` — general purpose, extracts text, tables, and key-value pairs from any document
- `prebuilt-invoice`, `prebuilt-receipt`, `prebuilt-id-document` — specialized for specific document types
- Custom models — trained on your own documents for highest accuracy

This project uses `prebuilt-layout` because it works on any structured form without needing training data. For a production system processing thousands of intake forms, a custom-trained model would give higher accuracy.

### Fallback Logic

```python
# If Document Intelligence returned zero fields, fall through to mock
if fields:
    return json.dumps({"success": True, "extracted_fields": fields, ...})
# Zero fields — fall through

# Check for bulk-mode local-dev URL
if blob_url.startswith("local-dev://"):
    key = blob_url[len("local-dev://"):]
    patient_data_path = f"data/intake_patient_{key}.json"
else:
    patient_data_path = "data/intake_patient_data.json"

with open(patient_data_path) as f:
    p = json.load(f)
mock_fields = {
    "Patient Last Name":   p.get("last_name", ""),
    "Patient First Name":  p.get("first_name", ""),
    "Insurance ID":        p.get("insurance_id", ""),
    ...
}
```

The `local-dev://` URL scheme is used by the bulk intake runner. Instead of uploading each patient's PDF to Azure, it passes `local-dev://{key}` where `key` is a UUID. The extraction tool reads from `data/intake_patient_{key}.json` — the file the bulk runner wrote before starting the agent.

### What It Returns

```json
{
  "success": true,
  "document_id": "DOC-4F2A8B1C",
  "blob_url": "https://healthcareprojectdata.blob.core.windows.net/intake-pdfs/...",
  "extracted_fields": {
    "Patient Last Name": "Johnson",
    "Patient First Name": "Mary",
    "Date of Birth": "1975-04-22",
    "Insurance ID": "INS-003",
    "Insurance Plan": "Aetna PPO Gold",
    "Primary Provider": "Dr. Carlos Mendes",
    "Chief Complaint": "Persistent lower back pain, 3 weeks duration",
    "Date of Service": "2025-05-25"
  },
  "field_count": 19,
  "extracted_at": "2025-05-25T14:32:00Z"
}
```

The `document_id` (`DOC-4F2A8B1C`) is generated here and used in all subsequent steps as the reference ID for this intake document.

---

## Tool 2 — `validate_insurance`

### What It Does

Checks whether the patient's insurance ID is currently active and eligible.

In a production healthcare system, this would call a real-time eligibility checking service using the 270/271 EDI transaction standard (the healthcare industry's standard for electronic eligibility verification). In this project, it uses a mock database of insurance plans:

```python
MOCK_INSURANCE_DB = {
    "INS-001": {"name": "Blue Cross Blue Shield PPO",   "eligible": True},
    "INS-002": {"name": "UnitedHealthcare Choice Plus", "eligible": True},
    "INS-003": {"name": "Aetna PPO Gold",               "eligible": True},
    "INS-004": {"name": "Cigna Open Access Plus",       "eligible": True},
    "INS-005": {"name": "Humana HMO",                   "eligible": False, "reason": "Plan terminated — contact member services"},
    "INS-006": {"name": "Medicare Part B",              "eligible": True},
    "INS-007": {"name": "Medicaid AHCCCS",              "eligible": True},
    "INS-999": {"name": "Unknown",                      "eligible": False, "reason": "Insurance ID not found in payer database"},
}
```

`INS-999` is the fallback for any ID not in the database. `INS-005` is intentionally ineligible — it represents a patient whose Humana HMO plan was terminated.

```python
record = MOCK_INSURANCE_DB.get(insurance_id, MOCK_INSURANCE_DB["INS-999"])
return json.dumps({
    "success":        True,
    "is_eligible":    record["eligible"],
    "plan_name":      record["name"],
    "reason":         record.get("reason", ""),
    ...
})
```

### Why the `is_eligible` Result Matters

The eligibility result determines which of two different branches the agent takes:

- **If eligible:** Call `store_indexed_record` → call `notify_patient`
- **If ineligible:** Call `store_indexed_record` → call `notify_patient` → also call `notify_staff_ineligible`

Both paths create the record and notify the patient. But the ineligible path adds an alert to intake staff because someone needs to contact the patient before their appointment.

---

## Tool 3 — `store_indexed_record`

### What It Does

Creates a structured patient record as JSON and saves it to Azure Blob Storage, mirroring what a document management system like DocVault would do — indexing each document with metadata so it can be retrieved later.

```python
patient_id = f"PAT-{uuid.uuid4().hex[:6].upper()}"  # e.g., PAT-7A3F2B
record = {
    "patient_id":     patient_id,
    "document_id":    document_id,
    "source_pdf_url": blob_url,
    "is_eligible":    is_eligible,
    "extracted_fields": fields,
    "indexed_at":     datetime.now(timezone.utc).isoformat(),
    "indexed_by":     "healthcare-ai-intake-agent",
}
```

The JSON is uploaded to `intake-pdfs/indexed/{patient_id}/{document_id}.json`. This path structure creates a virtual folder per patient, allowing all documents for one patient to be listed by prefix.

### Dev Fallback

If Blob Storage credentials aren't configured, the record is saved to `data/indexed_records/{patient_id}_{document_id}.json` locally. The function returns the same success structure either way.

---

## Tool 4 — `notify_patient`

### What It Does

Sends the patient a confirmation email that their intake was received and processed. The email includes:
- Their patient ID (PAT-XXXXXX) — their reference number for this visit
- The reference number (DOC-XXXXXXXX) — the document ID
- Date of service
- Provider name
- Insurance plan and eligibility status (green "Verified - Eligible" or red "Not Eligible" row)
- Instructions to arrive 15 minutes early and bring photo ID and insurance card

### Insurance Status Row

```python
eligibility_row = (
    '<tr style="background:#E8F5E9"><td ...>Insurance Status</td>'
    '<td style="color:#2E7D32">Verified - Eligible</td></tr>'
    if is_eligible else
    '<tr style="background:#FFEBEE"><td ...>Insurance Status</td>'
    '<td style="color:#C62828">Not Eligible - Please contact our billing office</td></tr>'
)
```

Even when the patient is ineligible, they receive this email so they know their intake was received and that they need to contact the billing office. The ineligible patient is not left confused — they get clear information.

### Email Override

Like all notification tools, the `PATIENT_NOTIFICATION_EMAIL` environment variable overrides the recipient. In this project, all test emails go to `hashimchughtai@gmail.com`.

---

## Tool 5 — `notify_staff_ineligible`

### What It Does

When a patient's insurance fails the eligibility check, this tool sends an alert to front desk / intake staff. It is called in addition to `notify_patient`, not instead of it.

The email uses a red header (versus blue for normal notifications) and includes:
- A clear "Insurance Eligibility Alert — Immediate Action Required" header
- A red alert box explaining that the patient's insurance failed
- Full patient details and the ineligibility reason
- Next steps guidance: "Contact the patient to obtain updated insurance information or arrange self-pay options before the appointment date"

This is a critical workflow — if a patient's insurance is invalid and no one follows up, the organization either loses the revenue (sees the patient and can't collect) or the patient shows up with no coverage and is not financially prepared.

---

## The Complete Flow — Single Patient

```
User clicks "Run Intake" in dashboard
        │
        ▼
dashboard.py → runs scripts/generate_intake_pdf.py
        ├── generates random patient data
        ├── creates PDF (fpdf2)
        ├── saves data/intake_patient_data.json
        ├── uploads PDF to Azure Blob Storage (intake-pdfs container)
        └── writes SAS URL to data/intake_blob_url.txt
        │
dashboard reads blob_url from file
        │
        ▼
dashboard.py → starts agent_runner subprocess with --blob-url {url}
        │
        ▼
agent_runner.py → sends to o4-mini:
"Process the patient intake document at {url}. Extract all fields..."
        │
Model responds: call extract_document_fields("{url}")
        │
        ▼
extract_document_fields
        ├── (if Document Intelligence configured) calls Azure Document Intelligence with url
        │     └── receives key-value pairs from the PDF
        └── (fallback) reads data/intake_patient_data.json
        └── returns: document_id, extracted_fields (including Insurance ID)
        │
Model responds: call validate_insurance("{insurance_id}", "{patient_name}", "{date_of_service}")
        │
        ▼
validate_insurance → checks MOCK_INSURANCE_DB
        └── returns: is_eligible, plan_name, reason (if ineligible)
        │
Model responds: call store_indexed_record("{document_id}", "{fields_json}", "{blob_url}", {is_eligible})
        │
        ▼
store_indexed_record
        ├── generates patient_id (PAT-XXXXXX)
        ├── builds record JSON with all extracted fields + eligibility
        ├── uploads to Azure Blob Storage (intake-pdfs/indexed/{patient_id}/)
        └── returns: patient_id, record_url
        │
Model responds: call notify_patient(...)
        │
        ▼
notify_patient → Azure Communication Services
        └── sends confirmation email (with insurance status)
        │
Model responds (IF ineligible): call notify_staff_ineligible(...)
        │
        ▼
notify_staff_ineligible → Azure Communication Services
        └── sends red alert email to front desk staff
        │
Model responds: finish_reason="stop" — writes final text summary
        │
        ▼
dashboard shows result card: patient name, insurance status badge, View PDF button
```

---

## Bulk Processing — `agent/bulk_intake_runner.py`

### Key Difference from Bulk Onboarding

Bulk intake is more complex than bulk onboarding because:
1. Each patient needs their own PDF generated (not just an API call)
2. Each patient needs their own JSON data file so the extraction fallback works
3. The PDF must be uploaded to Azure Blob (or a local-dev fallback) before the agent starts

### Per-Patient Setup

For each patient in the batch:

```python
key  = uuid.uuid4().hex[:8]  # unique key for this patient's files
name = f"{patient['first_name']} {patient['last_name']}"

# Write patient data JSON (for extraction fallback)
patient_json_path = f"data/intake_patient_{key}.json"
with open(patient_json_path, "w") as f:
    json.dump(patient, f)

# Generate PDF with this patient's data
pdf_path = f"data/intake_bulk_{key}.pdf"
generate_pdf(pdf_path, patient=patient)

# Try Azure Blob upload; fall back to local-dev URL
try:
    blob_url = upload_and_get_sas_url(pdf_path, blob_name=f"intake_bulk_{key}.pdf")
except Exception:
    blob_url = f"local-dev://{key}"
```

The `local-dev://` URL scheme tells `extract_document_fields` to read from `data/intake_patient_{key}.json` instead of calling Document Intelligence. This means bulk intake works entirely offline if needed.

### Pre-Eligibility Check

The bulk runner pre-checks insurance eligibility before starting the agent, so the status card shows the eligibility badge immediately:

```python
dos = datetime.now(timezone.utc).strftime("%Y-%m-%d")
elig = json.loads(validate_insurance(patient.get("insurance_id", "INS-999"), name, dos))
card["is_eligible"] = elig.get("is_eligible", False)
card["plan_name"]   = elig.get("plan_name", "")
```

This is a direct tool function call (not through the agent). The eligibility result appears in the dashboard card instantly, before the full agent run even starts.

### Dashboard Cards

```
INTAKE_STARTING:{patient_name}    → shows "processing" state for this patient
INTAKE_CARD:{json}                → shows final status with eligibility badge
```

The `INTAKE_CARD` JSON includes `is_eligible` and `pdf_key`. The dashboard uses `is_eligible` to show a green "Eligible" or red "Ineligible" badge, and `pdf_key` to build the URL for the "View PDF" button (`/intake/bulk-pdf/{key}`).

---

## Key Design Decisions Explained

### Why Does the Bulk Intake Runner Use `local-dev://` Instead of Always Uploading to Azure?

Azure Blob uploads can fail (network issues, quota limits, credentials expired). In a bulk run of 5 patients, one failed upload would break that patient's entire processing. The `local-dev://` fallback ensures bulk intake is resilient — even if Azure Blob is unavailable, the agent can still run the full workflow using the local files. The only thing lost is the Azure-hosted PDF URL in the stored record.

### Why Does the Bulk Intake Template Include One Ineligible Patient?

This is intentional. The bulk intake template (`data/bulk_intake_template.xlsx`) has 5 patients, and one of them has `insurance_id=INS-005` (Humana HMO — terminated). This demonstrates the full workflow including the ineligible branch — when you run bulk intake with the template, you see 4 green "Eligible" cards and 1 red "Ineligible" card, with a staff alert email triggered for the ineligible patient. This shows the system handles edge cases correctly.

### Why Is the Document ID Generated in `extract_document_fields` and Not Later?

The document ID is the reference ID for the entire processing chain — it's used in `store_indexed_record`, `notify_patient`, and `notify_staff_ineligible`. Generating it at extraction time means every tool in the chain uses the same ID. If it were generated later, there would be a risk of different tools generating different IDs for the same document.
