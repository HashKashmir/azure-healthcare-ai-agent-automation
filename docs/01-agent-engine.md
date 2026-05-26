# Agent Engine — How the Core Loop Works

This document explains every piece of infrastructure that runs the AI agents: the agent runner, how tools are built and registered, how the model loop works, prompts, bulk runners, the audit logger, and the dashboard that ties it all together.

---

## The Agent Runner — `agent/agent_runner.py`

This is the heart of the entire project. Every automation (onboarding, intake, billing, reporting) flows through this file.

### What It Does

The agent runner does three things:
1. Converts your Python tool functions into JSON schemas the OpenAI API understands
2. Runs the model → tool call → result loop until the model says it's done
3. Dispatches each tool call to the right Python function and routes the result back

### Imports and Tool Registry

At the top of the file, every tool function from every automation is imported:

```python
from agent.tools.onboarding_tools import (get_employee_details, fill_onboarding_form, ...)
from agent.tools.billing_tools import (get_billing_exception_queue, classify_claim, ...)
# ... and so on for intake and reporting tools
```

These are then registered in a dictionary called `TOOL_REGISTRY`:

```python
TOOL_REGISTRY: dict[str, callable] = {
    "get_employee_details": get_employee_details,
    "fill_onboarding_form": fill_onboarding_form,
    ...
}
```

This dictionary is how the runner maps the model's requested tool name (a string like `"classify_claim"`) to the actual Python function that should be called. When the model says "call classify_claim," the runner does `TOOL_REGISTRY["classify_claim"](**args)`.

### Automatic Tool Schema Generation

The OpenAI API requires tools to be described in a specific JSON schema format — it needs to know each function's name, description, and what parameters it accepts. Writing this manually for 26 tool functions would be tedious and error-prone.

Instead, the runner has a function called `_build_tool_schema` that generates these schemas automatically by reading your Python function's own signature and docstring using Python's `inspect` module:

```python
def _build_tool_schema(fn: callable) -> dict:
    sig  = inspect.signature(fn)      # reads the function's parameters
    doc  = inspect.getdoc(fn) or ""   # reads the docstring
    first_sentence = doc.split(".")[0].strip() + "."
    ...
    for name, param in sig.parameters.items():
        ann = param.annotation  # reads the type hints (str, int, bool, float)
        properties[name] = {"type": _python_type_to_json(ann)}
        if param.default is inspect.Parameter.empty:
            required.append(name)  # no default = required parameter
```

This means: if you write a clean, well-typed Python function with a good docstring, the schema is generated automatically. The first sentence of the docstring becomes the description the model uses to understand what the tool does.

### Automation Configuration

The runner knows which tools belong to which automation via `AUTOMATION_CONFIG`:

```python
AUTOMATION_CONFIG = {
    "onboarding": {
        "prompt_file": "onboarding_prompt",
        "tools": [get_employee_details, fill_onboarding_form, ...],
    },
    "billing": {
        "prompt_file": "billing_classifier_prompt",
        "tools": [get_billing_exception_queue, get_claim_details, ...],
    },
    ...
}
```

When you run `--automation billing`, the runner loads only the billing tools and the billing prompt. The model never sees the onboarding tools during a billing run — this keeps the model focused and prevents confusion.

### The `HealthcareAIAgent` Class

The main class is `HealthcareAIAgent`. It initializes once:

```python
self.client = OpenAI(
    base_url=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
    api_key=os.getenv("AZURE_API_KEY", ""),
    max_retries=5,
)
self.model = os.getenv("MODEL_DEPLOYMENT_NAME", "o4-mini")
```

Note: it uses the standard `OpenAI` client (not `AzureOpenAI`) but points `base_url` at the Azure endpoint. This is because the Azure AI Foundry endpoint format (`/openai/v1`) is compatible with the standard client, and avoids a double-path issue that the `AzureOpenAI` client would introduce with this specific endpoint structure.

### The Main Loop — `run()`

This is the most important method. Here is what it does, step by step:

```python
def run(self, automation: str, user_message: str) -> str:
    cfg   = AUTOMATION_CONFIG[automation]
    tools = [_build_tool_schema(fn) for fn in cfg["tools"]]
    instructions = _load_prompt(cfg["prompt_file"])

    messages = [
        {"role": "system", "content": instructions},
        {"role": "user",   "content": user_message},
    ]

    while iteration < 60:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        finish = response.choices[0].finish_reason

        if finish == "stop":
            return choice.message.content   # agent is done

        if finish == "tool_calls":
            messages.append(choice.message)  # add assistant's tool-call decision to thread

            for tc in choice.message.tool_calls:
                fn = TOOL_REGISTRY[tc.function.name]
                fn_args = json.loads(tc.function.arguments)
                output = fn(**fn_args)       # actually call the Python function

                write_audit_entry(...)       # log it

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": output,       # send result back to model
                })
```

