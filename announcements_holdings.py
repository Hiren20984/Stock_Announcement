import os, json, requests, datetime, pytz, time, hashlib, sys, traceback
from fpdf import FPDF

TOKEN = os.getenv('TELEGRAM_TOKEN_HOLDINGS')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID_HOLDINGS')
print(f'DEBUG: TOKEN exists? {bool(TOKEN)}')
if not TOKEN or not CHAT_ID:
    print('ERROR: Secrets missing!')
    sys.exit(0)

IST = pytz.timezone('Asia/Kolkata')
now = datetime.datetime.now(IST)
today_display = now.strftime('%d %b %Y')
today_str = now.strftime('%Y%m%d')
yesterday = now - datetime.timedelta(days=1)
yesterday_str = yesterday.strftime('%Y%m%d')
HISTORY_FILE = 'sent_history.json'

def normalize(s):
    return str(s or '').upper().replace(' ', '').replace('-', '').replace('&', 'AND').replace('.', '').replace('LTD', '').replace('LIMITED', '')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/html, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.google.com/',
}

try:
    symbols = []
    if os.path.exists('holdings.json'):
        try:
            with open('holdings.json') as f:
                data = json.load(f)
                symbols = list(data.keys()) if isinstance(data, dict) else data
        except Exception as e:
            print(f'holdings.json corrupt: {e}')
    symbols = [str(s).upper().strip() for s in symbols if s]
    print(f'Tracking {len(symbols)} stocks')
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
    cutoff = (now - datetime.timedelta(days=7)).strftime('%Y-%m-%d')
    sent_history = {k: v for k, v in sent_history.items() if isinstance(v, str) and v >= cutoff}
    print(f'History loaded: {len(sent_history)} ids')

    def make_id(symbol, title, time_str):
        raw = f'{symbol}|{title}|{time_str}'
        raw = raw.upper().strip()
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    def safe_json(r):
        try:
            txt = r.text.strip()
            if not txt or txt.startswith('<'):
                print(f'HTML not JSON status {r.status_code} len {len(txt)}')
                return {}
            return r.json()
        except Exception as e:
            print(f'JSON parse failed: {e}')
            return {}

    def get_bse_ann():
        all_rows = []
        for d in [yesterday_str, today_str]:
            try:
                s = requests.Session()
                s.headers.update(HEADERS)
                try:
                    s.get('https://www.bseindia.com/', timeout=20)
                    time.sleep(1)
                except:
                    pass
                url = 'https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w'
                payload = {'strCat': '-1', 'strPrevDate': d, 'strScrip': '', 'strSearch': 'P', 'strToDate': d, 'strType': 'C', 'subcategory': '-1'}
                h = dict(HEADERS)
                h.update({'Referer': 'https://www.bseindia.com/corporates/ann.html', 'Content-Type': 'application/json'})
                r = s.post(url, headers=h, json=payload, timeout=30)
                print(f'BSE Ann {d}: status {r.status_code}')
                if r.status_code == 200:
                    data = safe_json(r)
                    rows = data.get('Table', []) if isinstance(data, dict) else []
                    print(f'BSE Ann {d}: {len(rows)} rows')
                    all_rows.extend(rows)
            except Exception as e:
                print(f'BSE Ann Error {d}: {e}')
        seen = set()
        uniq = []
        for row in all_rows:
            k = f"{row.get('HEADLINE','')}|{row.get('DT_TM','')}|{row.get('SCRIP_CD','')}"
            if k not in seen:
                seen.add(k)
                uniq.append(row)
        print(f'BSE Ann total: {len(uniq)}')
        return uniq

    def get_nse_ann():
        try:
            s = requests.Session()
            s.headers.update(HEADERS)
            try:
                s.get('https://www.nseindia.com', timeout=20)
                time.sleep(1)
            except:
                pass
            url = 'https://www.nseindia.com/api/corporate-announcements?index=equities&symbol='
            r = s.get(url, timeout=30)
            print(f'NSE Ann: status {r.status_code}')
            if r.status_code == 200:
                data = safe_json(r)
                rows = data.get('data', []) if isinstance(data, dict) else []
                print(f'NSE Ann: {len(rows)} rows')
                return rows
        except Exception as e:
            print(f'NSE Ann Error: {e}')
        return []

    def get_nse_bulk_block():
        bulk = []
        block = []
        try:
            s = requests.Session()
            s.headers.update(HEADERS)
            try:
                s.get('https://www.nseindia.com', timeout=20)
                time.sleep(1)
            except:
                pass
            for kind, url in [('bulk', 'https://www.nseindia.com/api/corporates-bulk-deals?index=equities'), ('block', 'https://www.nseindia.com/api/corporates-block-deals?index=equities')]:
                try:
                    r = s.get(url, timeout=30)
                    print(f'NSE {kind}: status {r.status_code}')
                    if r.status_code == 200:
                        data = safe_json(r)
                        rows = data.get('data', []) if isinstance(data, dict) else []
                        print(f'NSE {kind}: {len(rows)} rows')
                        if kind == 'bulk':
                            bulk = rows
                        else:
                            block = rows
                except Exception as e:
                    print(f'NSE {kind} Error: {e}')
        except Exception as e:
            print(f'NSE Bulk Block Error: {e}')
        return bulk, block

    def make_pdf(symbol, items, deal_type='ANNOUNCEMENT'):
        filename = f'{symbol}_{deal_type}_{now.strftime("%d%b%Y_%H%M")}.pdf'
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        pdf.set_font('Helvetica', 'B', 16)
        if deal_type == 'BULK':
            title_h = f'{symbol} - BULK DEAL'
        elif deal_type == 'BLOCK':
            title_h = f'{symbol} - BLOCK DEAL'
        else:
            title_h = f'{symbol} - Announcements'
        pdf.cell(0, 10, title_h, new_x='LMARGIN', new_y='NEXT', align='C')
        pdf.set_font('Helvetica', '', 10)
        pdf.cell(0, 8, f'Date: {today_display} | New: {len(items)}', new_x='LMARGIN', new_y='NEXT', align='C')
        pdf.ln(5)
        for i, a in enumerate(items, 1):
            src = a.get('source', '')
            tm = str(a.get('time', ''))
            tm = tm.encode('ascii', 'ignore').decode()
            pdf.set_font('Helvetica', 'B', 11)
            pdf.multi_cell(0, 7, f'{i}. [{src}] {tm}')
            pdf.set_font('Helvetica', '', 10)
            ttl = str(a.get('title', ''))
            ttl = ttl.encode('ascii', 'ignore').decode()
            pdf.multi_cell(0, 6, ttl)
            link = a.get('link', '')
            if link:
                pdf.set_font('Helvetica', '', 8)
                pdf.set_text_color(0, 0, 255)
                pdf.multi_cell(0, 5, f'Link: {str(link)[:180]}')
                pdf.set_text_color(0, 0, 0)
            pdf.ln(4)
        pdf.output(filename)
        return filename

    def send_pdf(filename, symbol, count, deal_type='ANNOUNCEMENT'):
        if deal_type == 'BULK':
            emoji = '📊'
            label = 'BULK DEAL'
        elif deal_type == 'BLOCK':
            emoji = '🧱'
            label = 'BLOCK DEAL'
        else:
            emoji = '📢'
            label = 'Announcement'
        caption = f'{emoji} {symbol} - {label}\n📅 {today_display} {now.strftime("%I:%M %p")}\n📄 {count} NEW {label}(s)'
        url = f'https://api.telegram.org/bot{TOKEN}/sendDocument'
        try:
            with open(filename, 'rb') as f:
                r = requests.post(url, data={'chat_id': CHAT_ID, 'caption': caption}, files={'document': f}, timeout=90)
            print(f'Sent {symbol} {deal_type}: {r.status_code}')
            return r.status_code == 200
        except Exception as e:
            print(f'Send PDF failed {symbol}: {e}')
            return False

    def send_text(msg):
        try:
            requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage', data={'chat_id': CHAT_ID, 'text': msg, 'parse_mode': 'HTML'}, timeout=20)
        except:
            pass

    bse_data = get_bse_ann()
    nse_data = get_nse_ann()
    nse_bulk, nse_block = get_nse_bulk_block()
    print(f'TOTAL FETCHED -> BSE Ann: {len(bse_data)}, NSE Ann: {len(nse_data)}, Bulk: {len(nse_bulk)}, Block: {len(nse_block)}')

    sent_now = 0
    with_news = []
    new_ids = []

    for sym in symbols:
        sym_norm = normalize(sym)
        if len(sym_norm) >= 6:
            sym_prefix = sym_norm[:6]
        else:
            sym_prefix = sym_norm

        matched = []
        for item in bse_data:
            if not isinstance(item, dict):
                continue
            name_norm = normalize(item.get('SLONGNAME', ''))
            if name_norm and (sym_norm in name_norm or name_norm.startswith(sym_prefix)):
                title = item.get('HEADLINE', '') or ''
                time_str = item.get('DT_TM', '') or ''
                uid = make_id(sym, title, time_str)
                if uid in sent_history:
                    continue
                attach = item.get('ATTACHMENTNAME', '') or ''
                link = f'https://www.bseindia.com/xml-data/corpfiling/AttachHis/{attach}' if attach else ''
                matched.append({'source': 'BSE', 'time': time_str, 'title': title, 'link': link, 'uid': uid})
        for item in nse_data:
            if not isinstance(item, dict):
                continue
            sym_val = str(item.get('symbol', '')).upper().strip()
            if sym_val == sym:
                title = item.get('subject', '') or ''
                time_str = item.get('annDate', '') or ''
                uid = make_id(sym, title, time_str)
                if uid in sent_history:
                    continue
                att = item.get('attachment', '') or ''
                matched.append({'source': 'NSE', 'time': time_str, 'title': title, 'link': att, 'uid': uid})
        if matched:
            pdf_file = make_pdf(sym, matched, 'ANNOUNCEMENT')
            ok = send_pdf(pdf_file, sym, len(matched), 'ANNOUNCEMENT')
            if ok:
                for m in matched:
                    new_ids.append(m['uid'])
                with_news.append(f'{sym} Ann ({len(matched)})')
                sent_now += 1
            time.sleep(1.5)

        bulk_matched = []
        for item in nse_bulk:
            sym_val = str(item.get('symbol', '')).upper().strip()
            if sym_val == sym:
                title = f"{item.get('buySell','')} - {item.get('clientName','')} - Qty: {item.get('quantityTraded','')} @ Rs {item.get('tradePrice','')}"
                dt = str(item.get('date', ''))
                uid = make_id(sym, title, dt)
                if uid in sent_history:
                    continue
                bulk_matched.append({'source': 'NSE BULK', 'time': dt, 'title': title, 'link': '', 'uid': uid})
        if bulk_matched:
            pdf_file = make_pdf(sym, bulk_matched, 'BULK')
            ok = send_pdf(pdf_file, sym, len(bulk_matched), 'BULK')
            if ok:
                for m in bulk_matched:
                    new_ids.append(m['uid'])
                with_news.append(f'{sym} Bulk ({len(bulk_matched)})')
                sent_now += 1
            time.sleep(1.5)

        block_matched = []
        for item in nse_block:
            sym_val = str(item.get('symbol', '')).upper().strip()
            if sym_val == sym:
                title = f"{item.get('buySell','')} - {item.get('clientName','')} - Qty: {item.get('quantityTraded','')} @ Rs {item.get('tradePrice','')}"
                dt = str(item.get('date', ''))
                uid = make_id(sym, title, dt)
                if uid in sent_history:
                    continue
                block_matched.append({'source': 'NSE BLOCK', 'time': dt, 'title': title, 'link': '', 'uid': uid})
        if block_matched:
            pdf_file = make_pdf(sym, block_matched, 'BLOCK')
            ok = send_pdf(pdf_file, sym, len(block_matched), 'BLOCK')
            if ok:
                for m in block_matched:
                    new_ids.append(m['uid'])
                with_news.append(f'{sym} Block ({len(block_matched)})')
                sent_now += 1
            time.sleep(1.5)

    for uid in new_ids:
        sent_history[uid] = now.strftime('%Y-%m-%d')
    try:
        with open(HISTORY_FILE, 'w') as f:
            json.dump(sent_history, f, indent=2)
        print(f'History saved: {len(sent_history)} ids')
    except Exception as e:
        print(f'History save failed: {e}')

    if sent_now == 0:
        print('No NEW - Silent')
    else:
        send_text(f'✅ <b>Done - {today_display} {now.strftime("%I:%M %p")}</b>\n\nNew PDFs sent: {sent_now}\nStocks: {", ".join(with_news)}')

except Exception as e:
    print('FATAL ERROR:')
    traceback.print_exc()
    try:
        requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage', data={'chat_id': CHAT_ID, 'text': f'❌ Holdings Bot Error:\n{str(e)[:500]}'}, timeout=20)
    except:
        pass
    sys.exit(0)
