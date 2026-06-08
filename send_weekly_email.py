"""
send_weekly_email.py
Pulls job data and employee start dates from Google Sheets and emails:
  1. Action items due within 7 days (including overdue)
  2. Employee performance reviews due within 7 days (6-month intervals)
Run by GitHub Actions every Monday at 7 AM.
"""

import csv, io, datetime, smtplib, requests, os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ══════════════════════════════════════════════════════════════════════════
# CONFIG — values injected from GitHub Secrets
# ══════════════════════════════════════════════════════════════════════════
GMAIL_ADDRESS      = os.environ.get("GMAIL_ADDRESS",      "gregjrobinson85@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
RECIPIENT_EMAIL    = os.environ.get("RECIPIENT_EMAIL",    "gregjrobinson85@gmail.com")

SHEET_ID       = "10yCntGjKe7e0o_qWxFfRUggKiLDb2jr3qGLq-QKz3hw"
JOBS_URL       = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=0"
EMPLOYEES_URL  = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=1"
# ══════════════════════════════════════════════════════════════════════════

TODAY = datetime.date.today()

# ── Date parser ────────────────────────────────────────────────────────────
def parse_date(val):
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
            return d.replace(year=TODAY.year).date()
        except ValueError:
            pass
    return None

# ── Next 6-month interval from a start date ───────────────────────────────
def next_review_date(start_date):
    """Returns the next upcoming 6-month anniversary on or after today."""
    # Calculate how many 6-month periods have elapsed
    months_elapsed = (TODAY.year - start_date.year) * 12 + (TODAY.month - start_date.month)
    intervals = months_elapsed // 6

    # Step forward through intervals until we find the next upcoming one
    for i in range(intervals, intervals + 3):
        total_months = i * 6
        years_add    = total_months // 12
        months_add   = total_months % 12
        review_month = start_date.month + months_add
        review_year  = start_date.year + years_add + (review_month - 1) // 12
        review_month = ((review_month - 1) % 12) + 1
        # Handle end-of-month edge cases
        import calendar
        last_day = calendar.monthrange(review_year, review_month)[1]
        review_day = min(start_date.day, last_day)
        review = datetime.date(review_year, review_month, review_day)
        if review >= TODAY:
            return review

    return None

# ── Fetch job action items ─────────────────────────────────────────────────
def fetch_action_items():
    print("Fetching job data from Google Sheets...")
    resp = requests.get(JOBS_URL, timeout=15)
    resp.raise_for_status()
    rows = list(csv.reader(io.StringIO(resp.text)))
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
        if due is None:
            continue
        days = (due - TODAY).days
        if days > 7:
            continue

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
    print(f"Found {len(items)} job action items.")
    return items

# ── Fetch employee review alerts ───────────────────────────────────────────
def fetch_employee_reviews():
    print("Fetching employee data from Google Sheets...")
    try:
        resp = requests.get(EMPLOYEES_URL, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"WARNING: Could not fetch Employees sheet: {e}")
        return []

    rows = list(csv.reader(io.StringIO(resp.text)))
    if not rows:
        return []

    header = [h.strip() for h in rows[0]]
    print(f"Employee columns found: {header}")

    def find_col(names):
        for name in names:
            for i, h in enumerate(header):
                if h.lower() == name.lower():
                    return i
        return None

    col_name  = find_col(["employee name", "name", "employee"])
    col_start = find_col(["start date", "start", "hire date", "hired"])
    if col_name is None: col_name  = 0
    if col_start is None: col_start = 1

    alerts = []
    for row in rows[1:]:
        if not any(row):
            continue
        def safe(idx):
            return str(row[idx]).strip() if idx is not None and idx < len(row) else ""

        name       = safe(col_name)
        start_date = parse_date(safe(col_start))

        if not name or not start_date:
            continue

        review_date = next_review_date(start_date)
        if review_date is None:
            continue

        days = (review_date - TODAY).days

        # Only alert if review is within 7 days (or overdue)
        if days > 7:
            continue

        # Calculate which interval this is (6mo, 12mo, 18mo, etc.)
        months_elapsed = (review_date.year - start_date.year) * 12 + (review_date.month - start_date.month)
        interval_label = f"{months_elapsed}-month"

        due_str = review_date.strftime("%m/%d/%Y")
        tenure  = f"{months_elapsed} months"

        if days < 0:
            urgency = "overdue"
            note    = f"⚠ OVERDUE — {interval_label} review was due {abs(days)} days ago"
        elif days == 0:
            urgency = "today"
            note    = f"🔴 DUE TODAY — Schedule {interval_label} performance review"
        else:
            urgency = "upcoming"
            note    = f"📅 Schedule {interval_label} performance review before {due_str}"

        alerts.append({"name": name, "start": start_date.strftime("%m/%d/%Y"),
                       "tenure": tenure, "review_date": due_str,
                       "days": days, "urgency": urgency,
                       "interval": interval_label, "note": note})

    alerts.sort(key=lambda x: x["days"])
    print(f"Found {len(alerts)} employee review alerts.")
    return alerts

# ── Styles ─────────────────────────────────────────────────────────────────
TH = ("font-family:Arial,sans-serif;font-size:11px;font-weight:bold;color:#ffffff;"
      "padding:8px 10px;text-align:left;")
TD = ("font-family:Arial,sans-serif;font-size:11px;color:#1a1a1a;"
      "padding:8px 10px;vertical-align:top;border-bottom:1px solid #e8e8e8;")

JOB_URGENCY_STYLE = {
    "overdue":   {"color": "#C00000", "bg": "#FFCCCC"},
    "today":     {"color": "#C00000", "bg": "#FFCCCC"},
    "this_week": {"color": "#C55A11", "bg": "#FCE4D6"},
}
EMP_URGENCY_STYLE = {
    "overdue":  {"color": "#C00000", "bg": "#FFCCCC"},
    "today":    {"color": "#C00000", "bg": "#FFCCCC"},
    "upcoming": {"color": "#375623", "bg": "#E2EFDA"},
}

# ── Build HTML email ───────────────────────────────────────────────────────
def build_html(job_items, emp_alerts):
    date_str     = TODAY.strftime("%A, %B %d, %Y")
    job_overdue  = sum(1 for i in job_items   if i["urgency"] in ("overdue","today"))
    emp_overdue  = sum(1 for i in emp_alerts  if i["urgency"] in ("overdue","today"))
    total_alerts = len(job_items) + len(emp_alerts)

    parts = []
    if job_overdue + emp_overdue:
        parts.append(f'<span style="color:#ffcccc;font-weight:bold">{job_overdue + emp_overdue} Overdue</span>')
    parts.append(f'{len(job_items)} Job Tasks')
    parts.append(f'{len(emp_alerts)} Employee Review{"s" if len(emp_alerts) != 1 else ""}')
    parts.append(f'{total_alerts} Total Alerts')
    summary = " &nbsp;|&nbsp; ".join(parts)

    # ── Job action items rows ──────────────────────────────────────────────
    job_rows = ""
    if job_items:
        for item in job_items:
            meta = JOB_URGENCY_STYLE.get(item["urgency"], JOB_URGENCY_STYLE["this_week"])
            if item["days"] < 0:
                days_display = f"{abs(item['days'])}d overdue"
            elif item["days"] == 0:
                days_display = "Today"
            else:
                days_display = f"{item['days']} day{'s' if item['days'] != 1 else ''}"
            job_rows += f"""
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
        job_rows = '<tr><td colspan="5" style="text-align:center;padding:20px;color:#375623;font-size:13px;font-weight:bold;">✅ No job tasks due this week</td></tr>'

    # ── Employee review rows ───────────────────────────────────────────────
    emp_rows = ""
    if emp_alerts:
        for emp in emp_alerts:
            meta = EMP_URGENCY_STYLE.get(emp["urgency"], EMP_URGENCY_STYLE["upcoming"])
            if emp["days"] < 0:
                days_display = f"{abs(emp['days'])}d overdue"
            elif emp["days"] == 0:
                days_display = "Today"
            else:
                days_display = f"In {emp['days']} day{'s' if emp['days'] != 1 else ''}"
            emp_rows += f"""
            <tr style="background:{meta['bg']}">
              <td style="{TD}font-weight:bold">{emp['name']}</td>
              <td style="{TD}">{emp['start']}</td>
              <td style="{TD}">{emp['tenure']}</td>
              <td style="{TD};text-align:center;white-space:nowrap">
                {emp['review_date']}<br>
                <span style="font-weight:bold;color:{meta['color']}">{days_display}</span>
              </td>
              <td style="{TD}">{emp['note']}</td>
            </tr>"""
    else:
        emp_rows = '<tr><td colspan="5" style="text-align:center;padding:20px;color:#375623;font-size:13px;font-weight:bold;">✅ No performance reviews due this week</td></tr>'

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4;padding:24px 0">
<tr><td align="center">
<table width="700" cellpadding="0" cellspacing="0"
    style="background:#ffffff;border-radius:6px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.12)">

  <!-- Header -->
  <tr><td style="background:#1F3864;padding:24px 28px">
    <div style="color:#ffffff;font-size:22px;font-weight:bold">📋 Weekly Action Items</div>
    <div style="color:#BDD7EE;font-size:13px;margin-top:4px">{date_str}</div>
  </td></tr>
  <tr><td style="background:#2E75B6;padding:10px 28px;font-size:13px;color:#ffffff">{summary}</td></tr>

  <!-- Section 1: Job Tasks -->
  <tr><td style="background:#1F6B75;padding:10px 16px">
    <span style="color:#ffffff;font-size:13px;font-weight:bold">🏗 Job Action Items</span>
    <span style="color:#BDD7EE;font-size:11px;margin-left:8px">Tasks overdue or due within 7 days</span>
  </td></tr>
  <tr><td style="padding:0 16px 16px">
  <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">
    <tr style="background:#2E75B6">
      <th style="{TH}width:20%">Job Name</th>
      <th style="{TH}width:12%">Owner</th>
      <th style="{TH}width:25%">Next Task Due</th>
      <th style="{TH}width:11%">When</th>
      <th style="{TH}width:32%">Suggested Action</th>
    </tr>
    {job_rows}
  </table></td></tr>

  <!-- Section 2: Employee Reviews -->
  <tr><td style="background:#375623;padding:10px 16px">
    <span style="color:#ffffff;font-size:13px;font-weight:bold">👤 Employee Performance Reviews</span>
    <span style="color:#C6EFCE;font-size:11px;margin-left:8px">6-month intervals — due within 7 days</span>
  </td></tr>
  <tr><td style="padding:0 16px 16px">
  <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">
    <tr style="background:#375623">
      <th style="{TH}width:20%">Employee</th>
      <th style="{TH}width:14%">Start Date</th>
      <th style="{TH}width:14%">Tenure</th>
      <th style="{TH}width:14%">Review Due</th>
      <th style="{TH}width:38%">Action</th>
    </tr>
    {emp_rows}
  </table></td></tr>

  <!-- Footer -->
  <tr><td style="background:#f4f4f4;padding:16px 28px;font-size:11px;color:#7F7F7F;border-top:1px solid #e0e0e0">
    Auto-generated every Monday by GitHub Actions from your
    <a href="https://docs.google.com/spreadsheets/d/{SHEET_ID}" style="color:#2E75B6">Job Tracker Google Sheet</a>.
  </td></tr>

</table></td></tr></table></body></html>"""

# ── Send email ─────────────────────────────────────────────────────────────
def send_email(job_items, emp_alerts):
    overdue  = sum(1 for i in job_items + emp_alerts if i["urgency"] in ("overdue","today"))
    date_str = TODAY.strftime("%B %d, %Y")
    subject  = f"📋 Weekly Action Items — {date_str}"
    if overdue:
        subject = f"⚠️ {overdue} Overdue  |  " + subject

    html = build_html(job_items, emp_alerts)
    msg  = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(html, "html"))

    print("Connecting to Gmail SMTP...")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD.replace(" ", ""))
        server.sendmail(GMAIL_ADDRESS, RECIPIENT_EMAIL, msg.as_string())
    print(f"✅ Email sent to {RECIPIENT_EMAIL}")

# ── Main ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    job_items  = fetch_action_items()
    emp_alerts = fetch_employee_reviews()
    send_email(job_items, emp_alerts)
