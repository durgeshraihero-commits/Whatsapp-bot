from fastapi import FastAPI, Request
import requests, os

app = FastAPI()

VERIFY_TOKEN = "darkbox_verify"
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

SEARCH_API = "https://relay-wzlz.onrender.com/api/search"
SEARCH_API_KEY = os.getenv("SEARCH_API_KEY")

user_sessions = {}

MENU_TEXT = """🔥 DARKBOX MENU 🔥

Reply with a number:

1️⃣ Phone Info
2️⃣ Family Info
3️⃣ Aadhar Info
4️⃣ Vehicle Info
5️⃣ UPI Info
6️⃣ Email Info
7️⃣ IMEI Info
8️⃣ GST Info
"""

MENU_MAP = {
    "1": "phone",
    "2": "family",
    "3": "aadhar",
    "4": "vehicle",
    "5": "upi",
    "6": "email",
    "7": "imei",
    "8": "gst",
}

def send_whatsapp(to, text):
    requests.post(
        f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages",
        headers={
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "messaging_product": "whatsapp",
            "to": to,
            "text": {"body": text},
        },
        timeout=30,
    )

@app.get("/webhook")
async def verify(request: Request):
    p = request.query_params
    if p.get("hub.verify_token") == VERIFY_TOKEN:
        return int(p.get("hub.challenge"))
    return "Invalid"

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()

    try:
        msg = data["entry"][0]["changes"][0]["value"]["messages"][0]
        from_user = msg["from"]
        text = msg["text"]["body"].strip().lower()

        session = user_sessions.get(from_user)

        # STEP 1: ACTIVATE BOT
        if text == "darkbox":
            user_sessions[from_user] = {"stage": "menu"}
            send_whatsapp(from_user, MENU_TEXT)
            return {"ok": True}

        # IGNORE if bot not activated
        if not session:
            return {"ignored": True}

        # STEP 2: MENU SELECTION
        if session["stage"] == "menu":
            if text in MENU_MAP:
                user_sessions[from_user] = {
                    "stage": "awaiting_input",
                    "search_type": MENU_MAP[text],
                }
                send_whatsapp(from_user, "🔍 Send input to search:")
            else:
                send_whatsapp(from_user, "❌ Invalid choice. Send 1–8.")
            return {"ok": True}

        # STEP 3: SEARCH INPUT
        if session["stage"] == "awaiting_input":
            search_type = session["search_type"]

            res = requests.post(
                SEARCH_API,
                headers={
                    "X-API-Key": SEARCH_API_KEY,
                    "Content-Type": "application/json",
                },
                json={"search_type": search_type, "query": text},
                timeout=60,
            )

            if res.status_code == 200:
                result = res.json().get("result", "No data found")
            else:
                result = "❌ Error fetching data"

            send_whatsapp(from_user, result)
            user_sessions.pop(from_user, None)

    except Exception as e:
        print("Error:", e)

    return {"status": "done"}
