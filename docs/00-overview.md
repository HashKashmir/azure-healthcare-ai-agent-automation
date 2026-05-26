# Healthcare AI Agent — Project Overview

## What Is This Project?

Healthcare AI is a software platform that uses artificial intelligence to eliminate manual administrative work at a fictional healthcare organization. Instead of a human employee sitting down and processing each HR form, each patient intake document, or each billing exception one by one, an AI agent reads a request, figures out what to do, executes a series of steps automatically, and notifies the right people when the work is done.

The platform has four independent automations, each targeting a different administrative bottleneck:

| # | Name | What It Replaces |
|---|------|-----------------|
| 1 | New Employee Onboarding | HR staff manually collecting and packaging onboarding forms, then emailing managers |
| 2 | Patient Intake & Document Capture | Front desk staff manually extracting data from intake PDFs and checking insurance |
| 3 | Billing Workflow Triage | Billing staff manually reviewing claim rejections and deciding whether to resubmit or escalate |
| 4 | Weekly Report & Admin Email | Analysts manually pulling data, building charts, writing summaries, and distributing reports |

Everything in this project uses 100% synthetic data — no real patient or employee information is used anywhere.

---

## What Does "AI Agent" Actually Mean Here?

The word "agent" is used a lot in AI, but here it has a specific meaning.

A traditional computer program follows a fixed script: Step 1, Step 2, Step 3, done. There is no decision-making — the programmer wrote every branch in advance.

An AI agent is different. You give it a **goal** in plain English, a **list of tools** it can use (functions it can call), and then it reasons about what to do. It decides which tools to call, in what order, with what arguments, based on what it learns at each step. If it gets back an unexpected result, it adapts. This project uses the OpenAI/Azure Chat Completions API with **function calling** as the mechanism that makes this work.

### How Function Calling Works (Step by Step)

1. The agent receives a system prompt (instructions) and a user message (the task).
2. It sends that to the Azure OpenAI model (o4-mini), along with a list of all the tools available to it.
3. The model reads everything and decides: *"I should call tool X with these arguments."*
4. The model returns a `tool_calls` response — not text, but a machine-readable instruction saying "call this function with these inputs."
5. Our code executes that Python function and gets back a result (as a JSON string).
6. That result is sent back to the model as context.
7. The model reads the result and decides: *"Now I should call tool Y"* — or *"I'm done, here is my final answer."*
8. This loop continues until the model produces a plain text response with `finish_reason = "stop"`.

The model never executes code itself. It only reads text and decides what to call next. Our Python code does the actual work.

---

## Azure AI Foundry and o4-mini

**Azure AI Foundry** is Microsoft's platform for deploying and hosting AI models in the Azure cloud. Think of it like a managed service that gives you a URL you can send API requests to in order to talk to an AI model — similar to how you'd use any other web API, but the service on the other end is an LLM (Large Language Model).

**o4-mini** is the specific model deployed in this project. It belongs to OpenAI's GPT-4o family (the "o" stands for Omni, meaning it handles multiple types of inputs). The "mini" version is designed to be fast and economical while still being highly capable at reasoning and function calling.

The model is accessed through the standard OpenAI Python SDK (`openai` library), but pointed at an Azure endpoint instead of OpenAI's servers. This is why the `.env` file has `AZURE_OPENAI_ENDPOINT` — the SDK is talking to Azure, not OpenAI directly.

---

## Full Architecture

```
                    ┌──────────────────────────────────────────┐
                    │           Azure AI Foundry               │
                    │         o4-mini (GPT-4o family)          │
                    │   Chat Completions API + function calling │
                    └─────────────────┬────────────────────────┘
                                      │ decides which tools to call
              ┌───────────────────────┼────────────────┬─────────────────────┐
              ▼                       ▼                ▼                     ▼
   ┌─────────────────┐   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
   │ Automation 1    │   │ Automation 2    │  │ Automation 3    │  │ Automation 4    │
   │ Onboarding      │   │ Patient Intake  │  │ Billing Triage  │  │ Weekly Reports  │
   └────────┬────────┘   └───────┬─────────┘  └────────┬────────┘  └────────┬────────┘
            │                   │                      │                     │
   PrimeHR  │            Azure Doc                NLM ICD-10            pandas +
   Mock API │           Intelligence               Public API           matplotlib
   (port    │           + Blob Storage                │                     │
   8001)    │                   │                ClaimBridge               AI
            │                   │                Mock API              analyzes
            │                   │                (port 8002)           metrics
            │                   │                      │                     │
            └───────────────────┴──────────────────────┴─────────────────────┘
                                              │
                                   Azure Blob Storage
                               (PDFs, reports, data files)
                                              │
                                   Azure Table Storage
                                    (audit log — all runs)
                                              │
                                 Azure Communication Services
                                    (all email notifications)
```

---

## The Four Automations — What Each One Does

### Automation 1 — New Employee Onboarding

**The problem:** When a new employee is hired, someone in HR has to look up their record, figure out which forms they need based on their role, generate a document package, upload it somewhere, and then email both the employee and their manager. For each new hire. Every time.

