# Azure Services — Every Service Explained

This document explains every Azure service this project uses: what it is, why it exists, how the project connects to it, and what would happen without it.

---

## Azure AI Foundry

### What Is It?

Azure AI Foundry (formerly Azure Machine Learning + Azure AI Studio combined) is Microsoft's platform for deploying, hosting, and accessing AI models. Think of it like AWS but specifically for AI — you go to the Azure portal, deploy a model (in this case, o4-mini), and Azure gives you a URL and an API key. You send requests to that URL and the model responds.

### Why Use Azure Instead of OpenAI Directly?

In a real healthcare environment, this matters enormously. Data sent to Azure OpenAI stays within the Microsoft Azure compliance boundary — it doesn't leave Microsoft's infrastructure. Azure has signed HIPAA Business Associate Agreements (BAAs) with healthcare organizations. OpenAI's public API does not offer the same compliance guarantees. Using Azure means patient data (even synthetic data in this project) never touches an external third-party server outside of Microsoft's controlled environment.

### How This Project Connects

The connection details live in `.env`:

```
PROJECT_ENDPOINT=https://healthcare-admin-agent-resource.services.ai.azure.com/api/projects/healthcare-admin-agent
AZURE_OPENAI_ENDPOINT=https://healthcare-admin-agent-resource.openai.azure.com/openai/v1
AZURE_API_KEY=...
MODEL_DEPLOYMENT_NAME=o4-mini
```

The agent runner connects like this:

```python
from openai import OpenAI
self.client = OpenAI(
    base_url=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_API_KEY"),
)
```

The standard OpenAI Python SDK is used, but `base_url` points to Azure instead of `api.openai.com`. Azure's endpoint exposes the same API shape as OpenAI's, so the SDK works without modification.

### What the Model Does

o4-mini (part of the GPT-4o family) reads your messages and tool definitions, decides which tool to call next, and eventually produces a final text answer. It does not have internet access, does not store state between runs, and does not execute code — it only reads text and produces text (or tool-call decisions). All the actual work (HTTP requests, file I/O, PDF generation) is done by our Python code after the model tells us what to do.

---

## Azure OpenAI — Chat Completions API with Function Calling

### What Is the Chat Completions API?

This is the API endpoint that actually powers the AI. You send it a list of messages (your conversation history) plus a list of tools (functions the model can call), and it responds with either:
- A `tool_calls` response — the model wants to call one or more functions
- A `stop` response — the model is done and has a final text answer

### What Is Function Calling?

Function calling is a specific capability of GPT-4o family models that lets them request that a specific Python function be called, with specific arguments, in a structured (machine-readable) JSON format.

Without function calling, you would have to ask the model to "write JSON that describes what to do" and then parse it yourself — unreliable and fragile. With function calling, the model's tool-call responses are guaranteed to be valid JSON that matches the schema you provided. This makes agent pipelines reliable.

The model sees each tool as:
```json
{
  "type": "function",
  "function": {
    "name": "classify_claim",
    "description": "Classify a billing claim exception and determine whether it can be auto-resolved.",
    "parameters": {
      "type": "object",
      "properties": {
        "claim_id":         {"type": "string"},
        "rejection_reason": {"type": "string"},
        "icd10_valid":      {"type": "boolean"}
      },
      "required": ["claim_id", "rejection_reason", "icd10_valid"]
    }
  }
}
```

This schema is generated automatically from the Python function signature by `_build_tool_schema` in `agent_runner.py`.

### Rate Limits

Azure OpenAI imposes rate limits on how many requests you can make per minute (typically 50–100 requests per minute for o4-mini, depending on your quota). This is why the bulk runners use batching and staggering — to avoid overwhelming the model API with too many parallel requests.

---

## Azure Document Intelligence

### What Is It?

Azure Document Intelligence (formerly called Azure Form Recognizer) is an AI service that reads PDFs and images and extracts structured data from them. You give it a PDF, and it gives you back a list of key-value pairs — the field labels and their values from the form.

For example, if you give it a patient intake form PDF that contains:
```
Patient Last Name: Johnson
Date of Birth: 1975-04-22
Insurance ID: INS-003
```

Document Intelligence returns:
```json
{
  "Patient Last Name": "Johnson",
  "Date of Birth": "1975-04-22",
  "Insurance ID": "INS-003"
}
```

This is OCR (Optical Character Recognition) combined with form understanding. OCR reads pixels and recognizes text. Form understanding maps those text fragments to their correct field labels, even when the layout is complex.

### How This Project Uses It

In Automation 2 (Patient Intake), the `extract_document_fields` tool calls Document Intelligence:

```python
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
from azure.core.credentials import AzureKeyCredential

client = DocumentIntelligenceClient(
    endpoint=os.getenv("DOC_INTELLIGENCE_ENDPOINT"),
    credential=AzureKeyCredential(os.getenv("DOC_INTELLIGENCE_KEY")),
)
poller = client.begin_analyze_document(
    "prebuilt-layout",                          # use the pre-built layout model
    AnalyzeDocumentRequest(url_source=blob_url), # point it at the PDF in Blob Storage
)
result = poller.result()
```

The `prebuilt-layout` model is Document Intelligence's general-purpose layout analyzer. It reads the document structure and extracts key-value pairs without needing to be trained on your specific form. A more accurate approach for production would be a custom-trained model, but the prebuilt model works well for standard healthcare intake forms.

The PDF must be accessible via a URL — which is why intake PDFs are first uploaded to Azure Blob Storage and given a SAS (Shared Access Signature) URL. Document Intelligence fetches the PDF from Blob Storage, not from your local machine.

### Fallback When Document Intelligence Isn't Available

If Document Intelligence credentials aren't configured, or if the API returns zero fields, the tool falls back to reading from a local JSON file (`data/intake_patient_data.json`) that was written when the PDF was generated. This means the project works even without Document Intelligence credentials — you just don't get real OCR.

```python
if doc_endpoint and doc_key:
    # try Document Intelligence...
    if fields:
        return json.dumps({...fields...})
    # zero fields returned — fall through

# Fallback: read from local patient data file
with open(patient_data_path) as f:
    p = json.load(f)
mock_fields = {
    "Patient Last Name": p.get("last_name", ""),
    ...
}
```

### Connection Details

```
DOC_INTELLIGENCE_ENDPOINT=https://healthcaredocintell.cognitiveservices.azure.com/
DOC_INTELLIGENCE_KEY=...
```

---

## Azure Blob Storage

### What Is It?

Azure Blob Storage is Microsoft's cloud file storage — think of it as a cloud hard drive. "Blob" stands for Binary Large OBject. You upload files (PDFs, images, JSON, HTML) to containers (like folders), and Azure gives each file a URL you can share.

### How This Project Uses It

Blob Storage is used in three places:

**1. Onboarding PDFs (`onboarding-docs` container)**

After generating a new employee's onboarding PDF, `store_document` in `onboarding_tools.py` uploads it:

```python
from azure.storage.blob import BlobServiceClient, ContentSettings, generate_blob_sas, BlobSasPermissions

svc = BlobServiceClient.from_connection_string(conn_str)
svc.create_container("onboarding-docs")  # creates if it doesn't exist
blob_client = svc.get_blob_client(container="onboarding-docs", blob=f"onboarding/{employee_id}/{document_id}.pdf")
blob_client.upload_blob(pdf_bytes, overwrite=True, content_settings=ContentSettings(content_type="application/pdf"))
```

A SAS token is then generated for 7 days so the manager's email link works without requiring the storage container to be public:

```python
sas_token = generate_blob_sas(
    account_name=account,
    container_name="onboarding-docs",
    blob_name=blob_name,
    account_key=account_key,
    permission=BlobSasPermissions(read=True),
    expiry=datetime.now(timezone.utc) + timedelta(days=7),
)
sas_url = f"https://{account}.blob.core.windows.net/onboarding-docs/{blob_name}?{sas_token}"
```

**2. Patient Intake PDFs (`intake-pdfs` container)**

The `scripts/generate_intake_pdf.py` script generates a patient intake PDF and uploads it to the `intake-pdfs` container with a 24-hour SAS URL. That URL is what the intake agent receives — it passes it to Document Intelligence, which fetches the PDF from Blob Storage.

Indexed patient records (JSON files) are also stored back to Blob Storage in `intake-pdfs/indexed/{patient_id}/` by `store_indexed_record`.

**3. Reports (`reports` container)**

Both the billing triage HTML reports (from `billing_tools.py`) and the analytics HTML reports (from `reporting_tools.py`) are uploaded to the `reports` container with 7-day SAS URLs. These URLs are included in the emails sent to managers.

**4. Weekly Metrics CSV (`data-csv` container)**

The reporting pipeline first tries to download `weekly_metrics_financial.csv` (etc.) from the `data-csv` Blob container. If unavailable, it falls back to the local `data/weekly_metrics.csv` file.

### Connection

```
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=healthcareprojectdata;AccountKey=...;EndpointSuffix=core.windows.net
AZURE_STORAGE_ACCOUNT_NAME=healthcareprojectdata
AZURE_STORAGE_CONTAINER_INTAKE=intake-pdfs
AZURE_STORAGE_CONTAINER_REPORTS=reports
AZURE_STORAGE_CONTAINER_DATA=data-csv
```

The connection string contains both the account name and the account key. The SDK uses this to authenticate all operations.

### What a SAS URL Is

