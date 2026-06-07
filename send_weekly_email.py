"""
send_weekly_email.py
────────────────────
Pulls job data from Google Sheets and emails a weekly digest of tasks
due within the next 7 days (plus any overdue items).

SCHEDULING
──────────
Windows — Task Scheduler:
  1. Open Task Scheduler → Create Basic Task
  2. Trigger: Weekly → Monday → 7:00 AM
  3. Action: Start a Program
       Program:   python
       Arguments: "C:\full\path\to\send_weekly_email.py"

Mac — cron:
  1. Terminal: crontab -e
  2. Add:  0 7 * * 1 /usr/bin/python3 /full/path/to/send_weekly_email.py
  3. Save (:wq)
"""

import csv, io, datetime, smtplib, requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ══════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════
GMAIL_ADDRESS      = "gregjrobinson85@gmail.com"
GMAIL_APP_PASSWORD = "phnl uhbb gxhu qnvg"
RECIPIENT_EMAIL    = "gregjrobinson85@gmail.com"

SHEET_ID  = "10yCntGjKe7e0o_qWxFfRUggKiLDb2jr3qGLq-QKz3hw"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=0"
# ══════════════════════════════════════════════════════════════════════════

TODAY = datetime.date.today()

# ── Date parser ────────────────────────────────────────────────────────────
def parse_date(val):
    if not val or not str(val).strip():
        return None
    val = str(val).strip()
    # Full date formats
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%m/%d/%y",
                "%B %d, %Y", "%b %d, %Y", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(val, fmt).date()
        except ValueError:
            pass
    # No-year formats — assume current year
    for fmt in ("%m/%d", "%m-%d", "%B %d", "%b %d"):
        try:
            d = datetime.datetime.strptime(val, fmt)
            return d.replace(year=TODAY.year).date()
        except ValueError:
            pass
    return None

# ── Pull and filter data from Google Sheets ────────────────────────────────
def fetch_action_items():
    print("Fetching data from Google Sheets...")
    try:
        resp = requests.get(SHEET_URL, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"Could not fetch Google Sheet: {e}\n"
                           "Make sure the sheet is shared (anyone with link can view).")

    rows = list(csv.reader(io.StringIO(resp.text)))
    if not rows:
        raise RuntimeError("Google Sheet appears to be empty.")

    # Map exact column names
    header = [h.strip() for h in rows[0]]
    print(f"Columns found: {header}")

    def find_col(names):
        for name in names:
            for i, h in enumerate(header):
                if h.lower() == name.lower():
                    return i
        return None

    col_job  = find_col(["Job Name"])
    col_owner= find_col(["Owner"])
    col_task = find_col(["Next Task Due"])
    col_when = find_col(["When"])

    missing = [n for n, i in [("Job Name", col_job), ("Owner", col_owner),
                               ("Next Task Due", col_task), ("When", col_when)]
               if i is None]
    if missing:
        print(f"WARNING: Could not find columns: {missing}. Using positions 0,1,2,3.")
        col_job, col_owner, col_task, col_when = 0, 1, 2, 3

    items = []
    for row in rows[1:]:
        if not any(row):
            continue

        def safe(idx):
            return str(row[idx]).strip() if idx is not None and idx < len(row) else ""

        job   = safe(col_job)
        owner = safe(col_owner)
        task  = safe(col_task)
        due   = parse_date(safe(col_when))

        if not job and not task:
            continue

        # ── Only include past dates and dates due within 7 days ───────────
        if due is None:
            continue
        days = (due - TODAY).days
        if days > 7:
            continue  # skip future dates beyond one week out

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

        items.append({
            "job": job, "owner": owner, "task": task,
            "due": due_str, "days": days,
            "urgency": urgency, "action": action,
        })

    # Sort overdue first, then by soonest due date
    items.sort(key=lambda x: x["days"])
    print(f"Found {len(items)} items (past due + due within 7 days).")
    return items

# ── Styles ─────────────────────────────────────────────────────────────────
TH = ("font-family:Arial,sans-serif;font-size:11px;font-weight:bold;color:#ffffff;"
      "padding:8px 10px;text-align:left;")
