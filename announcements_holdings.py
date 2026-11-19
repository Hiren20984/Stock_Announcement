import os, json, requests, datetime, pytz, time, hashlib, sys, traceback, csv, io
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

def safe_json(r):
    try:
        txt = r.text.strip()
        if not txt or txt.startswith("<"):
            print(f"  -> HTML not JSON. Status {r.status_code}, first 150: {txt[:150]}")
            return {}
        return r.json()
    except Exception as e:
        print(f"  -> JSON parse failed: {e}")
        return {}

def normalize(s):
    return str(s or "").upper().replace(" ", "").replace("-", "").replace("&", "AND").replace(".", "").replace("LTD", "").replace("LIMITED", "")

# Browser headers that Akamai / NSE actually accept
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,text/plain,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Cache-Control": "max-age=0",
}

try:
    symbols = []
    if os.path.exists("holdings.json"):
        try:
            with open("holdings.json") as f:
                data = json.load(f)
                symbols = list(data.keys()) if isinstance(data, dict) else data
        except Exception as e:
            print(f"holdings.json corrupt: {e}")
    symbols = [str(s).upper().strip() for s in symbols if s]
    print(f"Tracking {len(symbols)} stocks: {symbols[:10]}")
    if len(symbols) == 0:
        print("No symbols, exit")
        sys.exit(0)

    sent_history = {}
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                c = f.read().strip()
                if c:
                    loaded = json.loads(c)
                    if isinstance(loaded, dict):
                        sent_history = loaded
        except:
            sent_history = {}
    cutoff = (now - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    sent_history = {k: v for k, v in sent_history.items() if isinstance(v, str) and v >= cutoff}
    print(f"History loaded: {len(sent_history)} ids")

    def make_id(symbol, title, time_str):
        return hashlib.md5(f"{symbol}|{title}|{time_str}".upper().strip().encode()).hexdigest()[:12]

    # ---------- BSE - WITH SESSION + RETRY + COOKIES ----------
    def get_bse_all():
        all_rows = []
        for d in [yesterday_str, today_str]:
            success = False
            for attempt in range(3):
                try:
                    s = requests.Session()
                    s.headers.update(BROWSER_HEADERS)
                    # Step 1: Get cookies from homepage first - Akamai needs this
                    try:
                        s.get("https://www.bseindia.com/", timeout=20)
                        time.sleep(1)
                    except:
                        pass
                    # Step 2: POST with session cookies
                    url = "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"
                    payload = {"strCat": "-1", "strPrevDate": d, "strScrip": "", "strSearch": "P", "strToDate": d, "strType": "C", "subcategory": "-1"}
                    headers2 = dict(BROWSER_HEADERS)
                    headers2.update({"Referer": "https://www.bseindia.com/corporates/ann.html", "Origin": "https://www.bseindia.com", "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest"})
                    r = s.post(url, headers=headers2, json=payload, timeout=30)
                    print(f"BSE Ann {d} attempt {attempt+1}: status {r.status_code}")
                    if r.status_code == 200:
                        data = safe_json(r)
                        rows = data.get("Table", []) if isinstance(data, dict) else []
                        print(f"BSE Ann {d}: {len(rows)} rows")
                        all_rows.extend(rows)
                        success = True
                        break
                    elif r.status_code == 403:
                        print(f"BSE 403 - Akamai blocked, retry in 3s...")
                        time.sleep(3)
                        continue
                    else:
                        print(f"BSE unexpected {r.status_code}: {r.text[:200]}")
                        break
                except Exception as e:
                    print(f"BSE Ann Error {d} attempt {attempt+1}: {e}")
                    time.sleep(2)
            if not success:
                print(f"BSE Ann {d}: FAILED after 3 attempts - will try NSE only")
        # dedup
        seen = set(); uniq = []
        for row in all_rows:
            k = f"{row.get('HEADLINE','')}|{row.get('DT_TM','')}|{row.get('SCRIP_CD','')}"
            if k not in seen:
                seen.add(k); uniq.append(row)
        print(f"BSE Ann total unique: {len(uniq)}")
        return uniq

    # ---------- NSE ANNOUNCEMENTS - TRY 3 ENDPOINTS ----------
    def get_nse_ann():
        endpoints = [
            "https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=",
            "https://www.nseindia.com/api/corporates-announcements?index=equities&symbol=",
            "https://www.nseindia.com/api/corporate-filings-announcements?index=equities&symbol=",
        ]
        for url in endpoints:
            try:
                s = requests.Session()
                s.headers.update(BROWSER_HEADERS)
                s.headers.update({"Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements"})
                try:
                    s.get("https://www.nseindia.com", timeout=20)
                    time.sleep(1)
                except:
                    pass
                r = s.get(url, timeout=30)
                print(f"NSE Ann try {url[:60]}: status {r.status_code}")
                if r.status_code == 200:
                    data = safe_json(r)
                    rows = data.get("data", []) if isinstance(data, dict) else []
                    print(f"NSE Ann SUCCESS: {len(rows)} rows from {url}")
                    return rows
                else:
                    print(f"NSE Ann failed {r.status_code}, trying next endpoint...")
                    continue
            except Exception as e:
                print(f"NSE Ann Error {url}: {e}")
                continue
        print("NSE Ann: ALL endpoints failed - NSE blocked from GitHub IP")
        return []

    # ---------- BULK / BLOCK - USE NSE ARCHIVES CSV (NOT BLOCKED) ----------
    def get_nse_bulk_block_from_archives():
        bulk, block = [], []
        # NSE publishes daily CSVs here - direct file download, not API
        for d_obj, label in [(now, "today"), (yesterday, "yesterday")]:
            d_fmt = d_obj.strftime("%d%m%Y")  # e.g. 20112026
            d_iso = d_obj.strftime("%Y-%m-%d")
            # Bulk Deals CSV
            for kind, lst in [("bulk", bulk), ("block", block)]:
                try:
                    # Try 2 URL patterns - NSE changes pattern sometimes
                    urls = []
                    if kind == "bulk":
                        urls = [
                            f"https://archives.nseindia.com/archives/equities/bulk_deals/bulk_deals_{d_fmt}.csv",
                            f"https://archives.nseindia.com/archives/equities/bulk_deals/bulk_deal_{d_fmt}.csv",
                        ]
                    else:
                        urls = [
                            f"https://archives.nseindia.com/archives/equities/block_deals/block_deals_{d_fmt}.csv",
                            f"https://archives.nseindia.com/archives/equities/block_deals/block_deal_{d_fmt}.csv",
                        ]
                    for url in urls:
                        try:
                            r = requests.get(url, headers=BROWSER_HEADERS, timeout=25)
                            print(f"NSE {kind.upper()} Archive {label} {url.split('/')[-1]}: status {r.status_code} len {len(r.text)}")
                            if r.status_code == 200 and len(r.text) > 100 and "Symbol" in r.text:
                                # Parse CSV
                                reader = csv.DictReader(io.StringIO(r.text))
                                count = 0
                                for row in reader:
                                    # CSV columns vary: Symbol, Security Name, Client Name, Buy / Sell, Quantity Traded, Trade Price, Date
                                    sym_val = (row.get("Symbol") or row.get("SYMBOL") or "").strip().upper()
                                    if not sym_val:
                                        continue
                                    # Normalize row to same format as API
                                    lst.append({
                                        "symbol": sym_val,
                                        "clientName": row.get("Client Name") or row.get("ClientName") or row.get("CLIENT NAME") or "",
                                        "buySell": row.get("Buy / Sell") or row.get("Buy/Sell") or row.get("BUY/SELL") or "",
                                        "quantityTraded": row.get("Quantity Traded") or row.get("Quantity") or "",
                                        "tradePrice": row.get("Trade Price") or row.get("Price") or "",
                                        "date": row.get("Date") or d_iso,
                                    })
                                    count += 1
                                print(f"NSE {kind.upper()} Archive {label}: parsed {count} rows")
                                break  # success, don't try 2nd URL
                            else:
                                print(f"  -> Not a valid CSV, trying next URL pattern")
                        except Exception as e:
                            print(f"NSE {kind} Archive Error {url}: {e}")
                            continue
                except Exception as e:
                    print(f"NSE {kind} Archive outer error: {e}")
        print(f"NSE Bulk Archive total: {len(bulk)}, Block Archive total: {len(block)}")
        return bulk, block

    def get_nse_bulk_block_api():
        # Fallback - try API if archives failed
        bulk, block = [], []
        try:
            s = requests.Session()
            s.headers.update(BROWSER_HEADERS)
            try:
                s.get("https://www.nseindia.com", timeout=20)
                time.sleep(1)
            except:
                pass
            for kind, url in [("bulk", "https://www.nseindia.com/api/corporates-bulk-deals?index=equities"), ("block", "https://www.nseindia.com/api/corporates-block-deals?index=equities")]:
                try:
                    r = s.get(url, timeout=30)
                    print(f"NSE {kind} API: status {r.status_code}")
                    if r.status_code == 200:
                        data = safe_json(r)
                        rows = data.get("data", []) if isinstance(data, dict) else []
                        print(f"NSE {kind} API: {len(rows)} rows")
                        if kind == "bulk":
                            bulk = rows
                        else:
                            block = rows
                except Exception as e:
                    print(f"NSE {kind} API Error: {e}")
        except Exception as e:
            print(f"NSE Bulk/Block API Session Error: {e}")
        return bulk, block

    def get_bse_bulk_block():
        # BSE Bulk - try same session method, if 403 skip (NSE archives cover most)
        bulk = []
        try:
            for d in [yesterday_str, today_str]:
                s = requests.Session()
                s.headers.update(BROWSER_HEADERS)
                try:
                    s.get("https://www.bseindia.com/", timeout=15)
                    time.sleep(1)
                except:
                    pass
                url = "https://api.bseindia.com/BseIndiaAPI/api/BulkDealData/w"
                payload = {"strCat": "", "strPrevDate": d, "strScrip": "", "strSearch": "P", "strToDate": d, "strType": "C"}
                r = s.post(url, headers={"Referer": "https://www.bseindia.com/", "Content-Type": "application/json", **BROWSER_HEADERS}, json=payload, timeout=25)
                print(f"BSE Bulk {d}: status {r.status_code}")
                if r.status_code == 200:
                    data = safe_json(r)
                    rows = data.get("Table", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
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
        title_h = f"{symbol} - BULK DEAL" if deal_type == "BULK" else (f"{symbol} - BLOCK DEAL" if deal_type == "BLOCK" else f"{symbol} - Announcements")
        pdf.cell(0, 10, title_h, new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 8, f"Date: {today_display} | New: {len(items)}", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.ln(5)
        for i, a in enumerate(items, 1):
            pdf.set_font("Helvetica", "B", 11)
            pdf.multi_cell(0, 7, f"{i}. [{a.get('source','')}] {str(a.get('time','')).encode('ascii','ignore').decode()}")
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 6, str(a.get('title','')).encode('ascii','ignore').decode())
            if a.get('link'):
                pdf.set_font("Helvetica", "", 8)
                pdf.set_text_color(0,0,255)
                pdf.multi_cell(0, 5, f"Link: {str(a['link'])[:180]}")
                pdf.set_text_color(0,0,0)
            pdf.ln(4)
        pdf.output(filename)
        return filename

    def send_pdf(filename, symbol, count, deal_type="ANNOUNCEMENT"):
        emoji, label = ("📊", "BULK DEAL") if deal_type == "BULK" else (("🧱", "BLOCK DEAL") if deal_type == "BLOCK" else ("📢", "Announcement"))
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
        try:
            r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=20)
            print(f"Send text: {r.status_code}")
        except Exception as e:
            print(f"Send text failed: {e}")

    # ---- Fetch All ----
    bse_data = get_bse_all()
    nse_data = get_nse_ann()
    # Bulk/Block: try Archives FIRST (not blocked), API as fallback
    nse_bulk, nse_block = get_nse_bulk_block_from_archives()
    if len(nse_bulk) == 0 and len(nse_block) == 0:
        print("Archives gave 0, trying NSE API as fallback...")
        nse_bulk, nse_block = get_nse_bulk_block_api()
    bse_bulk = get_bse_bulk_block()
    print(f"TOTAL FETCHED -> BSE Ann: {len(bse_data)}, NSE Ann: {len(nse_data)}, NSE Bulk: {len(nse_bulk)}, NSE Block: {len(nse_block)}, BSE Bulk: {len(bse_bulk)}")

    sent_now = 0
    with_news = []
    new_ids = []

    for sym in symbols:
        sym_norm = normalize(sym)
        sym_prefix = sym_norm[:6] if len(sym_norm) >= 6 else sym_norm
        matched = []
        for item in bse_data:
            if not isinstance(item, dict):
                continue
            name_norm = normalize(item.get("SLONGNAME", ""))
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

        bulk_matched = []
        for item in nse_bulk:
            if not isinstance(item, dict):
                continue
            if str(item.get("symbol", "")).upper().strip() == sym:
                title = f"{item.get('buySell','')} - {item.get('clientName','')} - Qty: {item.get('quantityTraded','')} @ Rs {item.get('tradePrice','')}"
                uid = make_id(sym, title, str(item.get("date","")))
                if uid in sent_history:
                    continue
                bulk_matched.append({"source": "NSE BULK", "time": str(item.get("date","")), "title": title, "link": "", "uid": uid})
        for item in bse_bulk:
            if not isinstance(item, dict):
                continue
            sname = normalize(item.get("ScripName", "") or item.get("SLONGNAME", "") or "")
            if sname and (sym_norm in sname or sname.startswith(sym_prefix)):
                title = f"BULK - {item.get('ClientName','') or item.get('CLIENTNAME','')} - Qty: {item.get('Qty','') or item.get('QUANTITY','')} @ Rs {item.get('Price','') or item.get('TRADEPRICE','')}"
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

        block_matched = []
        for item in nse_block:
            if not isinstance(item, dict):
                continue
            if str(item.get("symbol", "")).upper().strip() == sym:
                title = f"{item.get('buySell','')} - {item.get('clientName','')} - Qty: {item.get('quantityTraded','')} @ Rs {item.get('tradePrice','')}"
                uid = make_id(sym, title, str(item.get("date","")))
                if uid in sent_history:
                    continue
                block_matched.append({"source": "NSE BLOCK", "time": str(item.get("date","")), "title": title, "link": "", "uid": uid})
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
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(sent_history, f, indent=2)
        print(f"History saved: {len(sent_history)} ids")
    except Exception as e:
        print(f"History save failed: {e}")

    if sent_now == 0:
        print("No NEW - Silent, no Telegram message")
        # If BOTH sources blocked, tell you - don't stay silent
        if len(bse_data) == 0 and len(nse_data) == 0:
            print("WARNING: BOTH BSE + NSE blocked (0 fetched) - will retry next run, not marking as sent")
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