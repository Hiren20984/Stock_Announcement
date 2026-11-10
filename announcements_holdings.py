import os, json, requests, datetime, pytz, time, hashlib, sys, traceback
from fpdf import FPDF

TOKEN = os.getenv("TELEGRAM_TOKEN_HOLDINGS")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID_HOLDINGS")

print(f"DEBUG: TOKEN exists? {bool(TOKEN)}")
print(f"DEBUG: CHAT_ID: {CHAT_ID}")

if not TOKEN or not CHAT_ID:
    print("ERROR: Secrets missing!")
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

    # ---------- ANNOUNCEMENTS - OLD CODE ----------
    def get_bse_all():
        all_rows = []
        for d in [yesterday_str, today_str]:
            try:
                url = f"https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w?strCat=-1&strPrevDate={d}&strScrip=&strSearch=P&strToDate={d}&strType=C"
                r = requests.get(url, headers=headers, timeout=25)
                all_rows.extend(r.json().get("Table", []))
            except Exception as e:
                print(f"BSE Ann Error {d}:", e)
        return all_rows

    def get_nse_ann():
        try:
            s = requests.Session()
            s.headers.update(headers)
            s.get("https://www.nseindia.com", timeout=20)
            r = s.get("https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=", timeout=25)
            return r.json().get("data", [])
        except Exception as e:
            print("NSE Ann Error:", e)
            return []

    # ---------- NEW: BULK + BLOCK DEALS ----------
    def get_nse_bulk_block():
        bulk = []
        block = []
        try:
            s = requests.Session()
            s.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            s.get("https://www.nseindia.com", timeout=20)
            # Bulk Deals
            try:
                r = s.get("https://www.nseindia.com/api/corporates-bulk-deals?index=equities", timeout=25)
                bulk = r.json().get("data", [])
                print(f"NSE Bulk: {len(bulk)} rows")
            except Exception as e:
                print("NSE Bulk Error:", e)
            # Block Deals
            try:
                r = s.get("https://www.nseindia.com/api/corporates-block-deals?index=equities", timeout=25)
                block = r.json().get("data", [])
                print(f"NSE Block: {len(block)} rows")
            except Exception as e:
                print("NSE Block Error:", e)
        except Exception as e:
            print("NSE Bulk/Block Session Error:", e)
        return bulk, block

    def get_bse_bulk_block():
        # BSE Bulk deals - try API, if fails return empty (NSE covers most)
        bulk = []
        try:
            for d in [yesterday_str, today_str]:
                url = f"https://api.bseindia.com/BseIndiaAPI/api/BulkDealData/w?strCat=&strPrevDate={d}&strScrip=&strSearch=P&strToDate={d}&strType=C"
                r = requests.get(url, headers=headers, timeout=20)
                data = r.json()
                if isinstance(data, dict):
                    bulk.extend(data.get("Table", []))
                elif isinstance(data, list):
                    bulk.extend(data)
            print(f"BSE Bulk rows: {len(bulk)}")
        except Exception as e:
            print("BSE Bulk Error:", e)
        return bulk

    def make_pdf(symbol, items, deal_type="ANNOUNCEMENT"):
        filename = f"{symbol}_{deal_type}_{now.strftime('%d%b%Y_%H%M')}.pdf"
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 16)
        if deal_type == "BULK":
            title_h = f"{symbol} - BULK DEAL"
        elif deal_type == "BLOCK":
            title_h = f"{symbol} - BLOCK DEAL"
        else:
            title_h = f"{symbol} - Announcements"
        pdf.cell(0, 10, title_h, new_x="LMARGIN", new_y="NEXT", align="C")
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

    def send_pdf(filename, symbol, count, deal_type="ANNOUNCEMENT"):
        if deal_type == "BULK":
            emoji = "📊"
            label = "BULK DEAL"
        elif deal_type == "BLOCK":
            emoji = "🧱"
            label = "BLOCK DEAL"
        else:
            emoji = "📢"
            label = "Announcement"
        caption = f"{emoji} {symbol} - {label}\n📅 {today_display} {now.strftime('%I:%M %p')}\n📄 {count} NEW {label}(s)"
        url = f"https://api.telegram.org/bot{TOKEN}/sendDocument"
        with open(filename, 'rb') as f:
            r = requests.post(url, data={"chat_id": CHAT_ID, "caption": caption}, files={"document": f}, timeout=60)
        print(f"Sent {symbol} {deal_type}: {r.status_code}")

    def send_text(msg):
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        r = requests.post(url, data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=20)
        print(f"Send text: {r.status_code}")

    # ---- Fetch All ----
    bse_data = get_bse_all()
    nse_data = get_nse_ann()
    nse_bulk, nse_block = get_nse_bulk_block()
    bse_bulk = get_bse_bulk_block()
    print(f"BSE Ann: {len(bse_data)}, NSE Ann: {len(nse_data)}, NSE Bulk: {len(nse_bulk)}, NSE Block: {len(nse_block)}")

    sent_now = 0
    with_news = []
    new_ids = []

    for sym in symbols:
        # --- 1. Announcements ---
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
            pdf_file = make_pdf(sym, matched, "ANNOUNCEMENT")
            send_pdf(pdf_file, sym, len(matched), "ANNOUNCEMENT")
            for m in matched:
                new_ids.append(m['uid'])
            with_news.append(f"{sym} Ann ({len(matched)})")
            sent_now += 1
            time.sleep(1.5)

        # --- 2. Bulk Deals (NSE) ---
        bulk_matched = []
        for item in nse_bulk:
            if str(item.get("symbol","")).upper() == sym:
                client = item.get("clientName","")
                buy_sell = item.get("buySell","")
                qty = item.get("quantityTraded","")
                price = item.get("tradePrice","")
                date = item.get("date","")
                title = f"{buy_sell} - {client} - Qty: {qty} @ Rs {price}"
                uid = make_id(sym, title, date)
                if uid in sent_history:
                    continue
                bulk_matched.append({"source":"NSE BULK","time":date,"title":title,"link":"","uid":uid})
        # BSE Bulk (if format matches)
        for item in bse_bulk:
            # BSE bulk fields vary, try common keys
            sname = str(item.get("ScripName","") or item.get("SLONGNAME","") or "").upper()
            if sym.replace("-","") in sname.replace(" ","") or sname and sym[:4] in sname:
                client = item.get("ClientName","") or item.get("CLIENTNAME","")
                qty = item.get("Qty","") or item.get("QUANTITY","")
                price = item.get("Price","") or item.get("TRADEPRICE","")
                title = f"BULK - {client} - Qty: {qty} @ Rs {price}"
                time_str = item.get("DealDate","") or item.get("DT_TM","") or today_display
                uid = make_id(sym, title, str(time_str))
                if uid in sent_history:
                    continue
                bulk_matched.append({"source":"BSE BULK","time":str(time_str),"title":title,"link":"","uid":uid})
        if bulk_matched:
            pdf_file = make_pdf(sym, bulk_matched, "BULK")
            send_pdf(pdf_file, sym, len(bulk_matched), "BULK")
            for m in bulk_matched:
                new_ids.append(m['uid'])
            with_news.append(f"{sym} Bulk ({len(bulk_matched)})")
            sent_now += 1
            time.sleep(1.5)

        # --- 3. Block Deals (NSE) ---
        block_matched = []
        for item in nse_block:
            if str(item.get("symbol","")).upper() == sym:
                client = item.get("clientName","")
                buy_sell = item.get("buySell","")
                qty = item.get("quantityTraded","")
                price = item.get("tradePrice","")
                date = item.get("date","")
                title = f"{buy_sell} - {client} - Qty: {qty} @ Rs {price}"
                uid = make_id(sym, title, date)
                if uid in sent_history:
                    continue
                block_matched.append({"source":"NSE BLOCK","time":date,"title":title,"link":"","uid":uid})
        if block_matched:
            pdf_file = make_pdf(sym, block_matched, "BLOCK")
            send_pdf(pdf_file, sym, len(block_matched), "BLOCK")
            for m in block_matched:
                new_ids.append(m['uid'])
            with_news.append(f"{sym} Block ({len(block_matched)})")
            sent_now += 1
            time.sleep(1.5)

    for uid in new_ids:
        sent_history[uid] = now.strftime("%Y-%m-%d")
    with open(HISTORY_FILE, "w") as f:
        json.dump(sent_history, f, indent=2)

    if sent_now == 0:
        print("No NEW - Silent, no Telegram message")
    else:
        send_text(f"✅ <b>Done - {today_display} {now.strftime('%I:%M %p')}</b>\n\nNew PDFs sent: {sent_now}\nStocks: {', '.join(with_news)}")

except Exception as e:
    print("FATAL ERROR:")
    traceback.print_exc()
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id": CHAT_ID, "text": f"❌ Holdings Bot Error:\n{str(e)[:500]}"}, timeout=20)
    except:
        pass
    sys.exit(0)
