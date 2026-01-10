from fastapi import FastAPI, Request
import requests
import os

app = FastAPI()

# ================= CONFIG =================

VERIFY_TOKEN = "darkbox_verify"

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
SEARCH_API_KEY = os.getenv("SEARCH_API_KEY")

GRAPH_API = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"

# ================= STATE =================

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

# ================= HELPERS =================

def send_text(to: str, text: str):
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    requests.post(
        GRAPH_API,
        headers={
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )


def send_template(to: str):
    """
    Uses the ONLY approved template you currently have: hello_world
    """
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": "hello_world",
            "language": {"code": "en_US"},
        },
    }
    requests.post(
        GRAPH_API,
        headers={
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )


# ================= ROUTES =================

@app.get("/")
def root():
    return {"status": "ok"}


@app.get("/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    if (
        params.get("hub.mode") == "subscribe"
        and params.get("hub.verify_token") == VERIFY_TOKEN
    ):
        return int(params.get("hub.challenge"))
    return "Invalid token"


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    print("INCOMING:", data)

    try:
        entry = data["entry"][0]
        change = entry["changes"][0]
        value = change["value"]

        # Ignore delivery/read statuses
        if "messages" not in value:
            return {"status": "ignored"}

        msg = value["messages"][0]
        from_user = msg["from"]
        text = msg["text"]["body"].strip().lower()

        print(f"FROM: {from_user} TEXT: {text}")

        session = user_sessions.get(from_user)

        # ===== STEP 1: ACTIVATE BOT =====
        if text == "darkbox":
            user_sessions[from_user] = {"stage": "menu"}
            send_template(from_user)      # REQUIRED first reply
            send_text(from_user, MENU_TEXT)
            return {"status": "menu_sent"}

        # Ignore messages before activation
        if not session:
            return {"status": "not_activated"}

        # ===== STEP 2: MENU SELECTION =====
        if session["stage"] == "menu":
            if text in MENU_MAP:
                user_sessions[from_user] = {
                    "stage": "awaiting_input",
                    "search_type": MENU_MAP[text],
                }
                send_text(from_user, "🔍 Send input to search:")
            else:
                send_text(from_user, "❌ Invalid choice. Reply 1–8.")
            return {"status": "menu_handled"}

        # ===== STEP 3: INPUT =====
        if session["stage"] == "awaiting_input":
            search_type = session["search_type"]

            # Placeholder response (you can plug your API here)
            result = f"✅ Search received\nType: {search_type}\nQuery: {text}"

            send_text(from_user, result)
            user_sessions.pop(from_user, None)
            return {"status": "search_done"}

    except Exception as e:
        print("ERROR:", e)

    return {"status": "ok"}
