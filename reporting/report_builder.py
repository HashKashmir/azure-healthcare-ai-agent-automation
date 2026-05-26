"""
Report builder — assembles the HTML insight report from GPT-4o insights
and chart PNGs, then optionally converts it to PDF via WeasyPrint.
"""

import base64
import os
from datetime import datetime

SEVERITY_ICON = {
    "critical": ("&#9888;",  "#B71C1C", "#FFEBEE"),
    "warning":  ("&#9888;",  "#E65100", "#FFF3E0"),
    "ok":       ("&#10003;", "#1B5E20", "#E8F5E9"),
    "info":     ("&#9670;",  "#0D47A1", "#E3F2FD"),
}

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
OUTPUT_DIR   = os.path.join(os.path.dirname(__file__), "..", "data", "reports")


def build_report(insights: dict | list, chart_paths: list[str], report_mode: str) -> dict:
    """
    Assemble an HTML report from insights and chart PNGs.
    Tries to convert to PDF with WeasyPrint; skips gracefully if unavailable.

    Args:
        insights:     Dict with an 'insights' key, or list of insight dicts.
        chart_paths:  List of PNG file paths produced by chart_generator.
        report_mode:  'financial' | 'clinical' | 'billing'

    Returns:
        Dict with 'html_path', 'pdf_path' (may be None), and 'report_id'.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Normalise insights to a plain list
    if isinstance(insights, dict):
        insight_list = insights.get("insights", [])
    else:
        insight_list = insights

    now       = datetime.now()
    week_str  = now.strftime("Week of %B %d, %Y")
    report_id = f"RPT-{now.strftime('%Y%m%d-%H%M%S')}-{report_mode.upper()}"

    html = _render_html(insight_list, chart_paths, report_mode, week_str, report_id)

    html_path = os.path.join(OUTPUT_DIR, f"{report_id}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    pdf_path = _try_pdf(html, report_id)

    return {
        "report_id":   report_id,
        "html_path":   html_path,
        "pdf_path":    pdf_path,
        "report_mode": report_mode,
        "generated_at": now.isoformat(),
    }


def _render_html(
    insights: list,
    chart_paths: list[str],
    mode: str,
    week_str: str,
    report_id: str,
) -> str:
    mode_labels = {
        "financial": ("Financial Insight Report", "CFO / Finance Administration"),
        "clinical":  ("Clinical Operations Report", "Operations Director"),
        "billing":   ("Billing Performance Report", "Billing Manager"),
    }
    title, audience = mode_labels.get(mode, ("Admin Report", "Administration"))

    insight_html = ""
    for item in insights:
        sev  = item.get("severity", "info")
        icon, text_color, bg_color = SEVERITY_ICON.get(sev, SEVERITY_ICON["info"])
        insight_html += f"""
        <div style="background:{bg_color};border-left:4px solid {text_color};
                    padding:14px 18px;margin-bottom:14px;border-radius:4px;">
          <div style="color:{text_color};font-weight:700;font-size:15px;margin-bottom:6px;">
            {icon}&nbsp;&nbsp;{item.get('title','')}</div>
          <div style="color:#333;font-size:14px;line-height:1.6;">{item.get('detail','')}</div>
          <div style="color:#555;font-size:13px;margin-top:8px;">
            <strong>Recommendation:</strong> {item.get('recommendation','')}</div>
        </div>"""

    chart_html = ""
    for path in chart_paths:
        if os.path.exists(path):
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            chart_html += f"""
            <div style="margin:20px 0;text-align:center;">
              <img src="data:image/png;base64,{b64}"
                   style="max-width:100%;border-radius:6px;box-shadow:0 2px 8px rgba(0,0,0,.12);" />
            </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Healthcare AI {title}</title>
  <style>
    body {{font-family:'Segoe UI',Arial,sans-serif;background:#f5f7fa;margin:0;padding:0;color:#212121;}}
    .wrapper {{max-width:820px;margin:32px auto;background:#fff;border-radius:8px;
               box-shadow:0 4px 18px rgba(0,0,0,.10);overflow:hidden;}}
    .header {{background:#1565C0;color:#fff;padding:28px 36px;}}
    .header h1 {{margin:0 0 6px;font-size:22px;font-weight:700;}}
    .header p  {{margin:0;font-size:14px;opacity:.85;}}
    .body {{padding:28px 36px;}}
    .section-title {{font-size:16px;font-weight:700;color:#1565C0;
                     border-bottom:2px solid #E3F2FD;padding-bottom:6px;margin:24px 0 14px;}}
    .meta-table {{border-collapse:collapse;width:100%;margin-bottom:20px;font-size:13px;}}
    .meta-table td {{padding:6px 10px;border:1px solid #e0e0e0;}}
    .meta-table td:first-child {{background:#f5f7fa;font-weight:600;width:30%;}}
    .footer {{background:#ECEFF1;padding:14px 36px;font-size:12px;color:#607D8B;text-align:center;}}
  </style>
</head>
<body>
  <div class="wrapper">
    <div class="header">
      <h1>Healthcare AI &mdash; {title}</h1>
      <p>{week_str} &bull; Audience: {audience} &bull; Report ID: {report_id}</p>
    </div>
    <div class="body">

      <div class="section-title">Report Metadata</div>
      <table class="meta-table">
        <tr><td>Report Mode</td><td>{mode.capitalize()}</td></tr>
        <tr><td>Period</td><td>{week_str}</td></tr>
        <tr><td>Generated</td><td>{datetime.now().strftime('%Y-%m-%d %H:%M UTC')}</td></tr>
        <tr><td>Generated By</td><td>Healthcare AI Analytics Agent</td></tr>
        <tr><td>Report ID</td><td>{report_id}</td></tr>
      </table>

      <div class="section-title">AI-Generated Insights</div>
      {insight_html or '<p style="color:#9E9E9E;">No insights generated.</p>'}

      <div class="section-title">Charts</div>
      {chart_html or '<p style="color:#9E9E9E;">No charts generated.</p>'}

    </div>
    <div class="footer">
      This report was generated automatically by the Healthcare AI Agent.
      All data is synthetic and for demonstration purposes only.
      &copy; {datetime.now().year} Healthcare AI.
    </div>
  </div>
</body>
</html>"""


def _try_pdf(html: str, report_id: str) -> str | None:
    try:
        from weasyprint import HTML
        pdf_path = os.path.join(OUTPUT_DIR, f"{report_id}.pdf")
        HTML(string=html).write_pdf(pdf_path)
        return pdf_path
    except Exception:
        return None   # WeasyPrint requires system fonts; skip gracefully in CI/dev
