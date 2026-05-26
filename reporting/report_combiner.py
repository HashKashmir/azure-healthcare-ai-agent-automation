"""
Report combiner — stitches the three individual mode reports into one
tabbed HTML file after an all-modes run.
"""

import glob
import os
from datetime import datetime

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "reports")

_MODE_LABELS = {
    "financial": "Financial",
    "clinical":  "Clinical",
    "billing":   "Billing",
}


def build_combined_report() -> dict:
    """
    Find the most recent RPT-*-{MODE}.html for each mode and combine them
    into a single tabbed RPT-COMBINED-*.html file.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    tab_bodies: dict[str, str] = {}
    for mode in ["financial", "clinical", "billing"]:
        pattern = os.path.join(OUTPUT_DIR, f"RPT-*-{mode.upper()}.html")
        files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
        if not files:
            continue
        with open(files[0], encoding="utf-8") as f:
            html = f.read()
        body = _extract_body(html)
        if body:
            tab_bodies[mode] = body

    if not tab_bodies:
        return {"success": False, "error": "No individual mode reports found."}

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    combined_path = os.path.join(OUTPUT_DIR, f"RPT-COMBINED-{ts}.html")
    with open(combined_path, "w", encoding="utf-8") as f:
        f.write(_render(tab_bodies))

    return {"success": True, "path": combined_path, "modes": list(tab_bodies.keys())}


def _extract_body(html: str) -> str:
    """Extract the content of <div class="body">...</div> from an individual report."""
    marker_open  = '<div class="body">'
    marker_close = '<div class="footer">'
    start = html.find(marker_open)
    end   = html.find(marker_close)
    if start == -1 or end == -1:
        return ""
    content = html[start + len(marker_open):end].strip()
    if content.endswith("</div>"):
        content = content[:-6].strip()
    return content


def _render(tab_bodies: dict[str, str]) -> str:
    now       = datetime.now()
    first_mode = next(iter(tab_bodies))

    tab_buttons = ""
    tab_panels  = ""
    for i, (mode, body) in enumerate(tab_bodies.items()):
        label      = _MODE_LABELS.get(mode, mode.capitalize())
        active_btn = " rtab-active" if i == 0 else ""
        hide_panel = "" if i == 0 else ' style="display:none"'
        tab_buttons += f'<button class="rtab-btn{active_btn}" onclick="switchTab(\'{mode}\')" id="rtab-{mode}">{label}</button>\n'
        tab_panels  += f'<div id="rpanel-{mode}" class="rpanel"{hide_panel}>\n{body}\n</div>\n'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Healthcare AI &mdash; Weekly Operations Report</title>
  <style>
    body{{font-family:'Segoe UI',Arial,sans-serif;background:#f5f7fa;margin:0;padding:0;color:#212121}}
    .wrapper{{max-width:860px;margin:32px auto;background:#fff;border-radius:8px;
              box-shadow:0 4px 18px rgba(0,0,0,.10);overflow:hidden}}
    .header{{background:#1565C0;color:#fff;padding:28px 36px}}
    .header h1{{margin:0 0 6px;font-size:22px;font-weight:700}}
    .header p{{margin:0;font-size:14px;opacity:.85}}
    .tab-bar{{background:#1976D2;padding:0 36px;display:flex;gap:2px;border-bottom:1px solid rgba(255,255,255,.15)}}
    .rtab-btn{{background:transparent;border:none;color:rgba(255,255,255,.65);padding:13px 24px;
               font-size:14px;font-weight:600;cursor:pointer;border-bottom:3px solid transparent;
               transition:all .15s;letter-spacing:.2px}}
    .rtab-btn:hover{{color:#fff;background:rgba(255,255,255,.08)}}
    .rtab-btn.rtab-active{{color:#fff;border-bottom-color:#fff;background:rgba(255,255,255,.1)}}
    .rpanel{{padding:28px 36px}}
    .section-title{{font-size:16px;font-weight:700;color:#1565C0;
                    border-bottom:2px solid #E3F2FD;padding-bottom:6px;margin:24px 0 14px}}
    .meta-table{{border-collapse:collapse;width:100%;margin-bottom:20px;font-size:13px}}
    .meta-table td{{padding:6px 10px;border:1px solid #e0e0e0}}
    .meta-table td:first-child{{background:#f5f7fa;font-weight:600;width:30%}}
    .footer{{background:#ECEFF1;padding:14px 36px;font-size:12px;color:#607D8B;text-align:center}}
  </style>
</head>
<body>
<div class="wrapper">
  <div class="header">
    <h1>Healthcare AI &mdash; Weekly Operations Report</h1>
    <p>{now.strftime('%B %d, %Y')} &bull; All Modes &bull; Financial &bull; Clinical &bull; Billing</p>
  </div>

  <div class="tab-bar">
    {tab_buttons}
  </div>

  {tab_panels}

  <div class="footer">
    This report was generated automatically by the Healthcare AI Agent.
    All data is synthetic and for demonstration purposes only.
    &copy; {now.year} Healthcare AI.
  </div>
</div>
<script>
function switchTab(mode){{
  document.querySelectorAll('.rpanel').forEach(function(p){{ p.style.display='none'; }});
  document.querySelectorAll('.rtab-btn').forEach(function(b){{ b.classList.remove('rtab-active'); }});
  document.getElementById('rpanel-'+mode).style.display='';
  document.getElementById('rtab-'+mode).classList.add('rtab-active');
}}
</script>
</body>
</html>"""
