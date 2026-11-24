"""Trade Yordamchi — Telegram chatiga signal yuboruvchi skript.

GitHub Actions har 15 daqiqada ishga tushiradi:
  1. Botga /start yozganlarni ro'yxatga qo'shadi (/stop — chiqaradi).
  2. Oltin, Bitcoin va Ethereum'ni 1 soatlik grafikda tahlil qiladi.
  3. KUCHLI signal yangi paydo bo'lsa, hammaga xabar yuboradi.
Holat state.json faylida saqlanadi.
"""
import json
import os
import urllib.parse
import urllib.request

TOKEN = os.environ["BOT_TOKEN"]
APP_URL = os.environ.get("APP_URL", "")
STATE_FILE = "state.json"
API = f"https://api.telegram.org/bot{TOKEN}/"

# (nomi, Binance belgisi, Kraken belgisi)
INSTRUMENTS = [
    ("Oltin (XAUUSD)", "PAXGUSDT", "PAXGUSD"),
    ("Bitcoin (BTCUSD)", "BTCUSDT", "XBTUSD"),
    ("Ethereum (ETHUSD)", "ETHUSDT", "ETHUSD"),
]


# ---------- yordamchilar ----------
def http_json(url, data=None):
    body = None
    headers = {"User-Agent": "Mozilla/5.0"}
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def tg(method, **params):
    try:
        return http_json(API + method, params)
    except Exception as e:  # bitta xato butun ishni to'xtatmasin
        print("Telegram xatosi:", method, e)
        return {}


def load_state():
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
    except Exception:
        s = {}
    s.setdefault("offset", 0)
    s.setdefault("chats", [])
    s.setdefault("last", {})
    return s


def save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


def fmt(v):
    a = abs(v)
    d = 2 if a >= 100 else 4 if a >= 1 else 6
    return f"{v:,.{d}f}"


# ---------- narx ma'lumoti ----------
def candles(binance_sym, kraken_sym):
    """(high, low, close) ro'yxati, faqat yopilgan shamlar."""
    try:
        d = http_json(f"https://data-api.binance.vision/api/v3/klines?symbol={binance_sym}&interval=1h&limit=300")
        rows = [(float(k[2]), float(k[3]), float(k[4])) for k in d]
    except Exception as e:
        print("Binance ishlamadi, Kraken'ga o'tamiz:", e)
        d = http_json(f"https://api.kraken.com/0/public/OHLC?pair={kraken_sym}&interval=60")
        res = d["result"]
        key = [k for k in res if k != "last"][0]
        rows = [(float(k[2]), float(k[3]), float(k[4])) for k in res[key]][-300:]
    return rows[:-1]  # oxirgi sham hali yopilmagan


# ---------- indikatorlar (ilovadagi bilan bir xil) ----------
def ema(a, p):
    k = 2 / (p + 1)
    out, prev = [], None
    for i, v in enumerate(a):
        prev = v if i == 0 else v * k + prev * (1 - k)
        out.append(prev)
    return out


def rsi(cl, p=14):
    g = l = 0.0
    for i in range(1, p + 1):
        d = cl[i] - cl[i - 1]
        g += max(d, 0)
        l += max(-d, 0)
    g /= p
    l /= p
    for i in range(p + 1, len(cl)):
        d = cl[i] - cl[i - 1]
        g = (g * (p - 1) + max(d, 0)) / p
        l = (l * (p - 1) + max(-d, 0)) / p
    return 100.0 if l == 0 else 100 - 100 / (1 + g / l)


def atr(c, p=14):
    tr = [c[0][0] - c[0][1]] + [
        max(c[i][0] - c[i][1], abs(c[i][0] - c[i - 1][2]), abs(c[i][1] - c[i - 1][2])) for i in range(1, len(c))
    ]
    a = sum(tr[1:p + 1]) / p
    for i in range(p + 1, len(tr)):
        a = (a * (p - 1) + tr[i]) / p
    return a