A SAS (Shared Access Signature) URL is a time-limited, permission-limited URL that lets someone access a specific blob without needing your storage account credentials. A 7-day read-only SAS URL lets the manager open the PDF link in their email for 7 days. After 7 days, the link expires and the blob is no longer accessible via that URL (though it still exists in storage).

This is how you share files from private storage without making the entire container public.

### Local Fallback

If `AZURE_STORAGE_CONNECTION_STRING` is not set, every storage function falls back to saving files locally in `data/onboarding_docs/`, `data/reports/`, etc. The project works entirely offline in this mode — you just don't get cloud storage or shareable URLs.

---

## Azure Table Storage

### What Is It?

Azure Table Storage is a NoSQL key-value store built into Azure Storage accounts. It's simpler than a database — think of it like a spreadsheet where each row has a PartitionKey, RowKey, and any additional columns you want. It's extremely cheap and fast for write-heavy workloads like audit logs.

### How This Project Uses It

Every single tool call in every automation is written to Table Storage by `audit/audit_logger.py`:

```python
from azure.data.tables import TableServiceClient

svc = TableServiceClient.from_connection_string(conn_str)
svc.create_table_if_not_exists("auditlog")
tbl = svc.get_table_client("auditlog")
tbl.upsert_entity(entry)  # write or overwrite if same RowKey exists
```

The `PartitionKey` is the automation name (`"billing"`, `"onboarding"`, etc.). The `RowKey` is a UUID. This structure lets you efficiently query all entries for a specific automation.

### What Uses the Same Connection String

Table Storage uses the same `AZURE_STORAGE_CONNECTION_STRING` as Blob Storage. Both services live on the same Azure Storage account.

```
AZURE_TABLE_NAME=auditlog
```

---

## Azure Communication Services

### What Is It?

Azure Communication Services (ACS) is Microsoft's cloud communications platform. This project uses the email capability, which lets you send email from a verified sender address. It's similar to SendGrid or Mailgun, but built into Azure.

### How Email Works in This Project

All four automations send emails through ACS. The email sender address (`DoNotReply@...azurecomm.net`) is a verified sender registered in the ACS resource. All recipient overrides (manager email, employee email, billing staff email) route to the same Gmail address in this project (`hashimchughtai@gmail.com`) because that's what's configured in `.env`.

In a production deployment, each email address in `.env` would be different:
- `MANAGER_NOTIFICATION_EMAIL` → actual manager's email
- `EMPLOYEE_NOTIFICATION_EMAIL` → actual new hire's email
- `BILLING_STAFF_EMAIL` → actual billing department
- `ADMIN_EMAIL_RECIPIENTS` → actual admin distribution list

The ACS email SDK works with a polling pattern:

```python
from azure.communication.email import EmailClient

client = EmailClient.from_connection_string(conn_str)
poller = client.begin_send({
    "senderAddress": sender,
    "recipients": {"to": [{"address": recipient}]},
    "content": {"subject": subject, "html": html_body},
})
poller.result()  # blocks until the email is accepted (not necessarily delivered)
```

`poller.result()` blocks the thread until ACS confirms the message was accepted for delivery. It does not guarantee delivery to the inbox — just that ACS received it successfully.

### Dev Mode Fallback

If `AZURE_COMMS_CONNECTION_STRING` is not set, all email functions skip the actual send and print the email details to the console instead:

```python
if conn_str:
    # send via Azure
else:
    print(f"\n[DEV] Email to {recipient}\nSubject: {subject}\n")
    return json.dumps({"success": True, "note": "Dev mode - email printed, not sent."})
```

This means you can run the entire project and see what would be emailed without having an ACS account configured.

### Report Email — Inline Image Handling

The reporting email is special. The HTML report has chart images embedded as base64-encoded PNG data (i.e., the image data is directly inside the HTML as text, not linked from a URL). Most email clients block images loaded from external URLs, so embedding them as base64 is more reliable.

However, some email clients have trouble with very large base64 `<img>` tags directly in HTML. The `email_sender.py` module converts these to CID (Content-ID) references — a standard email attachment technique:

```python
def _extract_cid_attachments(html_body: str) -> tuple:
    # finds: src="data:image/png;base64,{huge_string}"
    # replaces with: src="cid:chart0"
    # and adds the image as an attachment with contentId="chart0"
```

The images are then sent as inline attachments alongside the HTML body. The email client assembles them back together when displaying the email.

### Connection

```
AZURE_COMMS_CONNECTION_STRING=endpoint=https://healthcare-comms.unitedstates.communication.azure.com/;accesskey=...
ADMIN_EMAIL_SENDER=DoNotReply@e4a45e2d-b2ab-4094-94af-92d6bd9ed435.azurecomm.net
```

---

## NLM Public API — The One Non-Azure Service

### What Is It?

The National Library of Medicine (NLM) provides a free public API for looking up ICD-10-CM diagnosis codes. ICD-10-CM is the coding system used in US healthcare to classify every disease, condition, and diagnosis into a standardized code (e.g., `E11.9` = Type 2 diabetes without complications).

