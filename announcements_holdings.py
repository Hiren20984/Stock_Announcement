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

HISTORY_FILE = "sent_history.json"

# ---------- SAFE JSON ----------
def safe_json(r):
    try:
        txt = r.text.strip()
        if not txt or txt.startswith("<"):
            print(f"  -> API returned HTML, not JSON. Status {r.status_code}, first 200 chars: {txt[:200]}")
            return {}
        return r.json()
    except Exception as e:
        print(f"  -> JSON parse failed: {e}, text[:200]={r.text[:200]}")
        return {}

def normalize(s):
    return str(s or "").upper().replace(" ", "").replace("-", "").replace("&", "AND").replace(".", "").replace("LTD", "").replace("LIMITED", "")

try:
    # ---------- LOAD HOLDINGS ----------
    symbols = []
    if os.path.exists("holdings.json"):
        try:
            with open("holdings.json") as f:
                data = json.load(f)
                # holdings.json can be {"JSWCEMENT": {...}} or ["JSWCEMENT", ...]
                if isinstance(data, dict):
                    symbols = list(data.keys())
                elif isinstance(data, list):
                    symbols = data
        except Exception as e:
            print(f"holdings.json corrupt: {e}")
            symbols = []
    symbols = [str(s).upper().strip() for s in symbols if s]
    print(f"Tracking {len(symbols)} stocks: {symbols[:10]}")
    if len(symbols) == 0:
        print("No symbols, exit")
        sys.exit(0)

    # ---------- LOAD HISTORY - SAFE, NEVER CRASH ----------
    sent_history = {}
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                content = f.read().strip()
                if content:
                    loaded = json.loads(content)
                    if isinstance(loaded, dict):
                        sent_history = loaded
        except Exception as e:
            print(f"History corrupt, starting fresh: {e}")
            sent_history = {}
    else:
        print("History file not found, creating empty one")
        sent_history = {}

    cutoff = (now - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    sent_history = {k: v for k, v in sent_history.items() if isinstance(v, str) and v >= cutoff}
    print(f"History loaded: {len(sent_history)} ids (last 7 days)")

    def make_id(symbol, title, time_str):
        raw = f"{symbol}|{title}|{time_str}".upper().strip()
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    # ---------- HEADERS ----------
    BSE_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Referer": "https://www.bseindia.com/",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.bseindia.com",
        "Content-Type": "application/json",
    }
    NSE_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
    }

    # ---------- ANNOUNCEMENTS - FIXED: POST not GET ----------
    def get_bse_all():
        all_rows = []
        for d in [yesterday_str, today_str]:
            try:
                url = "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"
                payload = {
                    "strCat": "-1",
                    "strPrevDate": d,
                    "strScrip": "",
                    "strSearch": "P",
                    "strToDate": d,
                    "strType": "C",
                    "subcategory": "-1"
                }
                r = requests.post(url, headers=BSE_HEADERS, json=payload, timeout=30)
                print(f"BSE Ann {d}: status {r.status_code}")
                data = safe_json(r)
                rows = data.get("Table", []) if isinstance(data, dict) else []
                print(f"BSE Ann {d}: {len(rows)} rows")
                all_rows.extend(rows)
            except Exception as e:
                print(f"BSE Ann Error {d}:", e)
                traceback.print_exc()
        # Deduplicate by headline+date
        seen = set()
        uniq = []
        for row in all_rows:
            key = f"{row.get('HEADLINE','')}|{row.get('DT_TM','')}|{row.get('SCRIP_CD','')}"
            if key not in seen:
                seen.add(key)
                uniq.append(row)
        print(f"BSE Ann total unique: {len(uniq)}")
        return uniq

    def get_nse_ann():
        try:
            s = requests.Session()
            s.headers.update(NSE_HEADERS)
            # Get cookies first - must succeed
            s.get("https://www.nseindia.com", timeout=20)
            time.sleep(1)
            r = s.get("https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=", headers=NSE_HEADERS, timeout=30)
            print(f"NSE Ann: status {r.status_code}")
            data = safe_json(r)
            rows = data.get("data", []) if isinstance(data, dict) else []
            print(f"NSE Ann: {len(rows)} rows")
            return rows
        except Exception as e:
            print("NSE Ann Error:", e)
            traceback.print_exc()
            return []

    # ---------- BULK + BLOCK - FIXED HEADERS + SAFE ----------
    def get_nse_bulk_block():
        bulk, block = [], []
        try:
            s = requests.Session()
            s.headers.update(NSE_HEADERS)
            s.get("https://www.nseindia.com", timeout=20)
            time.sleep(1)
            try:
                r = s.get("https://www.nseindia.com/api/corporates-bulk-deals?index=equities", headers=NSE_HEADERS, timeout=30)
                print(f"NSE Bulk: status {r.status_code}")
                data = safe_json(r)
                bulk = data.get("data", []) if isinstance(data, dict) else []
                print(f"NSE Bulk: {len(bulk)} rows")
            except Exception as e:
                print("NSE Bulk Error:", e)
            try:
                r = s.get("https://www.nseindia.com/api/corporates-block-deals?index=equities", headers=NSE_HEADERS, timeout=30)
                print(f"NSE Block: status {r.status_code}")
                data = safe_json(r)
                block = data.get("data", []) if isinstance(data, dict) else []
                print(f"NSE Block: {len(block)} rows")
            except Exception as e:
                print("NSE Block Error:", e)
        except Exception as e:
            print("NSE Bulk/Block Session Error:", e)
        return bulk, block

    def get_bse_bulk_block():
        # BSE Bulk Deal API - also POST, not GET
        bulk = []
        try:
            for d in [yesterday_str, today_str]:
                url = "https://api.bseindia.com/BseIndiaAPI/api/BulkDealData/w"
                payload = {"strCat": "", "strPrevDate": d, "strScrip": "", "strSearch": "P", "strToDate": d, "strType": "C"}
                r = requests.post(url, headers=BSE_HEADERS, json=payload, timeout=25)
                print(f"BSE Bulk {d}: status {r.status_code}")
                data = safe_json(r)
                if isinstance(data, dict):
                    rows = data.get("Table", [])
                elif isinstance(data, list):
                    rows = data
                else:
                    rows = []
                print(f"BSE Bulk {d}: {len(rows)} rows")
                bulk.extend(rows)
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
            # SAFE - no [0] crash, encode ascii
            time_txt = str(a.get('time', '')).encode('ascii', 'ignore').decode()
            pdf.multi_cell(0, 7, f"{i}. [{a.get('source','')}] {time_txt}")
            pdf.set_font("Helvetica", "", 10)
            title_txt = str(a.get('title', '')).encode('ascii', 'ignore').decode()
            pdf.multi_cell(0, 6, title_txt)
            link = a.get('link') or ""
            if link:
                pdf.set_font("Helvetica", "", 8)
                pdf.set_text_color(0, 0, 255)
                pdf.multi_cell(0, 5, f"Link: {str(link)[:180]}")
                pdf.set_text_color(0, 0, 0)
            pdf.ln(4)
        pdf.output(filename)
        return filename

    def send_pdf(filename, symbol, count, deal_type="ANNOUNCEMENT"):
        if deal_type == "BULK":
            emoji, label = "📊", "BULK DEAL"
        elif deal_type == "BLOCK":
            emoji, label = "🧱", "BLOCK DEAL"
        else:
            emoji, label = "📢", "Announcement"
        caption = f"{emoji} {symbol} - {label}\n📅 {today_display} {now.strftime('%I:%M %p')}\n📄 {count} NEW {label}(s)"
        url = f"https://api.telegram.org/bot{TOKEN}/sendDocument"
        try:
            with open(filename, 'rb') as f:
                r = requests.post(url, data={"chat_id": CHAT_ID, "caption": caption}, files={"document": f}, timeout=90)
            print(f"Sent {symbol} {deal_type}: {r.status_code} {r.text[:300]}")
            return r.status_code == 200
        except Exception as e:
            print(f"Send PDF failed {symbol}: {e}")
            return False

    def send_text(msg):
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        try:
            r = requests.post(url, data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=20)
            print(f"Send text: {r.status_code}")
        except Exception as e:
            print(f"Send text failed: {e}")

    # ---- Fetch All ----
    bse_data = get_bse_all()
    nse_data = get_nse_ann()
    nse_bulk, nse_block = get_nse_bulk_block()
    bse_bulk = get_bse_bulk_block()
    print(f"TOTAL FETCHED -> BSE Ann: {len(bse_data)}, NSE Ann: {len(nse_data)}, NSE Bulk: {len(nse_bulk)}, NSE Block: {len(nse_block)}, BSE Bulk: {len(bse_bulk)}")

    sent_now = 0
    with_news = []
    new_ids = []

    for sym in symbols:
        sym_norm = normalize(sym)
        # For BSE matching, also keep first 6 chars as fallback, but NOT 4 (too loose)
        sym_prefix = sym_norm[:6] if len(sym_norm) >= 6 else sym_norm

        # --- 1. Announcements ---
        matched = []
        # BSE - match by company name
        for item in bse_data:
            if not isinstance(item, dict):
                continue
            name_norm = normalize(item.get("SLONGNAME", ""))
            # Match: symbol in name OR name starts with symbol prefix AND length check
            # Example: sym=BAZARSTYLE, name=BAZARSTYLERETAIL -> match
            # Example: sym=NLCINDIA, name=NLCINDIALIMITED -> match
            if not name_norm:
                continue
            if sym_norm in name_norm or (len(sym_prefix) >= 6 and name_norm.startswith(sym_prefix)):
                title = item.get("HEADLINE", "") or ""
                time_str = item.get("DT_TM", "") or ""
                uid = make_id(sym, title, time_str)
                if uid in sent_history:
                    continue
                attach = item.get("ATTACHMENTNAME", "") or ""
                link = f"https://www.bseindia.com/xml-data/corpfiling/AttachHis/{attach}" if attach else ""
                matched.append({"source": "BSE", "time": time_str, "title": title, "link": link, "uid": uid})

        # NSE - exact symbol match
        for item in nse_data:
            if not isinstance(item, dict):
                continue
            if str(item.get("symbol", "")).upper().strip() == sym:
                title = item.get("subject", "") or ""
                time_str = item.get("annDate", "") or ""
                uid = make_id(sym, title, time_str)
                if uid in sent_history:
                    continue
                matched.append({"source": "NSE", "time": time_str, "title": title, "link": item.get("attachment", "") or "", "uid": uid})

        if matched:
            pdf_file = make_pdf(sym, matched, "ANNOUNCEMENT")
            if send_pdf(pdf_file, sym, len(matched), "ANNOUNCEMENT"):
                for m in matched:
                    new_ids.append(m['uid'])
                with_news.append(f"{sym} Ann ({len(matched)})")
                sent_now += 1
            time.sleep(1.5)

        # --- 2. Bulk Deals ---
        bulk_matched = []
        for item in nse_bulk:
            if not isinstance(item, dict):
                continue
            if str(item.get("symbol", "")).upper().strip() == sym:
                client = item.get("clientName", "") or ""
                buy_sell = item.get("buySell", "") or ""
                qty = item.get("quantityTraded", "") or ""
                price = item.get("tradePrice", "") or ""
                date = item.get("date", "") or ""
                title = f"{buy_sell} - {client} - Qty: {qty} @ Rs {price}"
                uid = make_id(sym, title, date)
                if uid in sent_history:
                    continue
                bulk_matched.append({"source": "NSE BULK", "time": date, "title": title, "link": "", "uid": uid})
        for item in bse_bulk:
            if not isinstance(item, dict):
                continue
            sname = normalize(item.get("ScripName", "") or item.get("SLONGNAME", "") or "")
            if sname and (sym_norm in sname or sname.startswith(sym_prefix)):
                client = item.get("ClientName", "") or item.get("CLIENTNAME", "") or ""
                qty = item.get("Qty", "") or item.get("QUANTITY", "") or ""
                price = item.get("Price", "") or item.get("TRADEPRICE", "") or ""
                title = f"BULK - {client} - Qty: {qty} @ Rs {price}"
                time_str = str(item.get("DealDate", "") or item.get("DT_TM", "") or today_display)
                uid = make_id(sym, title, time_str)
                if uid in sent_history:
                    continue
                bulk_matched.append({"source": "BSE BULK", "time": time_str, "title": title, "link": "", "uid": uid})
        if bulk_matched:
            pdf_file = make_pdf(sym, bulk_matched, "BULK")
            if send_pdf(pdf_file, sym, len(bulk_matched), "BULK"):
                for m in bulk_matched:
                    new_ids.append(m['uid'])
                with_news.append(f"{sym} Bulk ({len(bulk_matched)})")
                sent_now += 1
            time.sleep(1.5)

        # --- 3. Block Deals ---
        block_matched = []
        for item in nse_block:
            if not isinstance(item, dict):
                continue
            if str(item.get("symbol", "")).upper().strip() == sym:
                client = item.get("clientName", "") or ""
                buy_sell = item.get("buySell", "") or ""
                qty = item.get("quantityTraded", "") or ""
                price = item.get("tradePrice", "") or ""
                date = item.get("date", "") or ""
                title = f"{buy_sell} - {client} - Qty: {qty} @ Rs {price}"
                uid = make_id(sym, title, date)
                if uid in sent_history:
                    continue
                block_matched.append({"source": "NSE BLOCK", "time": date, "title": title, "link": "", "uid": uid})
        if block_matched:
            pdf_file = make_pdf(sym, block_matched, "BLOCK")
            if send_pdf(pdf_file, sym, len(block_matched), "BLOCK"):
                for m in block_matched:
                    new_ids.append(m['uid'])
                with_news.append(f"{sym} Block ({len(block_matched)})")
                sent_now += 1
            time.sleep(1.5)

    for uid in new_ids:
        sent_history[uid] = now.strftime("%Y-%m-%d")
    # Save history - always, even if 0 new, to update cutoff
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(sent_history, f, indent=2)
        print(f"History saved: {len(sent_history)} ids")
    except Exception as e:
        print(f"History save failed: {e}")

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