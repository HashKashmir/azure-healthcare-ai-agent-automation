# Automation 1 — New Employee Onboarding

This document explains every file, function, and decision involved in Automation 1. By the end you should understand exactly what happens when you click "Run Onboarding," how every piece of code contributes, and why each design decision was made.

---

## What Problem Does This Solve?

When a healthcare organization hires a new employee, the HR department must:

1. Look up the employee's record in the HR system
2. Determine which forms they need (a physician needs different forms than a contractor)
3. Assess whether they have any outstanding critical documents that could delay their start date
4. Generate and package all the required forms
5. Upload the package somewhere accessible
6. Email the new hire with their document checklist
7. Email the manager with the onboarding package and appropriate urgency level

For a small organization, this might take an HR coordinator 30–45 minutes per hire. For a large healthcare network onboarding dozens of people monthly, this is significant administrative overhead with compliance risk if steps are skipped or notifications are delayed.

Automation 1 does all of this automatically, in under a minute, triggered by a single employee ID.

---

## Files Involved

| File | Role |
|---|---|
| `agent/tools/onboarding_tools.py` | All 6 tool functions the agent calls |
| `agent/prompts/onboarding_prompt.txt` | System instructions that tell the agent what to do |
| `agent/agent_runner.py` | The agent loop (runs the model + dispatches tool calls) |
| `agent/bulk_onboarding_runner.py` | Handles batch processing of multiple employees |
| `mock_apis/primehr_api.py` | The fake HR system the tools talk to |
| `mock_apis/data/employees.json` | 75 synthetic employee records |
| `data/bulk_onboarding_template.xlsx` | Excel template for bulk upload |

---

## The Prompt — `agent/prompts/onboarding_prompt.txt`

Before the agent calls any tools, it reads its system prompt. This prompt is the agent's "job description." It tells the agent:

1. Call `get_employee_details(employee_id)` first
2. Call `fill_onboarding_form(employee_id)` next
3. Call `assess_onboarding_risk(pending_documents_json, start_date)` 
4. Call `store_document(employee_id, document_id, document_content_json)` — use the document_id from fill_onboarding_form
5. Call `notify_employee(...)` — pass forms_required and pending_documents from fill_onboarding_form
6. Call `notify_manager(...)` — use notification_type from assess_onboarding_risk; pass the blob_url from store_document

Without this prompt, the model might call the tools in a different order or skip steps. The prompt enforces the workflow. The key design principle: the model decides the overall strategy, but the prompt constrains the sequence so the output is reliable.

---

## Tool 1 — `get_employee_details`

### What It Does

Makes an HTTP GET request to the PrimeHR mock API to fetch the full employee record.

```python
def get_employee_details(employee_id: str) -> str:
    resp = requests.get(f"{PRIMEHR_URL}/employees/{employee_id}", timeout=5)
    resp.raise_for_status()
    return json.dumps({"success": True, "employee": resp.json()})
```

### What It Returns

A JSON string containing the full employee record:
```json
{
  "success": true,
  "employee": {
    "employee_id": "EMP-0023",
    "first_name": "Maria",
    "last_name": "Rodriguez",
    "department": "Cardiology",
    "position": "Cardiologist",
    "employment_type": "Full-Time",
    "hire_date": "2025-01-10",
    "start_date": "2025-01-24",
    "annual_salary": 342000,
    "manager_name": "James Chen",
    "manager_email": "james.chen@healthcare-ai.com",
    "onboarding_status": "In Progress",
    "documents_pending": ["Background Check Consent", "Drug Screen Authorization"]
  }
}
```

### What the Agent Does With It

The model reads this response and knows the employee's name, department, position, start date, and manager. It passes the relevant fields to subsequent tools. For example, it knows to pass `emp['start_date']` to `assess_onboarding_risk` and `emp['manager_email']` to `notify_manager`.

### Error Handling

If the PrimeHR API is not running, the function catches the `ConnectionError` and returns a JSON error instead of crashing. The model sees the error and will report it in its final response rather than attempting subsequent steps.

---

