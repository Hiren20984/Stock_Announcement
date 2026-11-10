import os, json, requests, datetime, pytz

TOKEN = os.getenv("TELEGRAM_TOKEN_HOLDINGS")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID_HOLDINGS")

IST = pytz.timezone("Asia/Kolkata")
now = datetime.datetime.now(IST)
today_display = now.strftime("%d %b %Y")

# Load holdings count
symbols = []
if os.path.exists("holdings.json"):
    with open("holdings.json") as f:
        symbols = list(json.load(f).keys())

# Load sent history - count last 7 days
sent_last_7 = 0
total_history = 0
if os.path.exists("sent_history.json"):
    with open("sent_history.json") as f:
        history = json.load(f)
    total_history = len(history)
    cutoff = (now - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    sent_last_7 = sum(1 for v in history.values() if v >= cutoff)

# Send weekly summary
msg = f"""📊 <b>Weekly Holdings Bot Report</b>
📅 {today_display} - Sunday 9:00 AM

✅ <b>Status:</b> Bot is ALIVE and running
👀 <b>Stocks Tracked:</b> {len(symbols)} stocks
📄 <b>PDFs Sent This Week:</b> {sent_last_7} announcements
🗂️ <b>Total in History:</b> {total_history} announcements

⏰ <b>Schedule:</b>
• Daily 7:00 PM - Main announcements
• Daily 10:00 PM - Late evening catch-up
• Daily 8:00 AM - Morning catch-up
• Sunday 9:00 AM - This weekly report

🔕 No news = No message (Silent mode ON)
Next report: Next Sunday 9 AM"""

url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
r = requests.post(url, data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=20)
print(f"Weekly summary sent: {r.status_code}")
