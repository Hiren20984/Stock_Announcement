import os, re, json, requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
CHANNEL_URL = "https://t.me/s/fundamental3"
STATE_FILE = "last_id.txt"

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True})

def main():
    with open("stocks.json") as f:
        stocks = json.load(f)

    # Build search list: Ticker + Company Name
    search_map = {}
    for ticker, name in stocks.items():
        search_map[ticker.upper()] = name
        # also add name without spaces
        search_map[name.upper().replace(" ", "")] = name
        search_map[name.upper()] = name

    html = requests.get(CHANNEL_URL, timeout=20).text
    soup = BeautifulSoup(html, "html.parser")
    msgs = soup.find_all("div", class_="tgme_widget_message")

    last_id = 0
    if os.path.exists(STATE_FILE):
        try: last_id = int(open(STATE_FILE).read().strip() or 0)
        except: last_id = 0

    new_last = last_id
    for m in msgs:
        try: mid = int(m.get("data-post", "x/0").split("/")[1])
        except: continue
        if mid <= last_id: continue
        new_last = max(new_last, mid)
        div = m.find("div", class_="tgme_widget_message_text")
        if not div: continue
        text = div.get_text()
        upper = " " + text.upper() + " "

        found = []
        for key, name in search_map.items():
            # Match whole word for ticker, or full company name
            if len(key) <= 6:
                if re.search(r'\b' + re.escape(key) + r'\b', upper):
                    found.append(f"{key} ({name})")
            else:
                if key in upper:
                    found.append(f"{key} ({name})")
        # Remove duplicates
        found = list(dict.fromkeys(found))
        if found:
            send_telegram(f"📢 fundamental3 | Found: {', '.join(found[:3])}\n\n{text}\n\n---\nFor educational purposes only. Not SEBI registered advisor.")

    open(STATE_FILE, "w").write(str(new_last))
    print(f"Done. Checked {len(msgs)} msgs, last_id={new_last}")

if __name__ == "__main__":
    main()