### How This Project Uses It

In Automation 3 (Billing Triage), before classifying a claim, the agent validates the claim's ICD-10 code against the NLM API. A claim rejected for "Invalid ICD-10 code" will have its code checked — if the code is actually invalid, the agent can look up the suggested correct code and resubmit with the correction.

```python
NLM_ICD10_URL = "https://clinicaltables.nlm.nih.gov/api/icd10cm/v3/search"

resp = requests.get(
    NLM_ICD10_URL,
    params={"sf": "code,name", "terms": code, "maxList": 5},
    timeout=8,
)
data  = resp.json()
total = data[0]    # number of matches
matches = data[3]  # list of [code, description] pairs

exact = next(
    (m for m in matches if m[0].upper() == code.strip().upper()), None
)
is_valid = exact is not None
```

The API returns up to 5 matching codes sorted by relevance. The function checks whether any match is an exact match for the submitted code. If yes, it's valid. If no, it's invalid and the suggestions array contains alternatives.

This is the only API call in the project that goes to a third-party non-Azure service. Since it's a public medical reference database, this is acceptable even in HIPAA contexts — no patient data is sent, only the code string.

---

## How All Azure Services Are Connected in a Typical Run

Here is the complete picture for **one Automation 1 (Onboarding) run**, showing every Azure service touched:

```
1. Agent runner initializes
       └── Connects to: Azure AI Foundry (o4-mini) ← AZURE_OPENAI_ENDPOINT, AZURE_API_KEY

2. get_employee_details called
       └── Calls: PrimeHR mock API (port 8001) — no Azure
       └── Writes to: Azure Table Storage (audit log) ← AZURE_STORAGE_CONNECTION_STRING

3. fill_onboarding_form called
       └── Calls: PrimeHR mock API twice
       └── Writes to: Azure Table Storage (audit log)

4. assess_onboarding_risk called
       └── Pure Python computation — no Azure
       └── Writes to: Azure Table Storage (audit log)

5. store_document called
       └── Generates PDF (fpdf2) — no Azure
       └── Uploads PDF to: Azure Blob Storage (onboarding-docs container) ← AZURE_STORAGE_CONNECTION_STRING
       └── Generates SAS URL — no extra service
       └── Writes to: Azure Table Storage (audit log)

6. notify_employee called
       └── Sends email via: Azure Communication Services ← AZURE_COMMS_CONNECTION_STRING
       └── Writes to: Azure Table Storage (audit log)

7. notify_manager called
       └── Sends email via: Azure Communication Services ← AZURE_COMMS_CONNECTION_STRING
       └── Writes to: Azure Table Storage (audit log)

Total Azure API calls per run: ~8 (6 model calls + 1 blob upload + 2 emails)
```

---

## Environment Variables Quick Reference

All credentials are stored in `.env` and loaded by `python-dotenv` at the start of every tool file and the agent runner.

| Variable | Service | What Happens Without It |
|---|---|---|
| `AZURE_OPENAI_ENDPOINT` | Azure AI Foundry | Agent cannot run at all |
| `AZURE_API_KEY` | Azure AI Foundry | Agent cannot run at all |
| `MODEL_DEPLOYMENT_NAME` | Azure AI Foundry | Defaults to `o4-mini` |
| `DOC_INTELLIGENCE_ENDPOINT` | Document Intelligence | Falls back to local patient data file |
| `DOC_INTELLIGENCE_KEY` | Document Intelligence | Falls back to local patient data file |
| `AZURE_STORAGE_CONNECTION_STRING` | Blob Storage + Table Storage | Files saved locally; no audit in Azure |
| `AZURE_TABLE_NAME` | Table Storage | Defaults to `auditlog` |
| `AZURE_COMMS_CONNECTION_STRING` | Communication Services | Emails printed to console, not sent |
| `ADMIN_EMAIL_SENDER` | Communication Services | Email not sent |
| `ADMIN_EMAIL_RECIPIENTS` | Communication Services | No recipients |
| `MANAGER_NOTIFICATION_EMAIL` | Communication Services | Defaults to ADMIN_EMAIL_RECIPIENTS |
| `EMPLOYEE_NOTIFICATION_EMAIL` | Communication Services | Uses the employee's HR email |
| `BILLING_STAFF_EMAIL` | Communication Services | Defaults to ADMIN_EMAIL_RECIPIENTS |
| `INTAKE_STAFF_EMAIL` | Communication Services | Defaults to ADMIN_EMAIL_RECIPIENTS |
| `PRIMEHR_API_URL` | PrimeHR mock API | Defaults to http://localhost:8001 |
| `CLAIMBRIDGE_API_URL` | ClaimBridge mock API | Defaults to http://localhost:8002 |