Key things to understand:

- **`messages` is the entire conversation history.** Every model response, every tool call, every tool result is appended. This is how the model knows what it has already done — it reads the full thread each time.
- **`tool_choice="auto"` means the model decides** whether to call a tool or stop. You can force it to call a specific tool or prevent it from calling any, but "auto" gives the model agency to reason.
- **The loop has a max of 60 iterations.** This is a safety limit. A billing run with 5 claims might use 30–35 iterations (6–7 per claim: get_details, validate_icd10, classify, resubmit/route, notify, record_outcome). The limit prevents infinite loops if the model gets stuck.
- **Each tool call can batch.** The model can request multiple tool calls in a single response. The loop processes all of them before making the next model call.

### Audit Logging in the Loop

After every single tool call, this line runs:

```python
write_audit_entry(
    automation=automation,
    action=fn_name,
    entity_id=(fn_args.get("employee_id") or fn_args.get("claim_id") or ...),
    input_summary=json.dumps(fn_args)[:300],
    output_summary=output[:300],
)
```

Every tool call — what was called, with what arguments, what it returned — is permanently recorded. This goes to Azure Table Storage and the local `audit/audit_log.jsonl` file simultaneously.

### CLI Entry Point

The `main()` function at the bottom lets you run any automation from the command line:

```bash
python -m agent.agent_runner --automation onboarding --employee-id EMP-0023
python -m agent.agent_runner --automation billing
python -m agent.agent_runner --automation report --mode financial
```

The script builds the appropriate user message string and calls `agent.run(automation, msg)`.

---

## System Prompts — `agent/prompts/`

Each automation has a `.txt` file that is loaded as the `system` message — the instructions the model reads before it does anything. Think of it as the job description you hand to a new employee.

### What Good Prompts Do

The prompts in this project do not just say "be an onboarding agent." They specify the **exact workflow** the agent should follow, step by step. This is important because the model has the freedom to call tools in any order — without explicit instructions, it might skip steps or do things in a surprising sequence.

### `billing_classifier_prompt.txt` — The Most Detailed

The billing prompt is the best example of a well-structured agent prompt. It specifies:

- **Step 1:** Call `get_billing_exception_queue(limit=5)`. If empty, stop.
- **Step 2:** For EACH claim, run steps a through d in order:
  - a. Call `get_claim_details`
  - b. Call `validate_icd10_code` 
  - c. Call `classify_claim`
  - d. Based on classification: either call `resubmit_claim` OR call `route_to_staff` + `notify_staff_claim_routed`
  - Then: call `record_claim_outcome` with specific fields
- **Step 3:** Call `generate_billing_report`
- **Step 4:** Call `send_billing_summary_email`
- **Final:** Write a text summary

It also lists exactly which fields to pass to each tool call and why. This level of specificity produces consistent, reliable agent behavior. Without it, the model might process claims out of order or skip the email notification step.

### `onboarding_prompt.txt`

Tells the agent to: get employee details → fill the form → assess risk → store the document → notify the employee → notify the manager. Specifies that the notification type (standard, conditional, critical) should come from the risk assessment result.

### `intake_prompt.txt`

Tells the agent to: extract document fields → validate insurance → store the indexed record → notify the patient → if ineligible, also notify intake staff.

### `report_analysis_prompt.txt`

Tells the agent to: fetch data → analyze trends → generate charts → build report → send email. One per report mode (financial, clinical, billing).

---

## The Audit Logger — `audit/audit_logger.py`

Every tool call in every automation is recorded by this module. It runs inside the agent runner's loop and cannot be bypassed.

### What Gets Written

Each entry contains:
- `PartitionKey` — the automation name (used by Azure Table Storage for querying)
- `RowKey` — a UUID (unique identifier for this entry)
- `timestamp` — UTC timestamp
- `automation` — which automation this came from
- `action` — the tool function name
- `entity_id` — the employee ID, claim ID, or report mode (for context)
- `input_hash` — a SHA-256 hash of the input (first 16 chars) — proves what was sent without storing sensitive data in full
- `input_summary` — first 500 characters of the JSON arguments
- `output_summary` — first 500 characters of the JSON result
- `status` — "success" or error

### Dual Write Strategy

The logger always writes to both places:
1. **Azure Table Storage** — the production audit trail, queryable, persistent in the cloud
2. **`audit/audit_log.jsonl`** — a local file where each line is one JSON record

The JSONL file is the local fallback. If Azure is unreachable (e.g., running without internet, or credentials not configured), the audit log still works locally. The file is excluded from git via `.gitignore` since it accumulates run history with entity IDs.

