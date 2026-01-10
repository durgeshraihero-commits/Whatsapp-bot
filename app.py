from fastapi import FastAPI, Request
import requests, os, datetime
from pymongo import MongoClient

app = FastAPI()

# ===== CONFIG =====
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
ADMIN_NUMBER = os.getenv("ADMIN_NUMBER")

SEARCH_API_URL = os.getenv("SEARCH_API_URL")
SEARCH_API_KEY = os.getenv("SEARCH_API_KEY")

GRAPH_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"

SESSION_HOURS = 5

# ===== DATABASE =====
client = MongoClient(os.getenv("MONGODB_URI"))
db = client[os.getenv("MONGODB_DBNAME")]

users = db.whatsapp_users
searches = db.whatsapp_searches
payments = db.whatsapp_payments

# ===== HELPERS =====
def send_text(to, text):
    requests.post(
        GRAPH_URL,
        headers={
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        },
        json={
            "messaging_product": "whatsapp",
            "to": to,
            "text": {"body": text}
        },
        timeout=30
    )

def forward_image_to_admin(media_id, caption):
    meta_url = f"https://graph.facebook.com/v19.0/{media_id}"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    media = requests.get(meta_url, headers=headers).json()

    requests.post(
        GRAPH_URL,
        headers=headers,
        json={
            "messaging_product": "whatsapp",
            "to": ADMIN_NUMBER,
            "type": "image",
            "image": {
                "link": media["url"],
                "caption": caption
            }
        }
    )

def is_session_active(user):
    if not user.get("darkbox_active"):
        return False
    last = user.get("last_active")
    if not last:
        return False
    return datetime.datetime.utcnow() - last < datetime.timedelta(hours=SESSION_HOURS)

def call_search_api(search_type, query):
    r = requests.post(
        SEARCH_API_URL,
        headers={
            "X-API-Key": SEARCH_API_KEY,
            "Content-Type": "application/json"
        },
        json={
            "search_type": search_type,
            "query": query
        },
        timeout=60
    )
    if r.status_code == 200:
        return r.json().get("result", "No data found")
    return "❌ Search API error"

# ===== WEBHOOK VERIFY =====
@app.get("/webhook")
async def verify(request: Request):
    p = request.query_params
    if p.get("hub.verify_token") == VERIFY_TOKEN:
        return int(p.get("hub.challenge"))
    return "Invalid token"

# ===== WEBHOOK RECEIVE =====
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()

    try:
        change = data["entry"][0]["changes"][0]["value"]

        if "statuses" in change:
            return {"ok": True}

        msg = change["messages"][0]
        wa_id = msg["from"]
        name = change["contacts"][0]["profile"]["name"]
        now = datetime.datetime.utcnow()

        user = users.find_one({"wa_id": wa_id})
        if not user:
            users.insert_one({
                "wa_id": wa_id,
                "name": name,
                "credits": 2,
                "darkbox_active": False,
                "last_active": None,
                "created_at": now
            })
            user = users.find_one({"wa_id": wa_id})

        # ===== TEXT =====
        if msg["type"] == "text":
            text = msg["text"]["body"].strip().lower()

            # EXIT
            if text == "exit":
                users.update_one({"wa_id": wa_id}, {"$set": {"darkbox_active": False}})
                send_text(wa_id, "🚪 You exited Darkbox. Send *darkbox* to start again.")
                return {"ok": True}

            # START
            if text == "darkbox":
                users.update_one(
                    {"wa_id": wa_id},
                    {"$set": {"darkbox_active": True, "last_active": now}}
                )
                send_text(
                    wa_id,
                    f"""👋 Hi {name}

Welcome to *DARKBOX* 🔐
A secret OSINT intelligence bot.

💳 Credits: {user['credits']}

Commands:
• phone <number>
• aadhar <number>
• family <number>
• vehicle <number>
• buy
• exit
"""
                )
                return {"ok": True}

            # BLOCK IF NOT ACTIVE
            if not is_session_active(user):
                return {"ok": True}

            users.update_one({"wa_id": wa_id}, {"$set": {"last_active": now}})

            # BUY
            if text == "buy":
                send_text(
                    wa_id,
                    """💳 PLANS

₹100 → 5 searches
₹200 → 15 searches
₹500 → Unlimited (7 days)

Reply:
PAY 100
PAY 200
PAY 500"""
                )
                return {"ok": True}

            if text.startswith("pay"):
                amount = text.split()[-1]
                payments.insert_one({
                    "wa_id": wa_id,
                    "amount": amount,
                    "status": "pending",
                    "created_at": now
                })
                send_text(
                    wa_id,
                    f"""💰 Payment Details

UPI: darkbox@upi
Amount: ₹{amount}

📸 Send payment screenshot."""
                )
                return {"ok": True}

            # SEARCH COMMANDS
            for cmd in ["phone", "aadhar", "family", "vehicle"]:
                if text.startswith(cmd):
                    user = users.find_one({"wa_id": wa_id})
                    if user["credits"] <= 0:
                        send_text(wa_id, "❌ No credits left. Buy a plan.")
                        return {"ok": True}

                    query = text.split()[-1]
                    users.update_one({"wa_id": wa_id}, {"$inc": {"credits": -1}})

                    result = call_search_api(cmd, query)

                    searches.insert_one({
                        "wa_id": wa_id,
                        "type": cmd,
                        "query": query,
                        "result": result[:500],
                        "time": now
                    })

                    send_text(wa_id, result)
                    return {"ok": True}

        # ===== IMAGE (PAYMENT SCREENSHOT) =====
        if msg["type"] == "image":
            if not is_session_active(user):
                return {"ok": True}

            media_id = msg["image"]["id"]
            forward_image_to_admin(
                media_id,
                f"🧾 Payment Screenshot\nUser: {wa_id}\nReply: APPROVE {wa_id}"
            )
            send_text(wa_id, "⏳ Payment received. Under review.")
            return {"ok": True}

    except Exception as e:
        print("ERROR:", e)

    return {"ok": True}