**What the agent does:**
1. Fetches the employee's full HR record from the PrimeHR system (name, department, position, manager, start date, pending documents)
2. Selects the right set of forms based on role — a physician needs DEA registration and malpractice verification; a contractor needs a W-9 instead of a W-4; a clinical staff member needs BLS certification verification
3. Assesses compliance risk — if critical documents are missing and the start date is within 3 days, the notification type escalates from "standard" to "critical"
4. Generates a professional PDF onboarding package
5. Uploads the PDF to Azure Blob Storage with a 7-day access link
6. Emails the employee their welcome message and document checklist
7. Emails the manager with the PDF link and the appropriate urgency level

Supports single employee runs and bulk processing of up to 5+ employees simultaneously via Excel upload.

### Automation 2 — Patient Intake & Document Capture

**The problem:** When a patient submits an intake form, someone has to read the PDF, manually enter the data into a system, check whether their insurance is valid, and then deal with any eligibility issues.

**What the agent does:**
1. Receives a URL pointing to a patient intake PDF in Azure Blob Storage
2. Calls Azure Document Intelligence to extract all structured fields from the PDF (name, date of birth, insurance ID, provider, chief complaint, etc.)
3. Checks insurance eligibility against a mock payer database
4. Stores the structured patient record as indexed JSON back to Azure Blob Storage
5. Emails the patient a confirmation that their intake was received and their insurance status
6. If the patient is ineligible, also emails the front desk staff an alert requiring immediate follow-up

Supports single patient runs and bulk processing of 5+ patients from an Excel upload.

### Automation 3 — Billing Workflow Triage

**The problem:** Insurance companies reject and deny claims for all kinds of reasons. Each rejected claim has to be reviewed: Is the ICD-10 code valid? Was prior authorization missing? Can this be resubmitted automatically? Or does it need a specialist's attention? This review, per claim, takes a trained billing person 15–20 minutes.

**What the agent does:**
1. Fetches the active billing exception queue from the ClaimBridge system (top 5 priority claims)
2. For each claim:
   - Fetches the full claim details
   - Validates the ICD-10 diagnosis code against the NLM (National Library of Medicine) public API
   - Classifies the rejection reason: can it be auto-resolved, or does it need human review?
   - Auto-resubmits resolvable claims (missing prior auth, wrong modifier, invalid ICD-10, duplicate)
   - Routes complex claims to the appropriate billing staff member with a priority flag
   - Emails the staff member an alert for their assigned claim
   - Records the outcome on the live dashboard
3. Generates an HTML triage report showing dollars recovered vs. dollars still at risk
4. Emails the billing manager a summary with a link to the full report

### Automation 4 — Weekly Report & Admin Email

**The problem:** Each week, someone has to pull operational data, calculate trends, write an analysis, build charts, put them together into a formatted report, and email it to the right people. This typically takes hours.

**What the agent does:**
1. Loads the weekly metrics CSV (12 weeks × 10 departments of financial, clinical, or billing data)
2. Pre-computes all the statistical signals in Python (week-over-week changes, department rankings, threshold breaches)
3. Sends the pre-computed signals to o4-mini with a role-specific prompt ("act as a CFO-level analyst...") and asks it to interpret the data, prioritize findings, and write recommendations
4. Generates three charts using matplotlib (bar and line charts matched to the report mode)
5. Assembles a professional HTML report with the AI insights and embedded charts
6. Uploads the report to Azure Blob Storage
7. Emails the report to the admin distribution list with charts embedded inline

Supports financial, clinical, and billing report modes, plus a combined mode that runs all three.

---

## Project File Structure

