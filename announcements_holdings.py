import os, json, requests, datetime, pytz, time, hashlib, sys, traceback
from fpdf import FPDF

TOKEN = os.getenv("TELEGRAM_TOKEN_HOLDINGS")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID_HOLDINGS")

print(f"DEBUG: TOKEN exists? {bool(TOKEN)} - Length: {len(TOKEN) if TOKEN else 0}")
print(f"DEBUG: CHAT_ID exists? {bool(CHAT_ID)} - Value: {CHAT_ID}")
print(f"DEBUG: Files in folder: {os.listdir('.')}")

if not TOKEN or not CHAT_ID:
    print("ERROR: Secrets TELEGRAM_TOKEN_HOLDINGS or TELEGRAM_CHAT_ID_HOLDINGS are MISSING in GitHub Secrets!")
    print("Go to Settings -> Secrets -> Actions and add them. Stopping safely.")
    sys.exit(0)

IST = pytz.timezone("Asia/Kolkata")
now = datetime.datetime.now(IST)
today_display = now.strftime("%d %b %Y")
today_str = now.strftime("%Y%m%d")
yesterday = now - datetime.timedelta(days=1)
yesterday_str = yesterday.strftime("%Y%m%d")

headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
HISTORY_FILE = "sent_history.json"

try:
    symbols = []
    if os.path.exists("holdings.json"):
        with open("holdings.json") as f:
            symbols = list(json.load(f).keys())
    print(f"Tracking {len(symbols)} stocks")
    if len(symbols) == 0:
        print("ERROR: holdings.json is empty or not found!")
        sys.exit(0)

    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE) as f:
            sent_history = json.load(f)
    else:
        sent_history = {}
    cutoff = (now - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    sent_history = {k: v for k, v in sent_history.items() if v >= cutoff}

    def make_id(symbol, title, time_str):
        raw = f"{symbol}|{title}|{time_str}".upper().strip()
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    def get_bse_all():
        all_rows = []
        for d in [yesterday_str, today_str]:
            try:
                url = f"https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w?strCat=-1&strPrevDate={d}&strScrip=&strSearch=P&strToDate={d}&strType=C"
                r = requests.get(url, headers=headers, timeout=25)
                all_rows.extend(r.json().get("Table", []))
            except Exception as e:
                print(f"BSE Error {d}:", e)
        return all_rows

    def get_nse_all():
        try:
            s = requests.Session()
            s.headers.update(headers)
            s.get("https://www.nseindia.com", timeout=20)
            r = s.get("https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=", timeout=25)
            return r.json().get("data", [])
        except Exception as e:
            print("NSE Error:", e)
            return []

    def make_pdf(symbol, items):
        filename = f"{symbol}_{now.strftime('%d%b%Y_%H%M')}.pdf"
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 10, f"{symbol} - Announcements", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 8, f"Date: {today_display} | New: {len(items)}", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.ln(5)
        for i, a in enumerate(items, 1):
            pdf.set_font("Helvetica", "B", 11)
            pdf.multi_cell(0, 7, f"{i}. [{a['source']}] {str(a['time']).encode('ascii','ignore').decode()}")
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 6, str(a['title']).encode('ascii','ignore').decode())
            if a.get('link'):
                pdf.set_font("Helvetica", "", 8)
                pdf.set_text_color(0,0,255)
                pdf.multi_cell(0, 5, f"Link: {str(a['link'])[:150]}")
                pdf.set_text_color(0,0,0)
            pdf.ln(4)
        pdf.output(filename)
        return filename

    def send_pdf(filename, symbol, count):
        caption = f"📢 {symbol}\n📅 {today_display} {now.strftime('%I:%M %p')}\n📄 {count} NEW Announcement(s)"
        url = f"https://api.telegram.org/bot{TOKEN}/sendDocument"
        with open(filename, 'rb') as f:
            r = requests.post(url, data={"chat_id": CHAT_ID, "caption": caption}, files={"document": f}, timeout=60)
        print(f"Sent {symbol}: {r.status_code} {r.text[:200]}")

    def send_text(msg):
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        r = requests.post(url, data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=20)
        print(f"Send text: {r.status_code} {r.text[:300]}")

    bse_data = get_bse_all()
    nse_data = get_nse_all()
    print(f"BSE: {len(bse_data)}, NSE: {len(nse_data)}")
    sent_now = 0
    with_news = []
    new_ids = []
    for sym in symbols:
        matched = []
        for item in bse_data:
            name = str(item.get("SLONGNAME","")).upper().replace(" ","").replace("&","AND")
            sym_clean = sym.replace("-","").replace(" ","")
            if sym_clean in name or sym_clean[:4] in name:
                title = item.get("HEADLINE","")
                time_str = item.get("DT_TM","")
                uid = make_id(sym, title, time_str)
                if uid in sent_history:
                    continue
                link = "https://www.bseindia.com/xml-data/corpfiling/AttachHis/" + item.get("ATTACHMENTNAME","") if item.get("ATTACHMENTNAME") else ""
                matched.append({"source":"BSE","time":time_str,"title":title,"link":link,"uid":uid})
        for item in nse_data:
            if str(item.get("symbol","")).upper() == sym:
                title = item.get("subject","")
                time_str = item.get("annDate","")
                uid = make_id(sym, title, time_str)
                if uid in sent_history:
                    continue
                matched.append({"source":"NSE","time":time_str,"title":title,"link":item.get("attachment",""),"uid":uid})
        if matched:
            pdf_file = make_pdf(sym, matched)
            send_pdf(pdf_file, sym, len(matched))
            for m in matched:
                new_ids.append(m['uid'])
            with_news.append(f"{sym} ({len(matched)})")
            sent_now += 1
            time.sleep(1.5)

    for uid in new_ids:
        sent_history[uid] = now.strftime("%Y-%m-%d")
    with open(HISTORY_FILE, "w") as f:
        json.dump(sent_history, f, indent=2)

    if sent_now == 0:
        send_text(f"✅ <b>Holdings Check - {today_display} {now.strftime('%I:%M %p')}</b>\n\nNo NEW announcements since last check.\n\nChecked {len(symbols)} stocks.")
    else:
        send_text(f"✅ <b>Done - {today_display} {now.strftime('%I:%M %p')}</b>\n\nNew PDFs sent: {sent_now}\nStocks: {', '.join(with_news)}")

except Exception as e:
    print("FATAL ERROR:")
    traceback.print_exc()
    # Send error to Telegram so you see it on phone
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id": CHAT_ID, "text": f"❌ Holdings Bot Error:\n{str(e)[:500]}"}, timeout=20)
    except:
        pass
    sys.exit(0)
