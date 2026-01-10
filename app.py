from fastapi import FastAPI, Request
import requests, os, datetime
from pymongo import MongoClient

app = FastAPI()

# ===== CONFIG =====
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
ADMIN_NUMBER = os.getenv("ADMIN_NUMBER")

GRAPH_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"

# ===== DATABASE =====
client = MongoClient(os.getenv("MONGODB_URI"))
db = client[os.getenv("MONGODB_DBNAME")]

users = db.whatsapp_users
searches = db.whatsapp_searches
payments = db.whatsapp_payments

SESSION_HOURS = 5

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

        # Ignore status callbacks
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

        # ===== TEXT MESSAGE =====
        if msg["type"] == "text":
            text = msg["text"]["body"].strip().lower()

            # EXIT DARKBOX
            if text == "exit":
                users.update_one(
                    {"wa_id": wa_id},
                    {"$set": {"darkbox_active": False}}
                )
                send_text(wa_id, "🚪 You exited Darkbox. Send *darkbox* to start again.")
                return {"ok": True}

            # ACTIVATE DARKBOX
            if text == "darkbox":
                users.update_one(
                    {"wa_id": wa_id},
                    {"$set": {"darkbox_active": True, "last_active": now}}
                )
                user = users.find_one({"wa_id": wa_id})
                send_text(
                    wa_id,
                    f"""👋 Hi {user['name']}

Welcome to *DARKBOX* 🕵️‍♂️
A private OSINT intelligence bot.

💳 Credits: {user['credits']}

Commands:
• phone <number>
• buy
• history
• exit
"""
                )
                return {"ok": True}

            # BLOCK IF SESSION NOT ACTIVE
            if not is_session_active(user):
                return {"ok": True}

            # UPDATE LAST ACTIVE
            users.update_one(
                {"wa_id": wa_id},
                {"$set": {"last_active": now}}
            )

            # BUY PLANS
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
                    f"""💰 Payment Info

UPI: darkbox@upi
Amount: ₹{amount}

📸 Send screenshot here."""
                )
                return {"ok": True}

            # PHONE SEARCH
            if text.startswith("phone"):
                user = users.find_one({"wa_id": wa_id})
                if user["credits"] <= 0:
                    send_text(wa_id, "❌ No credits left. Buy a plan.")
                    return {"ok": True}

                query = text.split()[-1]
                users.update_one({"wa_id": wa_id}, {"$inc": {"credits": -1}})
                searches.insert_one({
                    "wa_id": wa_id,
                    "type": "phone",
                    "query": query,
                    "time": now
                })
                send_text(wa_id, f"🔍 Result for {query}\n(Data demo)")
                return {"ok": True}

        # ===== IMAGE MESSAGE =====
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