## Tool 2 — `fill_onboarding_form`

### What It Does

This is the most complex tool. It:
1. Fetches the employee's full record AND their onboarding checklist (two separate API calls)
2. Selects the appropriate forms for this employee's role
3. Builds a complete form package record
4. Returns it as a JSON string

### Role-Based Form Selection

The forms are not the same for every employee. The function uses four sets of forms:

```python
_BASE_FORMS = [
    "I-9 Employment Eligibility",
    "HIPAA Confidentiality Agreement",
    "Employee Handbook Acknowledgment",
    "Background Check Consent",
    "Emergency Contact Form",
    "IT Access Request",
]
_EMPLOYEE_FORMS = ["W-4 Federal Withholding", "AZ State Tax Form", "Direct Deposit Authorization", ...]
_CONTRACTOR_FORMS = ["W-9 Independent Contractor", "Drug Screen Authorization"]
_CLINICAL_FORMS = ["BLS/ACLS Certification Verification", "Professional License Verification"]
_PHYSICIAN_FORMS = ["DEA Registration Verification", "Malpractice Insurance Verification", "Medical Staff Credentialing Application"]
```

The selection logic:

```python
def _select_forms(employment_type: str, position: str, department: str) -> tuple:
    forms = list(_BASE_FORMS)  # everyone gets the base forms

    if "contract" in employment_type.lower() or "prn" in employment_type.lower():
        forms += _CONTRACTOR_FORMS   # contractors get W-9 instead of W-4
    else:
        forms += _EMPLOYEE_FORMS     # regular employees get W-4, tax forms, benefits enrollment

    if department.lower() in _CLINICAL_DEPARTMENTS:
        forms += _CLINICAL_FORMS     # clinical staff need BLS certification, license verification

    if any(title in position.lower() for title in _PHYSICIAN_TITLES):
        forms += _PHYSICIAN_FORMS    # physicians additionally need DEA + malpractice verification
```

This covers the real-world complexity of healthcare hiring: a contract ER nurse needs different forms than a full-time cardiologist, who needs different forms than a full-time medical assistant.

### What It Returns

```json
{
  "success": true,
  "form_package": {
    "document_id": "FORM-A3F7B12C",
    "employee_id": "EMP-0023",
    "employee_name": "Maria Rodriguez",
    "department": "Cardiology",
    "position": "Cardiologist",
    "role_category": "Physician / Provider - Full-Time Employee",
    "forms_included": ["I-9 Employment Eligibility", "HIPAA Confidentiality Agreement", ..., "DEA Registration Verification", "Malpractice Insurance Verification"],
    "pending_documents": ["Background Check Consent", "Drug Screen Authorization"],
    "generated_at": "2025-01-15T14:32:00Z",
    "status": "ready_for_upload"
  }
}
```

The `document_id` (`FORM-A3F7B12C`) is randomly generated with `uuid.uuid4().hex[:8].upper()`. This becomes the PDF filename and the reference ID in all subsequent steps.

---

## Tool 3 — `assess_onboarding_risk`

### What It Does

Evaluates the compliance risk of this onboarding based on:
- Which documents are still pending
- How many days until the employee's start date

### Risk Logic

```python
critical_docs = ["Background Check", "I-9", "Drug Screen"]
missing_critical = [doc for doc in critical_docs if any(doc.lower() in p.lower() for p in pending)]

if missing_critical and 0 <= days_until_start <= 3:
    risk_level = "critical"
    notification_type = "critical"
    alert_message = f"START DATE IN {days_until_start} DAY(S) - Missing critical documents: ..."
elif missing_critical:
    risk_level = "warning"
    notification_type = "conditional"
    alert_message = "Hire is conditional - pending critical documents: ..."
elif pending:
    risk_level = "low"
    notification_type = "standard"
elif not pending:
    risk_level = "clear"
    notification_type = "standard"
```

The three critical document types (`Background Check`, `I-9`, and `Drug Screen`) are legally required before most healthcare employees can start work. If they're missing with 3 days or fewer until the start date, this is an emergency — someone needs to act immediately.

