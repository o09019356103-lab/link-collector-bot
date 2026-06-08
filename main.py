import os
import json
import re
import time
import requests
import anthropic
from flask import Flask, request, jsonify

app = Flask(__name__)

LARK_APP_ID = os.environ.get("LARK_APP_ID")
LARK_APP_SECRET = os.environ.get("LARK_APP_SECRET")
BITABLE_APP_TOKEN = os.environ.get("BITABLE_APP_TOKEN")
TABLE_ID = os.environ.get("TABLE_ID")

processed_events = set()

def get_tenant_token():
   url = "https://open.larksuite.com/open-apis/auth/v3/tenant_access_token/internal"
   res = requests.post(url, json={"app_id": LARK_APP_ID, "app_secret": LARK_APP_SECRET})
   return res.json().get("tenant_access_token")

def get_youtube_thumbnail(url_text):
   match = re.search(r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})", url_text)
   if match:
       video_id = match.group(1)
       return f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
   return None

def upload_image_to_lark(image_url, token):
   try:
       img_data = requests.get(image_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"}).content
       if len(img_data) < 1000:
           print("SKIP: image too small")
           return None
       url = "https://open.larksuite.com/open-apis/drive/v1/medias/upload_all"
       headers = {"Authorization": f"Bearer {token}"}
       files = {"file": ("thumbnail.jpg", img_data, "image/jpeg")}
       data = {"file_name": "thumbnail.jpg", "parent_type": "bitable_image", "parent_node": BITABLE_APP_TOKEN, "size": str(len(img_data))}
       res = requests.post(url, headers=headers, files=files, data=data)
       return res.json().get("data", {}).get("file_token")
   except Exception as e:
       print("UPLOAD ERROR:", e)
       return None

def is_url_already_registered(url_text):
   token = get_tenant_token()
   search_url = f"https://open.larksuite.com/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{TABLE_ID}/records"
   headers = {"Authorization": f"Bearer {token}"}
   params = {"page_size": 100}
   res = requests.get(search_url, headers=headers, params=params)
   items = res.json().get("data", {}).get("items", [])

   def base_url(url):
       return url.split("?")[0].rstrip("/")

   for item in items:
       url_field = item.get("fields", {}).get("URL", {})
       existing_url = url_field.get("link", "") if isinstance(url_field, dict) else ""
       if base_url(existing_url) == base_url(url_text):
           return True
   return False

def ai_summarize(title, description, url):
   try:
       client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
       prompt = f"""以下のWebページの情報を元に、日本語で答えてください。

URL: {url}
タイトル: {title}
説明: {description}

以下の2つをJSON形式のみで返してください。前後に余分なテキストや```は不要です：
{{"clean_title": "タイトルをシンプルに整形したもの（- YouTubeなどの余分な文字を除く）", "summary": "内容を日本語で2〜3文で要約したもの"}}"""

       message = client.messages.create(
           model="claude-haiku-4-5-20251001",
           max_tokens=300,
           messages=[{"role": "user", "content": prompt}]
       )
       text = message.content[0].text.strip()
       text = re.sub(r'```json|```', '', text).strip()
       result = json.loads(text)
       return result.get("clean_title", title), result.get("summary", description)
   except Exception as e:
       print("AI ERROR:", e)
       return title, description

def add_record(url_text):
   if is_url_already_registered(url_text):
       print("SKIP: already registered:", url_text)
       return

   token = get_tenant_token()
   api_url = f"https://open.larksuite.com/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{TABLE_ID}/records"
   headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

   title = url_text
   summary = ""
   og_image_url = None

   try:
       r = requests.get(url_text, timeout=5, headers={"User-Agent": "Mozilla/5.0"})

       match = re.search(r'<title>(.*?)</title>', r.text)
       if match:
           title = match.group(1).strip()

       og_desc = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']', r.text)
       if not og_desc:
           og_desc = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', r.text)
       if og_desc:
           summary = og_desc.group(1).strip()

       og_img = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](.*?)["\']', r.text)
       if og_img:
           og_image_url = og_img.group(1).strip()

   except:
       pass

   # AIでタイトル整形・日本語要約
   title, summary = ai_summarize(title, summary, url_text)

   now_ms = int(time.time() * 1000)
   fields = {
       "テキスト": title,
       "URL": {"link": url_text, "text": url_text},
       "日付": now_ms,
       "要約": summary
   }

   image_url = og_image_url or get_youtube_thumbnail(url_text)
   if image_url:
       file_token = upload_image_to_lark(image_url, token)
       if file_token:
           fields["サムネ"] = [{"file_token": file_token}]

   res = requests.post(api_url, headers=headers, json={"fields": fields})
   print("BITABLE RESULT:", res.status_code, res.text)

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

   event_id = body.get("header", {}).get("event_id", "")
   if event_id and event_id in processed_events:
       print("SKIP: duplicate event:", event_id)
       return jsonify({"code": 0})
   if event_id:
       processed_events.add(event_id)

   event = body.get("event", {})
   message = event.get("message", {})
   chat_id = message.get("chat_id")

   try:
       content = json.loads(message.get("content", "{}"))
       text = content.get("text", "").strip()
       if text.startswith("http"):
           add_record(text)
           send_message(chat_id, "✅ Baseに登録しました！")
   except Exception as e:
       print("ERROR:", e)

   return jsonify({"code": 0})

if __name__ == "__main__":
   app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
