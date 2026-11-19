import os, json, requests, datetime, pytz, time, hashlib, sys, traceback, re, csv, io
from fpdf import FPDF
try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except:
    HAS_BS4 = False
    print("bs4 not installed, will install via requirements")

TOKEN = os.getenv("TELEGRAM_TOKEN_HOLDINGS")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID_HOLDINGS")
print(f"DEBUG: TOKEN exists? {bool(TOKEN)}")
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

def safe_json_text(txt):
    try:
        txt = txt.strip()
        if not txt or txt.startswith("<"):
            return {}
        # AllOrigins wraps JSON in {"contents": "..."}
        if '"contents"' in txt[:200]:
            try:
                outer = json.loads(txt)
                inner = outer.get("contents", "")
                if isinstance(inner, str):
                    return json.loads(inner)
                return inner
            except:
                pass
        return json.loads(txt)
    except Exception as e:
        print(f"  -> JSON parse failed: {e}, txt[:200]={txt[:200]}")
        return {}

def normalize(s):
    return str(s or "").upper().replace(" ", "").replace("-", "").replace("&", "AND").replace(".", "").replace("LTD", "").replace("LIMITED", "")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/",
}

# ---------- PROXY HELPERS - THIS IS THE FIX ----------
def fetch_via_proxy(target_url):
    """Try direct first, then 2 public proxies. Returns (status, text)"""
    # 1. Direct
    try:
        r = requests.get(target_url, headers=HEADERS, timeout=25)
        if r.status_code == 200 and len(r.text) > 100 and "Access Denied" not in r.text and "Resource not found" not in r.text:
            print(f"  DIRECT OK: {target_url[:70]} -> {r.status_code} len {len(r.text)}")
            return r.status_code, r.text
        print(f"  DIRECT blocked: {target_url[:70]} -> {r.status_code} len {len(r.text)}")
    except Exception as e:
        print(f"  DIRECT error: {e}")
    # 2. AllOrigins proxy - GET
    for proxy in [
        f"https://api.allorigins.win/raw?url={requests.utils.quote(target_url, safe='')}",
        f"https://corsproxy.io/?url={requests.utils.quote(target_url, safe='')}",
    ]:
        try:
            print(f"  Trying proxy: {proxy[:60]}...")
            r = requests.get(proxy, headers=HEADERS, timeout=30)
            print(f"  PROXY -> {r.status_code} len {len(r.text)}")
            if r.status_code == 200 and len(r.text) > 100 and "Access Denied" not in r.text:
                return r.status_code, r.text
        except Exception as e:
            print(f"  PROXY error: {e}")
            continue
    return 0, ""

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
    print(f"Tracking {len(symbols)} stocks")
    if len(symbols) == 0:
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

    # ---------- 1. NSE ANNOUNCEMENTS VIA PROXY ----------
    def get_nse_ann():
        url = "https://www.nseindia.com/api/corporate-announcements?index=equities&symbol="
        status, txt = fetch_via_proxy(url)
        if status == 200:
            data = safe_json_text(txt)
            rows = data.get("data", []) if isinstance(data, dict) else []
            print(f"NSE Ann VIA PROXY: {len(rows)} rows")
            return rows
        print("NSE Ann: proxy also failed")
        return []

    # ---------- 2. BSE ANNOUNCEMENTS VIA MONEYCONTROL RSS + PROXY ----------
    def get_bse_all_via_moneycontrol():
        # Moneycontrol publishes all BSE announcements in RSS, not blocked
        # We fetch Moneycontrol company announcements search via proxy
        all_rows = []
        # Try Moneycontrol announcements API via proxy
        # Fallback: Use BSE via proxy GET (BSE API also accepts GET with query params when via proxy)
        for d in [yesterday_str, today_str]:
            # BSE GET version - works when not blocked
            bse_url = f"https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w?strCat=-1&strPrevDate={d}&strScrip=&strSearch=P&strToDate={d}&strType=C"
            status, txt = fetch_via_proxy(bse_url)
            if status == 200:
                data = safe_json_text(txt)
                rows = data.get("Table", []) if isinstance(data, dict) else []
                print(f"BSE Ann {d} VIA PROXY: {len(rows)} rows")
                all_rows.extend(rows)
            else:
                print(f"BSE Ann {d} VIA PROXY failed")
        # Deduplicate
        seen = set(); uniq = []
        for row in all_rows:
            k = f"{row.get('HEADLINE','')}|{row.get('DT_TM','')}|{row.get('SCRIP_CD','')}"
            if k not in seen:
                seen.add(k); uniq.append(row)
        print(f"BSE Ann total unique via proxy: {len(uniq)}")
        return uniq

    # ---------- 3. BULK / BLOCK VIA CHITTORGARH (NOT BLOCKED) ----------
    def get_bulk_block_via_chittorgarh():
        bulk, block = [], []
        if not HAS_BS4:
            print("bs4 missing, cannot parse Chittorgarh")
            return bulk, block
        for d_obj in [now, yesterday]:
            d_str = d_obj.strftime("%Y-%m-%d")  # 2026-11-20
            d_display = d_obj.strftime("%d %b %Y")
            for kind in ["bulk-deals", "block-deals"]:
                url = f"https://www.chittorgarh.com/report/{kind}/{d_str}/"
                print(f"Chittorgarh {kind} {d_str}: fetching {url}")
                try:
                    r = requests.get(url, headers=HEADERS, timeout=25)
                    print(f"  Chittorgarh {kind}: status {r.status_code} len {len(r.text)}")
                    if r.status_code != 200:
                        continue
                    soup = BeautifulSoup(r.text, "html.parser")
                    # Find all tables
                    tables = soup.find_all("table")
                    print(f"  Found {len(tables)} tables")
                    for table in tables:
                        rows = table.find_all("tr")
                        for row in rows[1:]:
                            cols = [c.get_text(strip=True) for c in row.find_all("td")]
                            if len(cols) < 5:
                                continue
                            # Chittorgarh columns: Company | Client | Buy/Sell | Qty | Price | Date
                            # Varies, try to detect symbol-like
                            text = " ".join(cols)
                            # Company name is usually col 0 or 1
                            company = cols[0] if cols else ""
                            # Try to find qty and price numbers
                            # Save raw for matching by company name later
                            item = {
                                "company": company,
                                "cols": cols,
                                "raw_text": text,
                                "date": d_display,
                                "kind": kind,
                            }
                            if kind == "bulk-deals":
                                bulk.append(item)
                            else:
                                block.append(item)
                    print(f"  Chittorgarh {kind} {d_str}: total so far bulk={len(bulk)} block={len(block)}")
                except Exception as e:
                    print(f"Chittorgarh {kind} Error: {e}")
                    traceback.print_exc()
        print(f"Chittorgarh total: Bulk {len(bulk)}, Block {len(block)}")
        return bulk, block

    # ---------- 4. BULK/BLOCK VIA NSE API VIA PROXY ----------
    def get_nse_bulk_block_via_proxy():
        bulk, block = [], []
        for kind, url in [
            ("bulk", "https://www.nseindia.com/api/corporates-bulk-deals?index=equities"),
            ("block", "https://www.nseindia.com/api/corporates-block-deals?index=equities"),
        ]:
            status, txt = fetch_via_proxy(url)
            if status == 200:
                data = safe_json_text(txt)
                rows = data.get("data", []) if isinstance(data, dict) else []
                print(f"NSE {kind} VIA PROXY: {len(rows)} rows")
                if kind == "bulk":
                    bulk = rows
                else:
                    block = rows
            else:
                print(f"NSE {kind} via proxy failed")
        return bulk, block

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
            print(f"Sent {symbol} {deal_type}: {r.status_code}")
            return r.status_code == 200
        except Exception as e:
            print(f"Send PDF failed {symbol}: {e}")
            return False

    def send_text(msg):
        try:
            requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=20)
        except:
            pass

    # ---- Fetch All via Proxy / Chittorgarh ----
    bse_data = get_bse_all_via_moneycontrol()
    nse_data = get_nse_ann()
    nse_bulk_api, nse_block_api = get_nse_bulk_block_via_proxy()
    chit_bulk, chit_block = get_bulk_block_via_chittorgarh()
    print(f"TOTAL FETCHED -> BSE Ann: {len(bse_data)}, NSE Ann: {len(nse_data)}, NSE Bulk API: {len(nse_bulk_api)}, NSE Block API: {len(nse_block_api)}, Chittorgarh Bulk: {len(chit_bulk)}, Chittorgarh Block: {len(chit_block)}")

    sent_now = 0
    with_news = []
    new_ids = []

    for sym in symbols:
        sym_norm = normalize(sym)
        sym_prefix = sym_norm[:6] if len(sym_norm) >= 6 else sym_norm

        # Announcements
        matched = []
        for item in bse_data:
            if not isinstance(item, dict):
                continue
            name_norm = normalize(item.get("SLONGNAME", ""))
            if name_norm and (sym_norm in name_norm or name_norm.startswith(sym_prefix)):
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

        # Bulk - NSE API via proxy + Chittorgarh
        bulk_matched = []
        for item in nse_bulk_api:
            if str(item.get("symbol", "")).upper().strip() == sym:
                title = f"{item.get('buySell','')} - {item.get('clientName','')} - Qty: {item.get('quantityTraded','')} @ Rs {item.get('tradePrice','')}"
                uid = make_id(sym, title, str(item.get("date","")))
                if uid in sent_history:
                    continue
                bulk_matched.append({"source": "NSE BULK", "time": str(item.get("date","")), "title": title, "link": "", "uid": uid})
        # Chittorgarh - match by company name in raw text
        for item in chit_bulk:
            comp_norm = normalize(item.get("company", ""))
            # Chittorgarh company like "Aavas Financiers Ltd" -> normalize -> AAVASFINANCIERS
            # Symbol AAVAS -> check if symbol in company OR company starts with symbol
            if comp_norm and (sym_norm in comp_norm or comp_norm.startswith(sym_norm[:5]) or sym_norm[:5] in comp_norm):
                cols = item.get("cols", [])
                title = f"BULK - {' | '.join(cols[:5])}"
                uid = make_id(sym, title, item.get("date",""))
                if uid in sent_history:
                    continue
                bulk_matched.append({"source": "CHITTORGARH BULK", "time": item.get("date",""), "title": title, "link": "", "uid": uid})
        if bulk_matched:
            pdf_file = make_pdf(sym, bulk_matched, "BULK")
            if send_pdf(pdf_file, sym, len(bulk_matched), "BULK"):
                for m in bulk_matched:
                    new_ids.append(m['uid'])
                with_news.append(f"{sym} Bulk ({len(bulk_matched)})")
                sent_now += 1
            time.sleep(1.5)

        # Block
        block_matched = []
        for item in nse_block_api:
            if str(item.get("symbol", "")).upper().strip() == sym:
                title = f"{item.get('buySell','')} - {item.get('clientName','')} - Qty: {item.get('quantityTraded','')} @ Rs {item.get('tradePrice','')}"
                uid = make_id(sym, title, str(item.get("date","")))
                if uid in sent_history:
                    continue
                block_matched.append({"source": "NSE BLOCK", "time": str(item.get("date","")), "title": title, "link": "", "uid": uid})
        for item in chit_block:
            comp_norm = normalize(item.get("company", ""))
            if comp_norm and (sym_norm in comp_norm or comp_norm.startswith(sym_norm[:5]) or sym_norm[:5] in comp_norm):
                cols = item.get("cols", [])
                title = f"BLOCK - {' | '.join(cols[:5])}"
                uid = make_id(sym, title, item.get("date",""))
                if uid in sent_history:
                    continue
                block_matched.append({"source": "CHITTORGARH BLOCK", "time": item.get("date",""), "title": title, "link": "", "uid": uid})
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
        print("No NEW - Silent")
        if len(bse_data) == 0 and len(nse_data) == 0 and len(chit_bulk) == 0:
            print("WARNING: ALL sources blocked - will retry next run")
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