### Why `input_hash` Exists

In a real HIPAA environment, you would not want to store patient names, insurance IDs, or employee SSNs in an audit log in plaintext. The `input_hash` proves that a specific input was sent to a tool (you can re-hash the original input and compare), without storing the raw value. This is a HIPAA-aligned pattern even though this project uses synthetic data.

---

## Bulk Runners — `agent/bulk_onboarding_runner.py` and `agent/bulk_intake_runner.py`

These two files handle batch processing — running the same automation for multiple employees or patients at the same time, in parallel.

### Why Parallel Processing?

Running 5 employees sequentially through the full onboarding agent would take 5 × 45–60 seconds = ~4–5 minutes. With parallel processing, all 5 run simultaneously and finish in ~60 seconds.

However, Azure OpenAI has a rate limit (roughly 50 requests per minute for o4-mini). If you fire all 5 agents instantly, they'll all try to call the model at the same time and hit rate limits. The bulk runners solve this with batching and staggering.

### How Batching and Staggering Work

```python
BATCH_SIZE  = 3
STAGGER_SEC = 2

batches = [employee_ids[i : i + BATCH_SIZE] for i in range(0, len(employee_ids), BATCH_SIZE)]

for batch in batches:
    for i, emp_id in enumerate(batch):
        if i > 0:
            time.sleep(STAGGER_SEC)   # wait 2 seconds between thread starts
        t = threading.Thread(target=_run_employee, args=(emp_id,))
        t.start()
    for t in threads:
        t.join()  # wait for all threads in this batch to finish before starting next batch
```

So for 5 employees: batch 1 runs employees 1, 2, 3 (started 2 seconds apart), waits for all three to finish, then batch 2 runs employees 4 and 5.

### Thread Safety

Multiple threads printing to stdout at the same time would produce garbled output. The bulk runners use a threading lock:

```python
_print_lock = threading.Lock()

def _safe_print(text: str) -> None:
    with _print_lock:
        print(text, flush=True)
```

Any thread that wants to print must acquire the lock first. Only one thread prints at a time, so the output remains coherent.

### The Stdout Protocol

The bulk runners communicate with the dashboard through a structured stdout protocol. Certain lines follow a specific format that the dashboard parses:

- `ONBOARDING_STARTING:{emp_id}` — tells the dashboard to show a "processing" card for this employee
- `ONBOARDING_CARD:{json}` — tells the dashboard to update the card with final status, risk level, PDF link
- `INTAKE_STARTING:{patient_name}` — same for intake
- `INTAKE_CARD:{json}` — same for intake completion

Everything else is just log output displayed in the log panel.

### Bulk Onboarding — Employee Pre-Fetch

Before starting the full agent run, the bulk onboarding runner pre-fetches the employee's name and department:

```python
raw = get_employee_details(emp_id)
emp = json.loads(raw)["employee"]
card["employee_name"] = f"{emp['first_name']} {emp['last_name']}"
card["department"]    = emp.get("department", "")
```

This fills the dashboard card immediately (name, department visible) before the full agent run starts, so the UI looks populated even while processing is in progress.

### Bulk Intake — PDF Generation Per Patient

Bulk intake is more complex because each patient needs their own PDF:

1. Write the patient's data to `data/intake_patient_{key}.json` — the intake tool reads this file when Azure Document Intelligence isn't available
2. Generate a PDF using the patient's data via `scripts/generate_intake_pdf.py`
3. Try to upload to Azure Blob Storage; if that fails, use a `local-dev://{key}` URL as fallback
4. Run the full intake agent with that URL

The `local-dev://` URL is a signal that tells the `extract_document_fields` tool to read from the local JSON file instead of sending the PDF to Azure Document Intelligence.

---

## The Dashboard — `dashboard.py`

The dashboard is a FastAPI web application that serves as the main user interface. It runs on port 8000 and is the primary way to use the project.

### Startup — Launching the Mock APIs

When the dashboard starts, it automatically launches both mock APIs as child processes:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    v = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mock_apis.primehr_api:app", "--port", "8001"],
        ...stdout=subprocess.DEVNULL...  # suppress their output
    )
    m = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mock_apis.claimbridge_api:app", "--port", "8002"],
        ...
    )
