import os, json, requests, datetime, pytz, time, hashlib, sys, traceback
from fpdf import FPDF
try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except:
    HAS_BS4 = False
    print("bs4 not installed")

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

def safe_json_text(txt):
    try:
        txt = txt.strip()
        if not txt or txt.startswith("<"):
            return {}
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

def fetch_via_proxy(target_url):
    try:
        r = requests.get(target_url, headers=HEADERS, timeout=25)
        if r.status_code == 200 and len(r.text) > 100 and "Access Denied" not in r.text and "Resource not found" not in r.text:
            print(f"  DIRECT OK: {target_url[:70]} -> {r.status_code} len {len(r.text)}")
            return r.status_code, r.text
        print(f"  DIRECT blocked: {target_url[:70]} -> {r.status_code} len {len(r.text)}")
    except Exception as e:
        print(f"  DIRECT error: {e}")
    for proxy in [
        f"https://api.allorigins.win/raw?url={target_url}",
        f"https://api.allorigins.win/get?url={target_url}",
        f"https://corsproxy.io/?url={target_url}",
    ]:
        try:
            print(f"  Trying proxy: {proxy[:70]}...")
            r = requests.get(proxy, headers=HEADERS, timeout=30)
            print(f"  PROXY -> {r.status_code} len {len(r.text)}")
            if r.status_code == 200 and len(r.text) > 100 and "Access Denied" not in r.text:
                txt = r.text
                if '"contents"' in txt[:300]:
                    try:
                        outer = json.loads(txt)
                        txt = outer.get("contents", txt)
                    except:
                        pass
                return 200, txt
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

    def get_bse_all():
        all_rows = []
        for d in [yesterday_str, today_str]:
            bse_url = f"https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w?strCat=-1&strPrevDate={d}&strScrip=&strSearch=P&strToDate={d}&strType=C"
            status, txt = fetch_via_proxy(bse_url)
            if status == 200:
                data = safe_json_text(txt)
                rows = data.get("Table", []) if isinstance(data, dict) else []
                print(f"BSE Ann {d} VIA PROXY: {len(rows)} rows")
                all_rows.extend(rows)
            else:
                print(f"BSE Ann {d} VIA PROXY failed")
        seen = set(); uniq = []
        for row in all_rows:
            k = f"{row.get('HEADLINE','')}|{row.get('DT_TM','')}|{row.get('SCRIP_CD','')}"
            if k not in seen:
                seen.add(k); uniq.append(row)
        print(f"BSE Ann total unique via proxy: {len(uniq)}")
        return uniq

    def get_bulk_block_via_chittorgarh():
        bulk, block = [], []
        if not HAS_BS4:
            print("bs4 missing")
            return bulk, block
        for kind, url in [
            ("bulk-deals", "https://www.chittorgarh.com/report/bulk-deals/"),
            ("block-deals", "https://www.chittorgarh.com/report/block-deals/"),
        ]:
            print(f"Chittorgarh {kind}: fetching {url}")
            try:
                r = requests.get(url, headers=HEADERS, timeout=25)
                print(f"  Chittorgarh {kind}: status {r.status_code} len {len(r.text)}")
                if r.status_code != 200:
                    continue
                soup = BeautifulSoup(r.text, "html.parser")
                tables = soup.find_all("table")
                print(f"  Found {len(tables)} tables")
                for table in tables:
                    rows = table.find_all("tr")
                    if rows:
                        header = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
                        print(f"  Table header: {header}")
                    for row in rows[1:]:
                        cols = [c.get_text(strip=True) for c in row.find_all("td")]
                        if len(cols) < 4:
                            continue
                        company = cols[0] if cols else ""
                        if not company or company.lower() in ["company", "no data"]:
                            continue
                        item = {"company": company, "cols": cols, "raw_text": " ".join(cols), "date": today_display, "kind": kind}
                        if kind == "bulk-deals":
                            bulk.append(item)
                        else:
                            block.append(item)
                print(f"  Chittorgarh {kind}: total bulk={len(bulk)} block={len(block)}")
            except Exception as e:
                print(f"Chittorgarh {kind} Error: {e}")
                traceback.print_exc()
        print(f"Chittorgarh total: Bulk {len(bulk)}, Block {len(block)}")
        return bulk, block

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
                pdf.set_text_color(0, 0, 255)
                pdf.multi_cell(0, 5, f"Link: {str(a['link'])[:180]}")
                pdf.set_text_color(0, 0, 0)
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

    bse_data = get_bse_all()
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
                title = item.get("subject", "") or