```
Healthcare-AI/
├── README.md                          ← Project readme
├── requirements.txt                   ← Python dependencies
├── .env                               ← Your real credentials (never committed)
├── .env.example                       ← Safe credential template (committed)
├── .gitignore                         ← Files excluded from git
├── dashboard.py                       ← FastAPI web server — the main UI
├── demo.py                            ← CLI script that runs all 4 automations in sequence
├── Start Dashboard.bat                ← Windows one-click launcher
│
├── agent/
│   ├── agent_runner.py                ← Core agent loop (model + tool orchestration)
│   ├── bulk_onboarding_runner.py      ← Parallel batch processor for bulk onboarding
│   ├── bulk_intake_runner.py          ← Parallel batch processor for bulk intake
│   └── tools/
│       ├── onboarding_tools.py        ← 6 tool functions for Automation 1
│       ├── intake_tools.py            ← 5 tool functions for Automation 2
│       ├── billing_tools.py           ← 10 tool functions for Automation 3
│       └── reporting_tools.py         ← 5 tool functions for Automation 4
│   └── prompts/
│       ├── onboarding_prompt.txt      ← System instructions for the onboarding agent
│       ├── intake_prompt.txt          ← System instructions for the intake agent
│       ├── billing_classifier_prompt.txt ← System instructions for the billing agent
│       └── report_analysis_prompt.txt ← System instructions for the reporting agent
│
├── mock_apis/
│   ├── primehr_api.py                 ← Fake HR system REST API (port 8001)
│   ├── claimbridge_api.py             ← Fake billing system REST API (port 8002)
│   ├── generate_data.py               ← Creates employees.json, claims.csv, weekly_metrics.csv
│   └── data/
│       ├── employees.json             ← 75 synthetic employee records
│       └── claims.csv                 ← 300 synthetic billing claims
│
├── data/
│   ├── bulk_onboarding_template.xlsx  ← Excel template for bulk onboarding upload
│   ├── bulk_intake_template.xlsx      ← Excel template for bulk intake upload
│   └── sample_intake.pdf              ← Pre-generated synthetic patient intake PDF
│
├── scripts/
│   └── generate_intake_pdf.py         ← Generates a patient PDF + uploads to Azure Blob
│
├── reporting/
│   ├── chart_generator.py             ← matplotlib chart rendering (3 charts per mode)
│   ├── report_builder.py              ← HTML report assembly with embedded charts
│   ├── report_combiner.py             ← Combines financial + clinical + billing into one tabbed report
│   ├── email_sender.py                ← Azure Communication Services email delivery
│   └── templates/                     ← HTML report templates
│
├── audit/
│   └── audit_logger.py                ← Writes every tool call to Azure Table Storage + local JSONL
│
└── docs/                              ← This documentation folder
```

---

## Technology Stack

| Technology | What It Is | How It's Used Here |
|---|---|---|
| **Azure AI Foundry** | Microsoft's AI model hosting platform | Hosts the o4-mini model; provides the API endpoint |
| **o4-mini** | OpenAI GPT-4o family model | The AI brain — reads tool results and decides what to do next |
| **Azure OpenAI API** | REST API for talking to the model | The Python `openai` SDK sends requests here |
| **Azure Document Intelligence** | OCR + form extraction service | Reads patient intake PDFs and extracts structured fields |
| **Azure Blob Storage** | Microsoft's cloud file storage | Stores onboarding PDFs, intake PDFs, reports |
| **Azure Table Storage** | Microsoft's cloud key-value database | Stores the audit log (every tool call, timestamped) |
| **Azure Communication Services** | Microsoft's email/SMS service | Sends all emails — welcome messages, alerts, reports |
| **FastAPI** | Python web framework | Powers the dashboard UI and the mock APIs |
| **pandas** | Python data analysis library | Reads and computes weekly metrics from CSV |
| **matplotlib** | Python chart library | Generates PNG bar/line charts for reports |
| **fpdf2** | Python PDF generation library | Builds onboarding and intake PDFs |
| **openpyxl** | Python Excel library | Parses bulk upload Excel files |
| **Faker** | Python fake data library | Generated all synthetic employee and claims data |
| **uvicorn** | Python ASGI server | Runs the FastAPI applications |

---

## What Happens When You Click "Run"

Here is the complete end-to-end flow for Automation 1 as a concrete example:

1. You open the dashboard at `http://localhost:8000`
2. You type an employee ID (e.g., `EMP-0023`) and click **Run Onboarding**
3. The browser calls `GET /run/onboarding?employee_id=EMP-0023` on the FastAPI server
4. The server starts a subprocess running `agent.agent_runner --automation onboarding --employee-id EMP-0023`
5. The subprocess creates a `HealthcareAIAgent` instance, which initializes an OpenAI client pointed at Azure
6. The agent sends the task message and the list of 6 available tool schemas to o4-mini
7. o4-mini responds: *"Call `get_employee_details` with `employee_id='EMP-0023'`"*
8. Our code calls `get_employee_details('EMP-0023')` → makes an HTTP request to the PrimeHR mock API → gets back the employee record
9. That result goes back to o4-mini
10. o4-mini responds: *"Call `fill_onboarding_form` with `employee_id='EMP-0023'`"*
11. Our code calls `fill_onboarding_form('EMP-0023')` → fetches the onboarding checklist → selects appropriate forms → returns the form package
12. This continues through `assess_onboarding_risk`, `store_document` (which generates and uploads the PDF), `notify_employee`, and `notify_manager`
13. Each tool call is also written to the audit log (Azure Table Storage + local JSONL)
14. The subprocess prints each line to stdout; the dashboard streams it to the browser via Server-Sent Events (SSE)
15. When the agent finishes, it prints `__DONE__` and the dashboard shows the completion status

---

## Read Next

- [Agent Engine — How the Core Loop Works](01-agent-engine.md)
- [Azure Services — Every Service Explained](02-azure-services.md)
- [Automation 1 — New Employee Onboarding](03-automation-1-onboarding.md)
- [Automation 2 — Patient Intake & Document Capture](04-automation-2-intake.md)
- [Automation 3 — Billing Workflow Triage](05-automation-3-billing.md)
- [Automation 4 — Weekly Report & Admin Email](06-automation-4-reporting.md)
- [Mock APIs & Synthetic Data](07-mock-apis-and-data.md)