TD = ("font-family:Arial,sans-serif;font-size:11px;color:#1a1a1a;"
      "padding:8px 10px;vertical-align:top;border-bottom:1px solid #e8e8e8;")

URGENCY_STYLE = {
    "overdue":   {"label": "🔴 OVERDUE",       "color": "#C00000", "bg": "#FFCCCC"},
    "today":     {"label": "🔴 DUE TODAY",      "color": "#C00000", "bg": "#FFCCCC"},
    "this_week": {"label": "🟠 DUE THIS WEEK",  "color": "#C55A11", "bg": "#FCE4D6"},
}

# ── Build HTML email ───────────────────────────────────────────────────────
def build_html(items):
    date_str      = TODAY.strftime("%A, %B %d, %Y")
    overdue_count = sum(1 for i in items if i["urgency"] == "overdue")
    today_count   = sum(1 for i in items if i["urgency"] == "today")
    week_count    = sum(1 for i in items if i["urgency"] == "this_week")

    summary_parts = []
    if overdue_count:
        summary_parts.append(
            f'<span style="color:#ffcccc;font-weight:bold">{overdue_count} Overdue</span>')
    if today_count:
        summary_parts.append(
            f'<span style="color:#ffcccc;font-weight:bold">{today_count} Due Today</span>')
    if week_count:
        summary_parts.append(
            f'<span style="color:#FCE4D6;font-weight:bold">{week_count} Due This Week</span>')
    summary_parts.append(f'{len(items)} Total')
    summary_str = " &nbsp;|&nbsp; ".join(summary_parts)

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
        rows_html = """
        <tr><td colspan="5" style="text-align:center;padding:32px;color:#375623;
            font-size:14px;font-weight:bold;">
            ✅ No tasks due in the next 7 days — great work!
        </td></tr>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4;padding:24px 0">
<tr><td align="center">
<table width="700" cellpadding="0" cellspacing="0"
    style="background:#ffffff;border-radius:6px;overflow:hidden;
           box-shadow:0 2px 8px rgba(0,0,0,0.12)">

  <tr><td style="background:#1F3864;padding:24px 28px">
    <div style="color:#ffffff;font-size:22px;font-weight:bold">
      📋 Weekly Action Items
    </div>
    <div style="color:#BDD7EE;font-size:13px;margin-top:4px">{date_str}</div>
  </td></tr>

  <tr><td style="background:#2E75B6;padding:10px 28px;font-size:13px;color:#ffffff">
    {summary_str}
  </td></tr>

  <tr><td style="padding:20px 16px">
  <table width="100%" cellpadding="0" cellspacing="0"
      style="border-collapse:collapse">
    <tr style="background:#1F6B75">
      <th style="{TH}width:20%">Job Name</th>
      <th style="{TH}width:12%">Owner</th>
      <th style="{TH}width:25%">Next Task Due</th>
      <th style="{TH}width:11%">When</th>
      <th style="{TH}width:32%">Suggested Action</th>
    </tr>
    {rows_html}
  </table>
  </td></tr>

  <tr><td style="background:#f4f4f4;padding:16px 28px;font-size:11px;
      color:#7F7F7F;border-top:1px solid #e0e0e0">
    Auto-generated every Monday from your
    <a href="https://docs.google.com/spreadsheets/d/{SHEET_ID}"
    style="color:#2E75B6">Job Tracker Google Sheet</a>.
    Only tasks due within 7 days (plus overdue) are shown.
  </td></tr>

</table>
</td></tr></table>
</body></html>"""

# ── Send email ─────────────────────────────────────────────────────────────
def send_email(html_body, items):
    date_str      = TODAY.strftime("%B %d, %Y")
    overdue_count = sum(1 for i in items if i["urgency"] in ("overdue", "today"))

    subject = f"📋 Weekly Action Items — {date_str}"
    if overdue_count:
        subject = f"⚠️ {overdue_count} Overdue  |  " + subject

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(html_body, "html"))

    print("Connecting to Gmail SMTP...")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD.replace(" ", ""))
        server.sendmail(GMAIL_ADDRESS, RECIPIENT_EMAIL, msg.as_string())
    print(f"✅ Email sent to {RECIPIENT_EMAIL}")

# ── Main ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    items = fetch_action_items()
    html  = build_html(items)
    send_email(html, items)
