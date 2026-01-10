from fastapi import FastAPI, Request
import requests, os

app = FastAPI()

VERIFY_TOKEN = "darkbox_verify"

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

GRAPH_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"

def send_text(to, text):
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }

    r = requests.post(GRAPH_URL, json=payload, headers=headers)
    print("SEND:", r.status_code, r.text)


@app.get("/webhook")
async def verify(request: Request):
    p = request.query_params
    if p.get("hub.verify_token") == VERIFY_TOKEN:
        return int(p.get("hub.challenge"))
    return "Invalid token"


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    print("INCOMING:", data)

    try:
        msg = data["entry"][0]["changes"][0]["value"]["messages"][0]
        sender = msg["from"]
        text = msg["text"]["body"]

        print("FROM:", sender, "TEXT:", text)

        # ✅ ALWAYS REPLY
        send_text(sender, "✅ Yes, working")

    except Exception as e:
        print("ERROR:", e)

    return {"ok": True}