def analyze(c):
    cl = [x[2] for x in c]
    price = cl[-1]
    e9, e21, e50, e200 = ema(cl, 9), ema(cl, 21), ema(cl, 50), ema(cl, 200)
    e12, e26 = ema(cl, 12), ema(cl, 26)
    macd = [a - b for a, b in zip(e12, e26)]
    sig = ema(macd, 9)
    r = rsi(cl)
    s = 0
    s += 1 if e9[-1] > e21[-1] else -1
    s += 1 if price > e50[-1] else -1
    s += 1 if price > e200[-1] else -1
    s += 1 if macd[-1] - sig[-1] > 0 else -1
    if r < 30:
        s += 1
    elif r > 70:
        s -= 1
    if s >= 4:
        v = "KUCHLI BUY"
    elif s >= 2:
        v = "BUY"
    elif s <= -4:
        v = "KUCHLI SELL"
    elif s <= -2:
        v = "SELL"
    else:
        v = "NEYTRAL"
    return {"verdict": v, "score": s, "price": price, "atr": atr(c), "rsi": r}


# ---------- xabarlar ----------
def keyboard():
    if not APP_URL:
        return None
    return {"inline_keyboard": [[{"text": "📈 Ilovada ko'rish", "web_app": {"url": APP_URL}}]]}


def send(chat, text):
    params = {"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    kb = keyboard()
    if kb:
        params["reply_markup"] = kb
    tg("sendMessage", **params)


def signal_text(name, res):
    buy = res["verdict"] == "KUCHLI BUY"
    a = res["atr"]
    head = "🟢 <b>SOTIB OLISH signali</b>" if buy else "🔴 <b>SOTISH signali</b>"
    move = "ko'tarilishini" if buy else "tushishini"
    sl_dir = "pastga" if buy else "yuqoriga"
    tp_dir = "yuqoriga" if buy else "pastga"
    agree = abs(res["score"])
    text = (
        f"{head}\n"
        f"<b>{name}</b> · 1 soatlik grafik\n\n"
        f"Hozirgi narx: <b>{fmt(res['price'])}</b>\n"
        f"Indikatorlar narx {move} ko'rsatyapti (kuch: {agree}/5).\n\n"
        f"🛑 Stop-loss: narxdan ~{fmt(1.5 * a)} {sl_dir}\n"
        f"🎯 Take-profit: narxdan ~{fmt(3 * a)} {tp_dir}\n"
    )
    if name.startswith("Oltin"):
        text += "\n<i>Oltin narxi PAXG asosida — Exness narxidan biroz farq qiladi, shuning uchun masofani o'z narxingizdan hisoblang.</i>\n"
    text += "\n⚠️ Bu moliyaviy maslahat emas. Avval demo hisobda sinang."
    return text


WELCOME = (
    "👋 Salom! Endi kuchli signal chiqqanda shu yerga yozaman.\n\n"
    "Kuzatiladi: Oltin, Bitcoin, Ethereum (1 soatlik grafik).\n"
    "Tekshiruv har ~15 daqiqada bo'ladi, shuning uchun javoblar biroz kechikib kelishi mumkin.\n\n"
    "To'xtatish uchun: /stop"
)


def handle_updates(state):
    d = tg("getUpdates", offset=state["offset"], timeout=0, allowed_updates=["message"])
    for u in d.get("result", []):
        state["offset"] = u["update_id"] + 1
        m = u.get("message") or {}
        chat = (m.get("chat") or {}).get("id")
        text = (m.get("text") or "").strip().lower()
        if not chat:
            continue
        if text.startswith("/start"):
            if chat not in state["chats"]:
                state["chats"].append(chat)
            send(chat, WELCOME)
        elif text.startswith("/stop"):
            if chat in state["chats"]:
                state["chats"].remove(chat)
            tg("sendMessage", chat_id=chat, text="🔕 Signal xabarlari to'xtatildi. Qayta yoqish: /start")
        elif text.startswith("/holat"):
            lines = [f"{n}: {state['last'].get(n, '—')}" for n, _, _ in INSTRUMENTS]
            tg("sendMessage", chat_id=chat, text="📊 Hozirgi holat (1 soatlik):\n" + "\n".join(lines))


def main():
    state = load_state()
    handle_updates(state)
    for name, bsym, ksym in INSTRUMENTS:
        try:
            res = analyze(candles(bsym, ksym))
        except Exception as e:
            print("Tahlil xatosi:", name, e)
            continue
        prev = state["last"].get(name)
        print(name, res["verdict"], fmt(res["price"]), "(oldin:", prev, ")")
        if res["verdict"].startswith("KUCHLI") and res["verdict"] != prev:
            for chat in state["chats"]:
                send(chat, signal_text(name, res))
        state["last"][name] = res["verdict"]
    save_state(state)


if __name__ == "__main__":
    main()
