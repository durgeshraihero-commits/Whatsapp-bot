from fastapi import FastAPI, Request
import requests, os, datetime
from pymongo import MongoClient

app = FastAPI()

# ================= CONFIG =================
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
ADMIN_NUMBER = os.getenv("ADMIN_NUMBER")
UPI_ID = os.getenv("UPI_ID")

GRAPH_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
SESSION_HOURS = 5

# ================= DATABASE =================
client = MongoClient(os.getenv("MONGODB_URI"))
db = client[os.getenv("MONGODB_DBNAME")]

users = db.users
payments = db.payments

# ================= HELPERS =================
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
            "type": "text",
            "text": {"body": text}
        }
    )

def send_buttons(to, text, buttons):
    requests.post(
        GRAPH_URL,
        headers={
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        },
        json={
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": text},
                "action": {"buttons": buttons}
            }
        }
    )

def send_list(to, text, rows):
    requests.post(
        GRAPH_URL,
        headers={
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        },
        json={
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": text},
                "action": {
                    "button": "Select",
                    "sections": [{
                        "title": "Options",
                        "rows": rows
                    }]
                }
            }
        }
    )

def send_payment_qr(to, amount):
    qr_url = (
        f"https://api.qrserver.com/v1/create-qr-code/"
        f"?size=300x300"
        f"&data=upi://pay?pa={UPI_ID}&pn=Darkbox&am={amount}&cu=INR"
    )

    requests.post(
        GRAPH_URL,
        headers={
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        },
        json={
            "messaging_product": "whatsapp",
            "to": to,
            "type": "image",
            "image": {
                "link": qr_url,
                "caption": f"📲 Scan to pay ₹{amount}\nSend screenshot after payment."
            }
        }
    )

def forward_image_to_admin(media_id, caption):
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}

    meta = requests.get(
        f"https://graph.facebook.com/v19.0/{media_id}",
        headers=headers
    ).json()

    media_url = meta.get("url")
    if not media_url:
        return

    image_bytes = requests.get(media_url, headers=headers).content

    upload = requests.post(
        f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/media",
        headers=headers,
        files={
            "file": ("pay.jpg", image_bytes, "image/jpeg"),
            "type": (None, "image/jpeg"),
            "messaging_product": (None, "whatsapp")
        }
    ).json()

    new_media_id = upload.get("id")
    if not new_media_id:
        return

    requests.post(
        GRAPH_URL,
        headers={
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        },
        json={
            "messaging_product": "whatsapp",
            "to": ADMIN_NUMBER,
            "type": "image",
            "image": {
                "id": new_media_id,
                "caption": caption
            }
        }
    )

def is_session_active(user):
    if not user.get("darkbox_active"):
        return False
    last = user.get("last_active")
    return last and (datetime.datetime.utcnow() - last <
                     datetime.timedelta(hours=SESSION_HOURS))

# ================= WEBHOOK VERIFY =================
@app.get("/webhook")
async def verify(request: Request):
    p = request.query_params
    if p.get("hub.verify_token") == VERIFY_TOKEN:
        return int(p.get("hub.challenge"))
    return "Invalid token"

# ================= WEBHOOK =================
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()

    try:
        change = data["entry"][0]["changes"][0]["value"]

        if "statuses" in change:
            return {"ok": True}

        msg = change["messages"][0]
        wa_id = msg["from"]
        name = change.get("contacts", [{}])[0].get("profile", {}).get("name", "User")
        now = datetime.datetime.utcnow()

        user = users.find_one({"wa_id": wa_id})
        if not user:
            users.insert_one({
                "wa_id": wa_id,
                "name": name,
                "credits": 2,
                "referred": False,
                "darkbox_active": False,
                "last_active": None,
                "created_at": now
            })
            user = users.find_one({"wa_id": wa_id})

        # ===== ADMIN APPROVAL =====
        if wa_id == ADMIN_NUMBER and msg["type"] == "text":
            text = msg["text"]["body"]
            if text.startswith("APPROVE"):
                uid = text.split()[1]
                u = users.find_one({"wa_id": uid})
                if u and u.get("pending_plan"):
                    credits = u["pending_plan"]["credits"]
                    users.update_one(
                        {"wa_id": uid},
                        {"$inc": {"credits": credits}, "$unset": {"pending_plan": ""}}
                    )
                    send_text(uid, f"✅ Payment approved\n🎉 {credits} credits added")
                    send_text(ADMIN_NUMBER, "✅ Approved")
            return {"ok": True}

        # ===== TEXT =====
        if msg["type"] == "text":
            text = msg["text"]["body"].lower()

            if text.startswith("ref "):
                ref = text.split()[1]
                if not user.get("referred") and ref != wa_id:
                    users.update_one(
                        {"wa_id": ref},
                        {"$inc": {"credits": 1}}
                    )
                    users.update_one(
                        {"wa_id": wa_id},
                        {"$set": {"referred": True}}
                    )
                return {"ok": True}

            if text == "darkbox":
                users.update_one(
                    {"wa_id": wa_id},
                    {"$set": {"darkbox_active": True, "last_active": now}}
                )

                send_buttons(
                    wa_id,
                    f"Welcome *{name}*\nCredits: {user['credits']}",
                    [
                        {"type": "reply", "reply": {"id": "SEARCH_MENU", "title": "🔍 Search"}},
                        {"type": "reply", "reply": {"id": "BUY_MENU", "title": "💳 Buy Credits"}},
                        {"type": "reply", "reply": {"id": "REFER_MENU", "title": "🎁 Refer"}}
                    ]
                )
                return {"ok": True}

        # ===== INTERACTIVE =====
        if msg["type"] == "interactive":
            reply = (
                msg["interactive"].get("button_reply", {}) or
                msg["interactive"].get("list_reply", {})
            ).get("id")

            if reply == "BUY_MENU":
                send_buttons(
                    wa_id,
                    "Select plan:",
                    [
                        {"type": "reply", "reply": {"id": "PLAN_100", "title": "₹100 – 5"}},
                        {"type": "reply", "reply": {"id": "PLAN_200", "title": "₹200 – 12"}},
                        {"type": "reply", "reply": {"id": "PLAN_500", "title": "₹500 – Unlimited"}},
                    ]
                )

            if reply.startswith("PLAN_"):
                plans = {
                    "PLAN_100": {"amount": 100, "credits": 5},
                    "PLAN_200": {"amount": 200, "credits": 12},
                    "PLAN_500": {"amount": 500, "credits": 9999}
                }
                plan = plans[reply]
                users.update_one(
                    {"wa_id": wa_id},
                    {"$set": {"pending_plan": plan}}
                )
                send_payment_qr(wa_id, plan["amount"])

            if reply == "REFER_MENU":
                send_text(
                    wa_id,
                    f"🎁 Refer & Earn\n\nShare this:\nref {wa_id}\n\nYou get 1 credit when they use bot once."
                )

            return {"ok": True}

        # ===== IMAGE =====
        if msg["type"] == "image":
            media_id = msg["image"]["id"]
            forward_image_to_admin(
                media_id,
                f"🧾 Payment Screenshot\nUser: {wa_id}\nReply: APPROVE {wa_id}"
            )
            send_text(wa_id, "⏳ Screenshot received. Awaiting approval.")
            return {"ok": True}

    except Exception as e:
        print("ERROR:", e)

    return {"ok": True}
