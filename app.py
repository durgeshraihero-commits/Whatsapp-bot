from fastapi import FastAPI, Request
import requests, os, datetime
from pymongo import MongoClient

app = FastAPI()

# ================= CONFIG =================
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
ADMIN_NUMBER = os.getenv("ADMIN_NUMBER")

SEARCH_API_URL = os.getenv("SEARCH_API_URL")
SEARCH_API_KEY = os.getenv("SEARCH_API_KEY")

GRAPH_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
SESSION_HOURS = 5

# ================= DATABASE =================
client = MongoClient(os.getenv("MONGODB_URI"))
db = client[os.getenv("MONGODB_DBNAME")]

users = db.whatsapp_users
payments = db.whatsapp_payments
searches = db.whatsapp_searches

# ================= HELPERS =================
def send_request(payload):
    r = requests.post(
        GRAPH_URL,
        headers={
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        },
        json=payload,
        timeout=30
    )
    print("SEND:", r.status_code, r.text)


def send_text(to, text):
    send_request({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
    })


# ================= MENUS =================
def send_main_menu(to, credits):
    send_request({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": f"🔐 *DARKBOX*\n\nCredits: {credits}\nChoose an option:"
            },
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "SEARCH_MENU", "title": "🔍 Search"}},
                    {"type": "reply", "reply": {"id": "BUY_MENU", "title": "💳 Buy Credits"}},
                    {"type": "reply", "reply": {"id": "MORE_MENU", "title": "☰ More"}}
                ]
            }
        }
    })


def send_search_menu(to):
    send_request({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": "Select search type:"},
            "action": {
                "button": "Choose",
                "sections": [{
                    "title": "OSINT Searches",
                    "rows": [
                        {"id": "SEARCH_PHONE", "title": "📞 Phone Number"},
                        {"id": "SEARCH_AADHAR", "title": "🆔 Aadhaar"},
                        {"id": "SEARCH_VEHICLE", "title": "🚗 Vehicle"},
                        {"id": "SEARCH_FAMILY", "title": "👨‍👩‍👦 Family"}
                    ]
                }]
            }
        }
    })


def send_buy_menu(to):
    send_request({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": "💳 Choose a plan:"},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "PAY_100", "title": "₹100 – 5 Searches"}},
                    {"type": "reply", "reply": {"id": "PAY_200", "title": "₹200 – 15 Searches"}},
                    {"type": "reply", "reply": {"id": "PAY_500", "title": "₹500 – Unlimited"}}
                ]
            }
        }
    })


def send_more_menu(to):
    send_request({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": "More options:"},
            "action": {
                "button": "Open",
                "sections": [{
                    "title": "Account",
                    "rows": [
                        {"id": "MY_CREDITS", "title": "📊 My Credits"},
                        {"id": "EXIT", "title": "🚪 Exit"}
                    ]
                }]
            }
        }
    })


# ================= LOGIC =================
def is_session_active(user):
    if not user.get("active"):
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
        json={"search_type": search_type, "query": query},
        timeout=60
    )
    if r.status_code == 200:
        return r.json().get("result", "No data found")
    return "❌ Search API error"


# ================= WEBHOOK VERIFY =================
@app.get("/webhook")
async def verify(request: Request):
    p = request.query_params
    if p.get("hub.verify_token") == VERIFY_TOKEN:
        return int(p.get("hub.challenge"))
    return "Invalid token"


# ================= WEBHOOK RECEIVE =================
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    print("RAW:", data)

    try:
        change = data["entry"][0]["changes"][0]["value"]

        if "statuses" in change:
            return {"ok": True}

        msg = change["messages"][0]
        wa_id = msg["from"]
        now = datetime.datetime.utcnow()

        user = users.find_one({"wa_id": wa_id})
        if not user:
            users.insert_one({
                "wa_id": wa_id,
                "credits": 2,
                "active": True,
                "last_active": now
            })
            user = users.find_one({"wa_id": wa_id})

        users.update_one({"wa_id": wa_id}, {"$set": {"last_active": now}})

        # ================= TEXT =================
        if msg["type"] == "text":
            text = msg["text"]["body"].strip().lower()

            if text == "darkbox":
                users.update_one(
                    {"wa_id": wa_id},
                    {"$set": {"active": True, "last_active": now}}
                )
                send_main_menu(wa_id, user["credits"])
                return {"ok": True}

            pending = user.get("pending_search")
            if not pending:
                send_main_menu(wa_id, user["credits"])
                return {"ok": True}

            if user["credits"] <= 0:
                send_text(wa_id, "❌ No credits left. Buy a plan.")
                return {"ok": True}

            search_map = {
                "SEARCH_PHONE": "phone",
                "SEARCH_AADHAR": "aadhar",
                "SEARCH_VEHICLE": "vehicle",
                "SEARCH_FAMILY": "family"
            }

            result = call_search_api(search_map[pending], text)

            users.update_one(
                {"wa_id": wa_id},
                {"$inc": {"credits": -1}, "$unset": {"pending_search": ""}}
            )

            searches.insert_one({
                "wa_id": wa_id,
                "type": search_map[pending],
                "query": text,
                "result": result[:500],
                "time": now
            })

            send_text(wa_id, result)
            send_main_menu(wa_id, user["credits"] - 1)
            return {"ok": True}

        # ================= INTERACTIVE =================
        if msg["type"] == "interactive":
            reply = msg["interactive"].get("button_reply") or msg["interactive"].get("list_reply")
            reply_id = reply.get("id")

            if reply_id == "SEARCH_MENU":
                send_search_menu(wa_id)

            elif reply_id == "BUY_MENU":
                send_buy_menu(wa_id)

            elif reply_id == "MORE_MENU":
                send_more_menu(wa_id)

            elif reply_id == "MY_CREDITS":
                send_text(wa_id, f"📊 Credits remaining: {user['credits']}")

            elif reply_id == "EXIT":
                users.update_one({"wa_id": wa_id}, {"$set": {"active": False}})
                send_text(wa_id, "🚪 You exited Darkbox.")

            elif reply_id.startswith("SEARCH_"):
                users.update_one({"wa_id": wa_id}, {"$set": {"pending_search": reply_id}})
                send_text(wa_id, "🔢 Send the number to search")

            elif reply_id.startswith("PAY_"):
                amount = reply_id.split("_")[1]
                payments.insert_one({
                    "wa_id": wa_id,
                    "amount": amount,
                    "status": "pending",
                    "time": now
                })
                send_text(
                    wa_id,
                    f"💰 Payment Details\n\nUPI: darkbox@upi\nAmount: ₹{amount}\n\n📸 Send payment screenshot"
                )

            return {"ok": True}

    except Exception as e:
        print("ERROR:", e)

    return {"ok": True}