### What the Agent Does With the Result

The `notification_type` from this tool is passed directly to `notify_manager`. This determines what subject line and urgency banner the manager email gets:
- `"standard"` → "New Hire Onboarding Ready"
- `"conditional"` → "Pending Documents Required" (orange banner)
- `"critical"` → "URGENT — Documents Missing, Start Date Approaching" (red banner)

---

## Tool 4 — `store_document`

### What It Does

1. Parses the form package JSON
2. Generates a professional PDF using `fpdf2`
3. Uploads the PDF to Azure Blob Storage
4. Generates a 7-day SAS URL
5. Also saves a local copy so the dashboard "View PDF" button works

### PDF Generation — `_generate_onboarding_pdf(record: dict)`

The PDF generator uses `fpdf2`, a pure-Python PDF library. It creates a multi-page document with:

- **Blue header bar** with "Healthcare AI" and "Employee Onboarding Package"
- **Employee Information table** — ID, name, department, position, role category, employment type, start date, hire date, pay frequency, manager name, manager email
- **Required Forms list** — numbered list of all forms this employee needs to complete
- **Pending Documents section** — only shown if there are outstanding items; displayed with an orange "Action Required" header
- **Footer** — generation timestamp and document ID

The `_safe()` helper encodes all string values to Latin-1 (fpdf2's Helvetica font only supports Latin-1 characters). This prevents crashes if names contain special characters.

```python
def _safe(value) -> str:
    return str(value or "").encode("latin-1", errors="replace").decode("latin-1")
```

### Azure Blob Upload

```python
svc = BlobServiceClient.from_connection_string(conn_str)
svc.create_container("onboarding-docs")  # safe to call if already exists
blob_client = svc.get_blob_client(container="onboarding-docs", blob=f"onboarding/{employee_id}/{document_id}.pdf")
blob_client.upload_blob(pdf_bytes, overwrite=True, content_settings=ContentSettings(content_type="application/pdf"))
```

The blob name follows the path `onboarding/{employee_id}/{document_id}.pdf`, so all a specific employee's documents are grouped together in "virtual folders" within the container.

### SAS URL Generation

```python
sas_token = generate_blob_sas(
    account_name=account,
    container_name="onboarding-docs",
    blob_name=blob_name,
    account_key=account_key,
    permission=BlobSasPermissions(read=True),  # read-only
    expiry=datetime.now(timezone.utc) + timedelta(days=7),
)
sas_url = f"{blob_client.url}?{sas_token}"
```

The SAS URL is what gets put into the manager's email as the "View PDF" link. After 7 days, the link expires. The underlying blob file remains in Azure Blob Storage indefinitely.

### Local Copy

Regardless of whether the Azure upload succeeds, the PDF is also saved to `data/onboarding_docs/{employee_id}_{document_id}.pdf`. The dashboard's `/onboarding/pdf/{employee_id}` endpoint serves this file when the user clicks "View PDF" in the dashboard, avoiding the need for a live Azure SAS URL when using the local dashboard.

---

## Tool 5 — `notify_employee`

### What It Does

Sends a welcome email to the new hire with:
- A personalized greeting
- Their start date
- A complete table of all required forms they must submit
- A highlighted "Action Required" section if documents are still pending
- Contact information for HR

### Email Override

```python
override = os.getenv("EMPLOYEE_NOTIFICATION_EMAIL", "")
if override:
    employee_email = override
```

In a real deployment, this would use the employee's actual email from the HR record. In this project, the `.env` file sets `EMPLOYEE_NOTIFICATION_EMAIL=hashimchughtai@gmail.com`, so all test emails go to the same inbox regardless of which employee is being onboarded.

### HTML Email Construction

The email body is a complete HTML document with inline CSS built as a Python f-string. It uses a blue header matching the Healthcare AI brand color (`#1565C0`), a table for employee details, and a highlighted orange box for pending documents (if any). This level of formatting is typical in professional HR notification emails.

### Azure Communication Services Send

```python
client = EmailClient.from_connection_string(conn_str)
message = {
    "senderAddress": sender,
    "recipients": {"to": [{"address": employee_email}]},
    "content": {"subject": subject, "html": body_html},
}
poller = client.begin_send(message)
result = poller.result()
```

`begin_send` starts the send operation asynchronously. `poller.result()` blocks until ACS confirms receipt. The returned `result` contains a message ID.

---

## Tool 6 — `notify_manager`

### What It Does

Sends an email to the employee's manager with:
- Employee details and start date
- A "View PDF" link (the SAS URL from `store_document`)
- An urgency banner determined by `notification_type`

### Three Urgency Levels

**Standard** (no urgent issues):
- Subject: "New Hire Onboarding Ready — Maria Rodriguez (EMP-0023)"
- No urgency banner, clean informational layout

**Conditional** (critical documents pending, plenty of time):
- Subject: "New Hire Onboarding — Pending Documents Required — Maria Rodriguez (EMP-0023)"
- Orange banner: "CONDITIONAL HIRE — Documents Pending. This hire is contingent on completion of outstanding documents."

**Critical** (critical documents missing, start date within 3 days):
- Subject: "URGENT — Onboarding Documents Missing, Start Date Approaching — Maria Rodriguez (EMP-0023)"
- Red banner: "URGENT ACTION REQUIRED. This employee's start date is within 3 days and critical onboarding documents are still missing."

This escalating urgency system ensures managers get appropriately alarmed when action is needed — not just a routine notification that they might ignore.

---

## The Complete Flow — Single Employee

```
User types EMP-0023 and clicks Run
        │
        ▼
dashboard.py → starts agent_runner subprocess
        │
        ▼
agent_runner.py → sends task to o4-mini:
"Process the full onboarding workflow for employee EMP-0023..."
        │
Model responds: call get_employee_details("EMP-0023")
        │
        ▼
get_employee_details → GET http://localhost:8001/employees/EMP-0023
        └── returns: full employee record (name, dept, position, dates, manager, pending docs)
        │
Model responds: call fill_onboarding_form("EMP-0023")
        │
        ▼
fill_onboarding_form → GET /employees/EMP-0023 + GET /employees/EMP-0023/onboarding
        └── selects forms based on employment_type + position + department
        └── returns: form_package with document_id, forms_included, pending_documents
        │
Model responds: call assess_onboarding_risk(pending_documents_json, start_date)
        │
        ▼
assess_onboarding_risk → checks missing critical docs, days until start
        └── returns: risk_level, notification_type, alert_message
        │
Model responds: call store_document("EMP-0023", "FORM-A3F7B12C", form_package_json)
        │
        ▼
store_document
        ├── generates PDF with fpdf2
        ├── uploads to Azure Blob Storage (onboarding-docs container)
        ├── generates 7-day SAS URL
        ├── saves local copy to data/onboarding_docs/
        └── returns: sas_url, blob_name, confirmation
        │
Model responds: call notify_employee(email, name, id, start_date, forms_json, pending_json)
        │
        ▼
notify_employee → Azure Communication Services
        └── sends welcome email with form checklist
        │
Model responds: call notify_manager(email, name, id, start_date, document_url, notification_type)
        │
        ▼
notify_manager → Azure Communication Services
        └── sends manager alert with PDF link and urgency level
        │
Model responds: finish_reason="stop" — writes final text summary
        │
        ▼
dashboard streams final text to browser
Status card shows: ✓ SUCCESS + risk level badge + View PDF button
```

---

## Bulk Processing — `agent/bulk_onboarding_runner.py`

### How It Works

When you upload an Excel file to the dashboard (the bulk template has columns `employee_id, first_name, last_name, department`), the dashboard parses the employee IDs and passes them to the bulk runner as a comma-separated list.

The bulk runner:
1. Splits into batches of 3 (to avoid overwhelming the model API)
2. For each batch, starts one thread per employee (up to 3 simultaneous threads)
3. Staggers thread starts by 2 seconds to spread out API calls
4. Each thread runs the full agent for its employee
5. After all threads in a batch complete, the next batch starts

### Dashboard Communication

The bulk runner emits structured lines that the dashboard's JavaScript parses:

```
ONBOARDING_STARTING:EMP-0023         → dashboard shows a "processing" spinner for EMP-0023
ONBOARDING_CARD:{"employee_id":"EMP-0023","employee_name":"Maria Rodriguez","status":"success","risk_level":"warning"}
                                       → dashboard renders the final status card
```

This lets the dashboard show a live status board where each employee's card updates as their individual agent run completes, rather than waiting for all 5 to finish before showing anything.

### Risk Level Extraction

After the agent run completes for each employee, the bulk runner reads back the last 100 lines of the audit log to find the `assess_onboarding_risk` output for this employee and extract the `risk_level` and `notification_type`:

```python
def _extract_risk_from_audit(emp_id: str) -> tuple:
    with open(log_path) as f:
        lines = f.readlines()[-100:]
    for line in reversed(lines):
        entry = json.loads(line)
        if entry.get("entity_id") == emp_id and entry.get("action") == "assess_onboarding_risk":
            out = json.loads(entry["output_summary"])
            return out.get("risk_level"), out.get("notification_type")
```

This is then included in the `ONBOARDING_CARD` data so the dashboard can color-code each card (green for clear, yellow for warning, red for critical).

---

## The PrimeHR Mock API — `mock_apis/primehr_api.py`

### Purpose

The PrimeHR API simulates the HR information system that a real healthcare organization would have. In a real deployment, `get_employee_details` would call a commercial HRIS like Workday, ADP, or Oracle HCM. In this project, it calls this mock FastAPI server instead.

### Endpoints Used

| Endpoint | Called By | What It Returns |
|---|---|---|
| `GET /employees/{id}` | `get_employee_details`, `fill_onboarding_form` | Full employee record |
| `GET /employees/{id}/onboarding` | `fill_onboarding_form` | Onboarding checklist with completed/pending documents |

### Data Loading

On startup, the API loads `mock_apis/data/employees.json` into memory:

```python
_employees: list[dict] = _load_employees()
_by_id: dict[str, dict] = {e["employee_id"]: e for c in _employees}
```

All requests are served from this in-memory dictionary. No database is needed. Each server restart loads fresh data from the JSON file.

### The Onboarding Endpoint

`GET /employees/{id}/onboarding` returns a formatted onboarding checklist derived from the employee's `documents_pending` field:

```json
{
  "employee_id": "EMP-0023",
  "onboarding_status": "In Progress",
  "checklist": {
    "completed_documents": ["I-9 Employment Eligibility", "HIPAA Confidentiality Agreement", ...],
    "pending_documents": ["Background Check Consent", "Drug Screen Authorization"]
  }
}
```

The `completed_documents` is computed as all standard onboarding docs minus the ones in `documents_pending`.

---

## Key Design Decisions Explained

### Why Does the Agent Call Two Separate Tools (`get_employee_details` AND `fill_onboarding_form`) Instead of One?

Each tool has a single, clear responsibility. `get_employee_details` is about fetching raw data. `fill_onboarding_form` is about intelligence — computing which forms to select based on that data. Keeping them separate means:

1. The audit log clearly shows both steps happened
2. The model can read the raw employee data before the form package is built, which gives it context for the risk assessment
3. Each tool can be called independently if needed

### Why Generate the PDF in Python (fpdf2) Instead of Using a Template Service?

fpdf2 is a dependency-free Python library that produces a PDF entirely in memory without any network calls. This is fast, reliable, and works without any external service. For a portfolio project, it also demonstrates the ability to programmatically generate documents rather than just calling a third-party API.

### Why Save a Local Copy of the PDF in Addition to Uploading to Azure?

The dashboard's "View PDF" button serves the file directly from the local `data/onboarding_docs/` directory. This means the PDF is immediately viewable in the browser without requiring a live Azure connection or a valid (non-expired) SAS URL. The Azure upload is for the manager's email link — the local copy is for the dashboard.
