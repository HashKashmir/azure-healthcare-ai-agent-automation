"""
Email sender — delivers the weekly report via Azure Communication Services.
Falls back to a console print in dev mode if the connection string is absent.
"""

import os
import re
import uuid
from datetime import datetime


def send_report(html_path: str, report_mode: str, recipients: list[str]) -> dict:
    """
    Send the compiled weekly report HTML to the recipient list.

    Args:
        html_path:    Local path to the rendered HTML report file.
        report_mode:  'financial' | 'clinical' | 'billing'
        recipients:   List of email addresses.

    Returns:
        Dict with status, message_ids, and recipient list.
    """
    mode_labels = {
        "financial": "Financial Insight Report",
        "clinical":  "Clinical Operations Report",
        "billing":   "Billing Performance Report",
    }
    week_str = datetime.now().strftime("Week of %B %d, %Y")
    label    = mode_labels.get(report_mode, "Admin Report")
    subject  = f"Healthcare AI {label} — {week_str}"

    with open(html_path, encoding="utf-8") as f:
        html_body = f.read()

    # Convert base64 inline images to CID references for email clients
    email_html, inline_attachments = _extract_cid_attachments(html_body)

    conn_str = os.getenv("AZURE_COMMS_CONNECTION_STRING", "")
    sender   = os.getenv("ADMIN_EMAIL_SENDER", "DoNotReply@healthcare-ai.com")

    if conn_str:
        return _send_via_azure(conn_str, sender, recipients, subject, email_html, inline_attachments)

    # Dev fallback
    print(f"\n[email-sender] DEV MODE — would send to: {recipients}")
    print(f"  Subject: {subject}")
    print(f"  Inline attachments: {len(inline_attachments)}")
    return {
        "status":      "dev_mode",
        "message_ids": [f"DEV-{uuid.uuid4().hex[:8]}" for _ in recipients],
        "recipients":  recipients,
        "subject":     subject,
        "note":        "Email not sent — Azure Communication Services not configured.",
    }


def _extract_cid_attachments(html_body: str) -> tuple[str, list[dict]]:
    """
    Replace data:image/png;base64,... src values with cid: references.
    Returns the modified HTML and a list of inline attachment dicts for ACS.
    """
    attachments = []
    counter = [0]

    def replacer(match):
        idx = counter[0]
        counter[0] += 1
        cid     = f"chart{idx}"
        b64data = match.group(1)
        attachments.append({
            "name":            f"chart{idx}.png",
            "contentType":     "image/png",
            "contentInBase64": b64data,
            "contentId":       cid,
        })
        return f'src="cid:{cid}"'

    pattern  = r'src="data:image/png;base64,([^"]+)"'
    new_html = re.sub(pattern, replacer, html_body)
    return new_html, attachments


def _send_via_azure(
    conn_str: str,
    sender: str,
    recipients: list[str],
    subject: str,
    html_body: str,
    inline_attachments: list[dict],
) -> dict:
    from azure.communication.email import EmailClient
    from azure.core.exceptions import ServiceRequestError, HttpResponseError

    client  = EmailClient.from_connection_string(conn_str)
    to_list = [{"address": r} for r in recipients]
    message = {
        "senderAddress": sender,
        "recipients":    {"to": to_list},
        "content":       {"subject": subject, "html": html_body},
    }
    if inline_attachments:
        message["attachments"] = inline_attachments

    try:
        poller = client.begin_send(message)
        result = poller.result()
        msg_id = result.get("id", str(uuid.uuid4()))
        return {
            "status":      "sent",
            "message_ids": [msg_id],
            "recipients":  recipients,
            "subject":     subject,
            "chart_count": len(inline_attachments),
        }
    except (ServiceRequestError, HttpResponseError) as e:
        print(f"[email-sender] Azure send failed ({e.__class__.__name__}): {e}")
        print(f"  Subject: {subject}")
        print(f"  Recipients: {recipients}")
        return {
            "status":      "azure_unreachable",
            "message_ids": [f"FAILED-{uuid.uuid4().hex[:8]}"],
            "recipients":  recipients,
            "subject":     subject,
            "note":        f"Azure send failed: {e.__class__.__name__}. Check connection string / network.",
        }
