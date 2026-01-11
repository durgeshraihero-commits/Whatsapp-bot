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

# ================= DATABASE =================
client = MongoClient(os.getenv("MONGODB_URI"))
db = client[os.getenv("MONGODB_DBNAME")]
users = db.users

# ================= SEND HELPERS =================
def wa_post(payload):
    requests.post(
        GRAPH_URL,
        headers={
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        },
        json=payload
    )

def send_text(to, text):
    wa_post({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
    })

def send_buttons(to, text, buttons):
    wa_post({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": text},
            "action": {"buttons": buttons}
        }
    })

def send_payment_qr(to, amount):
    qr_url = (
        f"https://api.qrserver.com/v1/create-qr-code/"
        f"?size=300x300&data="
        f"upi://pay?pa={UPI_ID}&pn=Darkbox&am={amount}&cu=INR"
    )

    wa_post({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": {
            "link": qr_url,
            "caption": f"📲 Scan to pay ₹{amount}\nSend screenshot after payment."
        }
    })

# ================= IMAGE FORWARD FIX =================
def forward_image_to_admin(media_id, user_wa):
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}

    meta = requests.get(
        f"https://graph.facebook.com/v19.0/{media_id}?fields=url",
        headers=headers
    ).json()

    media_url = meta.get("url")
    if not media_url:
        print("❌ Media URL not found")
        return

    image_bytes = requests.get(media_url, headers=headers).content

    upload = requests.post(
        f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/media",
        headers=headers,
        files={
            "file": ("payment.jpg", image_bytes, "image/jpeg"),
            "type": (None, "image/jpeg"),
            "messaging_product": (None, "whatsapp")
        }
    ).json()

    new_id = upload.get("id")
    if not new_id:
        print("❌ Reupload failed")
        return

    wa_post({
        "messaging_product": "whatsapp",
        "to": ADMIN_NUMBER,
        "type": "image",
        "image": {
            "id": new_id,
            "caption": f"🧾 Payment Screenshot\nUser: {user_wa}\nReply:\nAPPROVE {user_wa}"
        }
    })

# ================= WEBHOOK VERIFY =================
@app.get("/webhook")
async def verify(request: Request):
    p = request.query_params
    if p.get("hub.verify_token") == VERIFY_TOKEN:
        return int(p.get("hub.challenge"))
    return "Invalid"

# ================= WEBHOOK =================
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()

    try:
        value = data["entry"][0]["changes"][0]["value"]

        if "statuses" in value:
            return {"ok": True}

        msg = value["messages"][0]
        wa_id = msg["from"]
        now = datetime.datetime.utcnow()

        user = users.find_one({"wa_id": wa_id})
        if not user:
            users.insert_one({
                "wa_id": wa_id,
                "credits": 2,
                "referred": False,
                "awaiting_search": False,
                "pending_plan": None,
                "created_at": now
            })
            user = users.find_one({"wa_id": wa_id})

        # ========== ADMIN ==========
        if wa_id == ADMIN_NUMBER and msg["type"] == "text":
            text = msg["text"]["body"]
            if text.startswith("APPROVE"):
                uid = text.split()[1]
                u = users.find_one({"wa_id": uid})
                if u and u.get("pending_plan"):
                    credits = u["pending_plan"]["credits"]
                    users.update_one(
                        {"wa_id": uid},
                        {"$inc": {"credits": credits}, "$set": {"pending_plan": None}}
                    )
                    send_text(uid, f"✅ Payment approved\n🎉 {credits} credits added")
                    send_text(ADMIN_NUMBER, "✅ Approved")
            return {"ok": True}

        # ========== IMAGE ==========
        if msg["type"] == "image":
            forward_image_to_admin(msg["image"]["id"], wa_id)
            send_text(wa_id, "⏳ Screenshot received. Awaiting approval.")
            return {"ok": True}

        # ========== TEXT ==========
        if msg["type"] == "text":
            text = msg["text"]["body"].lower()

            if user.get("awaiting_search"):
                users.update_one(
                    {"wa_id": wa_id},
                    {"$inc": {"credits": -1}, "$set": {"awaiting_search": False}}
                )
                send_text(
                    wa_id,
                    f"🔍 Searching for:\n{text}\n\n✅ Search completed.\n1 credit deducted."
                )
                return {"ok": True}

            if text.startswith("ref "):
                ref = text.split()[1]
                if not user["referred"] and ref != wa_id:
                    users.update_one({"wa_id": ref}, {"$inc": {"credits": 1}})
                    users.update_one({"wa_id": wa_id}, {"$set": {"referred": True}})
                return {"ok": True}

            if text == "darkbox":
                send_buttons(
                    wa_id,
                    f"Welcome 👋\nCredits: {user['credits']}",
                    [
                        {"type": "reply", "reply": {"id": "SEARCH", "title": "🔍 Search"}},
                        {"type": "reply", "reply": {"id": "BUY", "title": "💳 Buy Credits"}},
                        {"type": "reply", "reply": {"id": "REFER", "title": "🎁 Refer"}}
                    ]
                )
                return {"ok": True}

        # ========== INTERACTIVE ==========
        if msg["type"] == "interactive":
            reply = msg["interactive"].get("button_reply", {}).get("id")

            if reply == "SEARCH":
                if user["credits"] <= 0:
                    send_text(wa_id, "❌ No credits left.")
                else:
                    send_text(wa_id, "Send search query:")
                    users.update_one({"wa_id": wa_id}, {"$set": {"awaiting_search": True}})

            if reply == "BUY":
                send_buttons(
                    wa_id,
                    "Select plan:",
                    [
                        {"type": "reply", "reply": {"id": "P100", "title": "₹100 – 5"}},
                        {"type": "reply", "reply": {"id": "P200", "title": "₹200 – 12"}},
                        {"type": "reply", "reply": {"id": "P500", "title": "₹500 – Unlimited"}}
                    ]
                )

            if reply in ["P100", "P200", "P500"]:
                plans = {
                    "P100": {"amount": 100, "credits": 5},
                    "P200": {"amount": 200, "credits": 12},
                    "P500": {"amount": 500, "credits": 9999}
                }
                plan = plans[reply]
                users.update_one({"wa_id": wa_id}, {"$set": {"pending_plan": plan}})
                send_payment_qr(wa_id, plan["amount"])

            if reply == "REFER":
                send_text(
                    wa_id,
                    f"🎁 Refer & Earn\n\nShare:\nref {wa_id}\n\nYou get 1 credit when they use bot once."
                )

    except Exception as e:
        print("ERROR:", e)

    return {"ok": True}
