import os
import json
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

LARK_APP_ID = os.environ.get("LARK_APP_ID")
LARK_APP_SECRET = os.environ.get("LARK_APP_SECRET")
BITABLE_APP_TOKEN = os.environ.get("BITABLE_APP_TOKEN")
TABLE_ID = os.environ.get("TABLE_ID")

def get_tenant_token():
    url = "https://open.larksuite.com/open-apis/auth/v3/tenant_access_token/internal"
    res = requests.post(url, json={"app_id": LARK_APP_ID, "app_secret": LARK_APP_SECRET})
    return res.json().get("tenant_access_token")

def add_record(url_text):
    token = get_tenant_token()
    url = f"https://open.larksuite.com/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{TABLE_ID}/records"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data = {"fields": {"URL": url_text}}
    requests.post(url, headers=headers, json=data)

def send_message(chat_id, text):
    token = get_tenant_token()
    url = "https://open.larksuite.com/open-apis/im/v1/messages?receive_id_type=chat_id"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data = {"receive_id": chat_id, "msg_type": "text", "content": json.dumps({"text": text})}
    requests.post(url, headers=headers, json=data)

@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_json()
    if body.get("type") == "url_verification":
        return jsonify({"challenge": body.get("challenge")})
    event = body.get("event", {})
    message = event.get("message", {})
    msg_type = message.get("msg_type", "")
    chat_id = message.get("chat_id")
    
    url_text = None
    
    if msg_type == "text":
        content = json.loads(message.get("content", "{}"))
        text = content.get("text", "").strip()
        if text.startswith("http"):
            url_text = text
    
    if not url_text:
        try:
            content = json.loads(message.get("content", "{}"))
            url_text = content.get("url", {}).get("url") or content.get("text", "").strip()
        except:
            pass
    
    if url_text and url_text.startswith("http"):
        add_record(url_text)
        send_message(chat_id, "✅ Baseに登録しました！")
    
    return jsonify({"code": 0})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
