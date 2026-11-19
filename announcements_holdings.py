import os, json, requests, datetime, pytz, time, hashlib, sys, traceback, re
from fpdf import FPDF
try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except:
    HAS_BS4 = False
    print('bs4 not installed')

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
HISTORY_FILE = 'sent_history.json'

def normalize(s):
    return str(s or '').upper().replace(' ', '').replace('-', '').replace('&', 'AND').replace('.', '').replace('LTD', '').replace('LIMITED', '')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
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

    # ---------- BULK / BLOCK FROM 5PAISA + TAPETIDE ----------
    def fetch_table_rows(url):
        try:
            print(f'Fetching {url}')
            r = requests.get(url, headers=HEADERS, timeout=30)
            print(f'  -> status {r.status_code} len {len(r.text)}')
            if r.status_code != 200:
                return []
            if len(r.text) < 2000:
                print('  -> too short, likely block page')
                return []
            soup = BeautifulSoup(r.text, 'html.parser')
            tables = soup.find_all('table')
            print(f'  -> found {len(tables)} tables')
            all_rows = []
            for table in tables:
                rows = table.find_all('tr')
                if not rows:
                    continue
                header = [c.get_text(strip=True) for c in rows[0].find_all(['th', 'td'])]
                print(f'  -> header: {header}')
                # Only accept tables that look like deals: has Stock/Company + Client + Quantity/Price
                header_text = ' '.join(header).lower()
                if 'client' not in header_text and 'stock' not in header_text and 'company' not in header_text:
                    continue
                for row in rows[1:]:
                    cols = [c.get_text(strip=True) for c in row.find_all('td')]
                    if len(cols) < 5:
                        continue
                    if not cols[0]:
                        continue
                    all_rows.append(cols)
            print(f'  -> parsed {len(all_rows)} deal rows from {url}')
            return all_rows
        except Exception as e:
            print(f'Fetch error {url}: {e}')
            traceback.print_exc()
            return []

    def get_bulk_block():
        bulk = []
        block = []
        # Try multiple URLs - first one that returns rows wins
        urls_5paisa = [
            'https://www.5paisa.com/research/bulk-block-deals',
            'https://www.5paisa.com/stocks/bulk-block-deals',
            'https://www.5paisa.com/bulk-block-deals',
        ]
        urls_tapetide = [
            'https://tapetide.com/bulk-block-deals',
            'https://tapetide.com/',
        ]
        all_rows = []
        for u in urls_5paisa:
            rows = fetch_table_rows(u)
            if rows:
                print(f'5PAISA SUCCESS from {u}')
                all_rows.extend(rows)
                break
        if not all_rows:
            for u in urls_tapetide:
                rows = fetch_table_rows(u)
                if rows:
                    print(f'TAPETIDE SUCCESS from {u}')
                    all_rows.extend(rows)
                    break
        print(f'TOTAL deal rows fetched: {len(all_rows)}')
        # Parse rows: 5paisa format = Stock | Client | Exchange | Deal Type | Action | Date | Price | Qty
        # Tapetide format = Company | Deal Type | Exchange | Date | Client | Action | Qty | Price
        for cols in all_rows:
            text = ' | '.join(cols)
            text_lower = text.lower()
            # Determine deal type
            is_block = 'block' in text_lower
            is_bulk = 'bulk' in text_lower
            # Company is usually cols[0]
            company = cols[0]
            item = {'company': company, 'cols': cols, 'raw': text, 'date': today_display}
            if is_block:
                block.append(item)
            elif is_bulk:
                bulk.append(item)
            else:
                # If no type in row, treat as bulk (most deals are bulk)
                bulk.append(item)
        print(f'Parsed: Bulk {len(bulk)}, Block {len(block)}')
        return bulk, block

    # ---------- ANNOUNCEMENTS FROM MONEYCONTROL + 5PAISA ----------
    def get_announcements():
        # Moneycontrol + 5paisa announcements pages - try both, take first that works
        ann = []
        urls = [
            'https://www.moneycontrol.com/company-announcements/',
            'https://www.moneycontrol.com/news/business/stocks/',
            'https://www.5paisa.com/research/corporate-announcements',
        ]
        for url in urls:
            try:
                print(f'Fetching announcements {url}')
                r = requests.get(url, headers=HEADERS, timeout=30)
                print(f'  -> status {r.status_code} len {len(r.text)}')
                if r.status_code != 200 or len(r.text) < 2000:
                    continue
                soup = BeautifulSoup(r.text, 'html.parser')
                # Find links with company-like titles
                links = soup.find_all('a')
                count = 0
                for a in links:
                    txt = a.get_text(strip=True)
                    href = a.get('href', '')
                    if len(txt) < 20 or len(txt) > 200:
                        continue
                    # Skip nav links
                    if txt.lower() in ['home', 'markets', 'news', 'more', 'login', 'sign up']:
                        continue
                    # Keep if it looks like announcement title
                    if any(k in txt.lower() for k in ['result', 'dividend', 'board', 'meeting', 'announce', 'allot', 'issue', 'buyback', 'split', 'bonus', 'agreement', 'order', 'contract']):
                        ann.append({'company': '', 'title': txt, 'link': href, 'time': today_display})
                        count += 1
                print(f'  -> {url} gave {count} announcement-like titles')
                if count > 5:
                    break
            except Exception as e:
                print(f'Ann fetch error {url}: {e}')
        print(f'Total announcements fetched: {len(ann)}')
        return ann

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

    bulk_list, block_list = get_bulk_block()
    ann_list = get_announcements()
    print(f'TOTAL FETCHED -> Bulk: {len(bulk_list)}, Block: {len(block_list)}, Ann: {len(ann_list)}')

    sent_now = 0
    with_news = []
    new_ids = []

    for sym in symbols:
        sym_norm = normalize(sym)
        if len(sym_norm) >= 5:
            sym_key = sym_norm[:5]
        else:
            sym_key = sym_norm

        # Bulk matching - company name contains symbol key
        bulk_matched = []
        for item in bulk_list:
            comp = item.get('company', '')
            comp_norm = normalize(comp)
            if comp_norm and (sym_norm in comp_norm or comp_norm.startswith(sym_key) or sym_key in comp_norm):
                cols = item.get('cols', [])
                joined = ' | '.join(cols[:8])
                title = f'BULK - {joined}'
                dt = item.get('date', '')
                uid = make_id(sym, title, dt)
                if uid in sent_history:
                    continue
                bulk_matched.append({'source': '5PAISA/TAPETIDE BULK', 'time': dt, 'title': title, 'link': '', 'uid': uid})
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
        for item in block_list:
            comp = item.get('company', '')
            comp_norm = normalize(comp)
            if comp_norm and (sym_norm in comp_norm or comp_norm.startswith(sym_key) or sym_key in comp_norm):
                cols = item.get('cols', [])
                joined = ' | '.join(cols[:8])
                title = f'BLOCK - {joined}'
                dt = item.get('date', '')
                uid = make_id(sym, title, dt)
                if uid in sent_history:
                    continue
                block_matched.append({'source': '5PAISA/TAPETIDE BLOCK', 'time': dt, 'title': title, 'link': '', 'uid': uid})
        if block_matched:
            pdf_file = make_pdf(sym, block_matched, 'BLOCK')
            ok = send_pdf(pdf_file, sym, len(block_matched), 'BLOCK')
            if ok:
                for m in block_matched:
                    new_ids.append(m['uid'])
                with_news.append(f'{sym} Block ({len(block_matched)})')
                sent_now += 1
            time.sleep(1.5)

        # Announcements - match by title containing symbol or company name
        # For now, only send announcements if title contains symbol (strict, avoids spam)
        ann_matched = []
        for item in ann_list:
            ttl = item.get('title', '')
            ttl_norm = normalize(ttl)
            if sym_norm in ttl_norm or sym_key in ttl_norm:
                dt = item.get('date', '')
                uid = make_id(sym, ttl, dt)
                if uid in sent_history:
                    continue
                ann_matched.append({'source': 'MONEYCONTROL', 'time': dt, 'title': ttl, 'link': item.get('link', ''), 'uid': uid})
        if ann_matched:
            pdf_file = make_pdf(sym, ann_matched, 'ANNOUNCEMENT')
            ok = send_pdf(pdf_file, sym, len(ann_matched), 'ANNOUNCEMENT')
            if ok:
                for m in ann_matched:
                    new_ids.append(m['uid'])
                with_news.append(f'{sym} Ann ({len(ann_matched)})')
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
        if len(bulk_list) == 0 and len(block_list) == 0 and len(ann_list) == 0:
            print('WARNING: ALL sources blocked - need Render move')
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