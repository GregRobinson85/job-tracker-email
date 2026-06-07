"""
app.py — Flask web app
Serves a simple dashboard and triggers the weekly email on demand or on schedule.
Deploy on Render.com as a Web Service + Cron Job.
"""

from flask import Flask, render_template_string, jsonify
import datetime, csv, io, smtplib, requests, os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

app = Flask(__name__)

# ══════════════════════════════════════════════════════════════════════════
# CONFIG — set these as Environment Variables in Render (never hardcode secrets)
# ══════════════════════════════════════════════════════════════════════════
GMAIL_ADDRESS      = os.environ.get("GMAIL_ADDRESS",      "gregjrobinson85@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
RECIPIENT_EMAIL    = os.environ.get("RECIPIENT_EMAIL",    "gregjrobinson85@gmail.com")
SHEET_ID           = "10yCntGjKe7e0o_qWxFfRUggKiLDb2jr3qGLq-QKz3hw"
SHEET_URL          = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=0"

TODAY = datetime.date.today()

# ── Date parser ────────────────────────────────────────────────────────────
def parse_date(val):
    today = datetime.date.today()
    if not val or not str(val).strip():
        return None
    val = str(val).strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%m/%d/%y",
                "%B %d, %Y", "%b %d, %Y", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(val, fmt).date()
        except ValueError:
            pass
    for fmt in ("%m/%d", "%m-%d", "%B %d", "%b %d"):
        try:
            d = datetime.datetime.strptime(val, fmt)
            return d.replace(year=today.year).date()
        except ValueError:
            pass
    return None

# ── Fetch and filter items ─────────────────────────────────────────────────
def fetch_action_items():
    today = datetime.date.today()
    resp  = requests.get(SHEET_URL, timeout=15)
    resp.raise_for_status()
    rows  = list(csv.reader(io.StringIO(resp.text)))
    if not rows:
        return []

    header = [h.strip() for h in rows[0]]
    def find_col(names):
        for name in names:
            for i, h in enumerate(header):
                if h.lower() == name.lower():
                    return i
        return None

    col_job   = find_col(["Job Name"])
    col_owner = find_col(["Owner"])
    col_task  = find_col(["Next Task Due"])
    col_when  = find_col(["When"])
    if any(c is None for c in [col_job, col_owner, col_task, col_when]):
        col_job, col_owner, col_task, col_when = 0, 1, 2, 3

    items = []
    for row in rows[1:]:
        if not any(row): continue
        def safe(idx):
            return str(row[idx]).strip() if idx is not None and idx < len(row) else ""
        job   = safe(col_job)
        owner = safe(col_owner)
        task  = safe(col_task)
        due   = parse_date(safe(col_when))
        if not job and not task: continue
        if due is None: continue
        days = (due - today).days
        if days > 7: continue

        due_str = due.strftime("%m/%d/%Y")
        if days < 0:
            urgency = "overdue"
            action  = "⚠ OVERDUE — Contact owner immediately and escalate"
        elif days == 0:
            urgency = "today"
            action  = "🔴 DUE TODAY — Confirm completion with owner"
        elif days <= 3:
            urgency = "this_week"
            action  = "📞 Call owner — task due in the next few days"
        else:
            urgency = "this_week"
            action  = "📧 Email owner — confirm task is on track for due date"

        items.append({"job": job, "owner": owner, "task": task,
                      "due": due_str, "days": days,
                      "urgency": urgency, "action": action})

    items.sort(key=lambda x: x["days"])
    return items

# ── Build HTML email body ──────────────────────────────────────────────────
TH = ("font-family:Arial,sans-serif;font-size:11px;font-weight:bold;color:#ffffff;"
      "padding:8px 10px;text-align:left;")
TD = ("font-family:Arial,sans-serif;font-size:11px;color:#1a1a1a;"
      "padding:8px 10px;vertical-align:top;border-bottom:1px solid #e8e8e8;")
URGENCY_STYLE = {
    "overdue":   {"color": "#C00000", "bg": "#FFCCCC"},
    "today":     {"color": "#C00000", "bg": "#FFCCCC"},
    "this_week": {"color": "#C55A11", "bg": "#FCE4D6"},
}

def build_email_html(items):
    today     = datetime.date.today()
    date_str  = today.strftime("%A, %B %d, %Y")
    overdue   = sum(1 for i in items if i["urgency"] == "overdue")
    due_today = sum(1 for i in items if i["urgency"] == "today")
    this_week = sum(1 for i in items if i["urgency"] == "this_week")

    parts = []
    if overdue:   parts.append(f'<span style="color:#ffcccc;font-weight:bold">{overdue} Overdue</span>')
    if due_today: parts.append(f'<span style="color:#ffcccc;font-weight:bold">{due_today} Due Today</span>')
    if this_week: parts.append(f'<span style="color:#FCE4D6;font-weight:bold">{this_week} Due This Week</span>')
    parts.append(f'{len(items)} Total')
    summary = " &nbsp;|&nbsp; ".join(parts)

    rows_html = ""
    if items:
        for item in items:
            meta = URGENCY_STYLE.get(item["urgency"], URGENCY_STYLE["this_week"])
            if item["days"] < 0:
                days_display = f"{abs(item['days'])}d overdue"
            elif item["days"] == 0:
                days_display = "Today"
            else:
                days_display = f"{item['days']} day{'s' if item['days'] != 1 else ''}"
            rows_html += f"""
            <tr style="background:{meta['bg']}">
              <td style="{TD}font-weight:bold">{item['job'] or '—'}</td>
              <td style="{TD}">{item['owner'] or '—'}</td>
              <td style="{TD}">{item['task'] or '—'}</td>
              <td style="{TD};text-align:center;white-space:nowrap">
                {item['due']}<br>
                <span style="font-weight:bold;color:{meta['color']}">{days_display}</span>
              </td>
              <td style="{TD}">{item['action']}</td>
            </tr>"""
    else:
        rows_html = '<tr><td colspan="5" style="text-align:center;padding:32px;color:#375623;font-size:14px;font-weight:bold;">✅ No tasks due in the next 7 days!</td></tr>'

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4;padding:24px 0">
<tr><td align="center">
<table width="700" cellpadding="0" cellspacing="0"
    style="background:#ffffff;border-radius:6px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.12)">
  <tr><td style="background:#1F3864;padding:24px 28px">
    <div style="color:#ffffff;font-size:22px;font-weight:bold">📋 Weekly Action Items</div>
    <div style="color:#BDD7EE;font-size:13px;margin-top:4px">{date_str}</div>
  </td></tr>
  <tr><td style="background:#2E75B6;padding:10px 28px;font-size:13px;color:#ffffff">{summary}</td></tr>
  <tr><td style="padding:20px 16px">
  <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">
    <tr style="background:#1F6B75">
      <th style="{TH}width:20%">Job Name</th>
      <th style="{TH}width:12%">Owner</th>
      <th style="{TH}width:25%">Next Task Due</th>
      <th style="{TH}width:11%">When</th>
      <th style="{TH}width:32%">Suggested Action</th>
    </tr>
    {rows_html}
  </table></td></tr>
  <tr><td style="background:#f4f4f4;padding:16px 28px;font-size:11px;color:#7F7F7F;border-top:1px solid #e0e0e0">
    Auto-generated from your <a href="https://docs.google.com/spreadsheets/d/{SHEET_ID}" style="color:#2E75B6">Job Tracker Google Sheet</a>.
  </td></tr>
</table></td></tr></table></body></html>"""

# ── Send email ─────────────────────────────────────────────────────────────
def send_email(items):
    today    = datetime.date.today()
    overdue  = sum(1 for i in items if i["urgency"] in ("overdue", "today"))
    date_str = today.strftime("%B %d, %Y")
    subject  = f"📋 Weekly Action Items — {date_str}"
    if overdue:
        subject = f"⚠️ {overdue} Overdue  |  " + subject

    html = build_email_html(items)
    msg  = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD.replace(" ", ""))
        server.sendmail(GMAIL_ADDRESS, RECIPIENT_EMAIL, msg.as_string())

# ══════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════
DASHBOARD = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Job Tracker — Email Scheduler</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: Arial, sans-serif; background: #f4f4f4; display: flex;
           justify-content: center; align-items: center; min-height: 100vh; padding: 24px; }
    .card { background: #fff; border-radius: 8px; box-shadow: 0 2px 12px rgba(0,0,0,0.12);
            max-width: 560px; width: 100%; overflow: hidden; }
    .header { background: #1F3864; color: #fff; padding: 28px 32px; }
    .header h1 { font-size: 22px; }
    .header p  { color: #BDD7EE; font-size: 13px; margin-top: 6px; }
    .body { padding: 28px 32px; }
    .info { background: #EEF4FB; border-left: 4px solid #2E75B6; padding: 14px 16px;
            border-radius: 4px; font-size: 13px; color: #1F3864; margin-bottom: 24px; line-height: 1.6; }
    .btn { display: block; width: 100%; background: #2E75B6; color: #fff; border: none;
           padding: 14px; border-radius: 6px; font-size: 15px; font-weight: bold;
           cursor: pointer; transition: background 0.2s; }
    .btn:hover { background: #1F5A9A; }
    .btn:disabled { background: #aaa; cursor: not-allowed; }
    .status { margin-top: 18px; padding: 12px 16px; border-radius: 6px;
              font-size: 13px; font-weight: bold; display: none; }
    .status.success { background: #E2EFDA; color: #375623; display: block; }
    .status.error   { background: #FFCCCC; color: #C00000; display: block; }
    .status.loading { background: #FFF2CC; color: #7F6000; display: block; }
    .footer { background: #f4f4f4; border-top: 1px solid #e0e0e0;
              padding: 14px 32px; font-size: 11px; color: #7F7F7F; }
  </style>
</head>
<body>
<div class="card">
  <div class="header">
    <h1>📋 Job Tracker Email Scheduler</h1>
    <p>Westerlies Design Build &nbsp;|&nbsp; Weekly Action Items</p>
  </div>
  <div class="body">
    <div class="info">
      ⏰ <strong>Scheduled:</strong> Every Monday at 7:00 AM (automatic)<br>
      📬 <strong>Recipient:</strong> gregjrobinson85@gmail.com<br>
      📊 <strong>Source:</strong> Job Tracker Google Sheet<br>
      🔍 <strong>Filter:</strong> Overdue + due within 7 days
    </div>
    <button class="btn" id="sendBtn" onclick="sendNow()">
      Send Email Now
    </button>
    <div class="status" id="status"></div>
  </div>
  <div class="footer">
    Emails are sent automatically every Monday. Use the button above to trigger manually.
  </div>
</div>

<script>
async function sendNow() {
  const btn    = document.getElementById('sendBtn');
  const status = document.getElementById('status');
  btn.disabled = true;
  btn.textContent = 'Sending...';
  status.className = 'status loading';
  status.textContent = '⏳ Fetching data and sending email...';

  try {
    const res  = await fetch('/send', { method: 'POST' });
    const data = await res.json();
    if (data.success) {
      status.className = 'status success';
      status.textContent = '✅ ' + data.message;
    } else {
      status.className = 'status error';
      status.textContent = '❌ ' + data.message;
    }
  } catch(e) {
    status.className = 'status error';
    status.textContent = '❌ Network error — please try again.';
  }
  btn.disabled = false;
  btn.textContent = 'Send Email Now';
}
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(DASHBOARD)

@app.route("/send", methods=["POST"])
def send():
    try:
        items = fetch_action_items()
        send_email(items)
        return jsonify({"success": True,
                        "message": f"Email sent! {len(items)} action item(s) included."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# This route is called by Render's cron job every Monday
@app.route("/cron/weekly", methods=["GET", "POST"])
def cron_weekly():
    try:
        items = fetch_action_items()
        send_email(items)
        return jsonify({"success": True, "sent": len(items),
                        "timestamp": datetime.datetime.utcnow().isoformat()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
