from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import requests
import os

app = FastAPI()

# ================= CONFIG =================

VERIFY_TOKEN = "darkbox_verify"

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

SEARCH_API = "https://relay-wzlz.onrender.com/api/search"
SEARCH_API_KEY = os.getenv("SEARCH_API_KEY")

# ================= SESSION STORE =================

user_sessions = {}

# ================= MENU =================

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

# ================= WHATSAPP SEND =================

def send_whatsapp(to: str, text: str):
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "text": {"body": text},
    }

    requests.post(url, headers=headers, json=payload, timeout=30)

# ================= WEBHOOK VERIFY =================

@app.get("/webhook")
async def verify_webhook(request: Request):
    params = request.query_params

    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return PlainTextResponse(challenge)

    return PlainTextResponse("Invalid token", status_code=403)

# ================= WEBHOOK RECEIVE =================

@app.post("/webhook")
async def receive_message(request: Request):
    data = await request.json()

    try:
        entry = data.get("entry", [])[0]
        change = entry.get("changes", [])[0]
        value = change.get("value", {})
        messages = value.get("messages")

        if not messages:
            return {"status": "no_message"}

        msg = messages[0]
        from_user = msg["from"]
        text = msg["text"]["body"].strip().lower()

        session = user_sessions.get(from_user)

        # 🔑 STEP 1: ACTIVATE BOT
        if text == "darkbox":
            user_sessions[from_user] = {"stage": "menu"}
            send_whatsapp(from_user, MENU_TEXT)
            return {"status": "menu_sent"}

        # ❌ Ignore if not activated
        if not session:
            return {"status": "ignored"}

        # 🔢 STEP 2: MENU SELECTION
        if session["stage"] == "menu":
            if text in MENU_MAP:
                user_sessions[from_user] = {
                    "stage": "awaiting_input",
                    "search_type": MENU_MAP[text],
                }
                send_whatsapp(from_user, "🔍 Send input to search:")
            else:
                send_whatsapp(from_user, "❌ Invalid choice. Reply 1–8.")
            return {"status": "menu_handled"}

        # 🔍 STEP 3: SEARCH
        if session["stage"] == "awaiting_input":
            search_type = session["search_type"]

            res = requests.post(
                SEARCH_API,
                headers={
                    "X-API-Key": SEARCH_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "search_type": search_type,
                    "query": text,
                },
                timeout=60,
            )

            if res.status_code == 200:
                result = res.json().get("result", "No data found")
            else:
                result = "❌ Error fetching data"

            send_whatsapp(from_user, str(result))
            user_sessions.pop(from_user, None)

            return {"status": "search_done"}

    except Exception as e:
        print("Webhook error:", e)

    return {"status": "ok"}