```

This means you only need to start one process (`python dashboard.py`) and all three servers come up together. When the dashboard shuts down, it terminates the mock API processes cleanly.

### Server-Sent Events (SSE) — Live Log Streaming

The most important technical feature of the dashboard is how it streams agent output to the browser in real time. This uses a standard web technology called **Server-Sent Events (SSE)**.

Here is how it works:

1. When you click "Run Onboarding," the browser opens a long-lived HTTP connection to `/run/onboarding`
2. The server starts the agent subprocess and begins reading its stdout line by line
3. For each line, the server sends it to the browser immediately as an SSE event
4. The browser's JavaScript listens for these events and appends them to the log panel

The SSE helper function:

```python
def _stream(cmd: list):
    def generate():
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                yield f"data: {json.dumps(line)}\n\n"  # SSE format: "data: " + JSON string + double newline
        proc.wait()
        yield f"data: {json.dumps('__DONE__')}\n\n"    # signals completion to the browser
    return StreamingResponse(generate(), media_type="text/event-stream", headers=SSE_HEADERS)
```

The `__DONE__` sentinel tells the browser's JavaScript to stop listening and show the completion state.

### Dashboard Endpoints

| Endpoint | Method | What It Does |
|---|---|---|
| `/` | GET | Serves the full dashboard HTML |
| `/run/onboarding` | GET | Starts onboarding agent, streams output |
| `/run/billing` | GET | Starts billing agent, streams output |
| `/run/report` | GET | Starts reporting agent (mode=financial/clinical/billing/all), streams output |
| `/run/intake` | GET | Generates intake PDF, then starts intake agent, streams output |
| `/upload/bulk-onboarding` | POST | Accepts Excel file, extracts employee IDs |
| `/run/bulk-onboarding` | GET | Starts bulk onboarding runner, streams output |
| `/upload/bulk-intake` | POST | Accepts Excel file, writes patient data JSON |
| `/run/bulk-intake` | GET | Starts bulk intake runner, streams output |
| `/onboarding/pdf/{employee_id}` | GET | Serves the onboarding PDF for a specific employee |
| `/intake/bulk-pdf/{key}` | GET | Serves a bulk intake PDF by key |
| `/billing/report` | GET | Serves the most recent billing triage HTML report |
| `/report/view` | GET | Serves the most recent analytics HTML report |
| `/audit` | GET | Returns the last 60 audit log entries as JSON |
| `/api/status` | GET | Checks whether both mock APIs are responding |
| `/bulk-onboarding/template` | GET | Downloads the bulk onboarding Excel template |
| `/bulk-intake/template` | GET | Downloads the bulk intake Excel template |

### The Dashboard HTML

The entire dashboard UI is a single HTML string embedded directly in `dashboard.py` as `DASHBOARD_HTML`. It uses inline CSS and vanilla JavaScript — no external frameworks, no build step, no separate files.

The JavaScript in the dashboard:
- Calls the SSE endpoints and appends log lines to the log panel
- Parses the `ONBOARDING_CARD:` and `INTAKE_CARD:` structured lines and renders status cards
- Parses `REPORT_INSIGHT:` lines and renders insight cards in real time
- Polls `/api/status` every few seconds to show whether the mock APIs are up (the green/orange dots in the header)
- Handles bulk upload flow: user uploads Excel → JavaScript sends it to the upload endpoint → gets back a list of IDs → renders a preview → user clicks Run → starts the bulk runner

---

## `demo.py` — Full CLI Demo

`demo.py` is a standalone script that runs all four automations in sequence from the command line, without the dashboard. It:

1. Starts both mock APIs as subprocesses
2. Waits for them to respond on their health check endpoints (with retries)
3. Runs Automation 1 (onboarding) for a single employee
4. Runs Automation 2 (intake) with a pre-existing PDF
5. Runs Automation 3 (billing)
6. Runs Automation 4 (financial report)

This is useful for a complete headless demo or for CI-style testing.

---

## `Start Dashboard.bat` — Windows Launcher

A Windows batch file that does two things:
1. Starts `python dashboard.py` in the current terminal
2. Opens `http://localhost:8000` in the default browser

This is a convenience shortcut so a non-technical user can double-click one file and the entire application comes up without touching a terminal.

---

## Summary — How All These Pieces Connect

```
User clicks "Run" in browser
    │
    ▼
dashboard.py (FastAPI) — receives HTTP request
    │
    ├── spawns subprocess: agent.agent_runner (or bulk runner)
    │       │
    │       ├── loads system prompt from prompts/*.txt
    │       ├── builds tool schemas from Python function signatures
    │       │
    │       └── LOOP:
    │               ├── sends messages + tools to Azure OpenAI (o4-mini)
    │               ├── model responds with tool_calls
    │               ├── runner calls the Python tool function
    │               ├── audit_logger writes the entry (Azure Table + local JSONL)
    │               └── result goes back to model
    │
    └── streams subprocess stdout to browser as Server-Sent Events
            │
            Browser JavaScript
            ├── appends log lines to log panel
            ├── parses structured CARD: lines → renders status cards
            └── when __DONE__ received → shows completion state
```
