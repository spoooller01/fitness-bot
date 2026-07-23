import os
import time
import google.generativeai as genai
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent
import google.generativeai as genai
from supabase import create_client

app = Flask(__name__)

# ดึงค่า Environment Variables
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', '').strip()
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', '').strip()
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '').strip()
SUPABASE_URL = os.getenv('SUPABASE_URL', '').strip()
SUPABASE_KEY = os.getenv('SUPABASE_KEY', '').strip()

# ตั้งค่า Clients
genai.configure(api_key=GEMINI_API_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

handler = WebhookHandler(LINE_CHANNEL_SECRET)
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

def get_gemini_response(prompt):
    try:
        # 1. ค้นหารายชื่อโมเดลทั้งหมดที่ API Key นี้มีสิทธิ์เรียกใช้จริง
        valid_models = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                # ตัดคำว่า 'models/' ออกเพื่อให้ได้ชื่อโมเดลที่ถูกต้อง
                model_name = m.name.replace('models/', '')
                valid_models.append(model_name)
        
        print(f"--- Available Models in your account: {valid_models} ---")
        
        if not valid_models:
            return "ไม่พบโมเดลที่รองรับในบัญชีนี้ กรุณาเช็ก API Key"

        # 2. ลองยิงทีละโมเดลที่มีอยู่จริง ตัวไหนผ่านใช้ตัวนั้นทันที
        last_error = None
        for model_name in valid_models:
            try:
                print(f"Trying model: {model_name}")
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(prompt)
                return response.text
            except Exception as err:
                print(f"Failed with {model_name}: {err}")
                last_error = err
                continue

        return f"ระบบ AI ขัดข้อง: {last_error}"

    except Exception as e:
        print(f"Gemini Exception: {e}")
        return f"เกิดข้อผิดพลาดในการดึงโมเดล: {str(e)}"
    
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    except Exception as e:
        print(f"Callback error: {e}")
    return 'OK', 200

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text(event):
    user_text = event.message.text
    reply_text = ""

    try:
        if "เปรียบเทียบ" in user_text:
            res = supabase.table('user_logs') \
                .select('image_url') \
                .eq('type', 'progress_pic') \
                .order('created_at', desc=True) \
                .limit(2) \
                .execute()
            
            if len(res.data) >= 2:
                img_new = res.data[0]['image_url']
                img_old = res.data[1]['image_url']
                prompt = f"วิเคราะห์ความต่างของรูปร่างจาก 2 ภาพนี้ ภาพปัจจุบัน: {img_new} ภาพอดีต: {img_old}"
                reply_text = get_gemini_response(prompt)
            else:
                reply_text = "ยังมีรูปภาพในฐานข้อมูลไม่เพียงพอสำหรับเปรียบเทียบครับ (ต้องมีอย่างน้อย 2 รูป)"
        else:
            prompt = f"คุณคือ AI เทรนเนอร์ฟิตเนส ตอบอย่างเป็นกันเอง สั้น กระชับ ให้กำลังใจ: {user_text}"
            reply_text = get_gemini_response(prompt)
            
            # บันทึกประวัติลง Supabase
            supabase.table('user_logs').insert({
                'type': 'text_chat',
                'details': f"User: {user_text} | AI: {reply_text}"
            }).execute()

    except Exception as e:
        print(f"Processing Error: {e}")
        reply_text = f"เกิดข้อผิดพลาด: {str(e)}"

    # ส่งข้อความตอบกลับ (ใช้ event.reply_token ที่แก้ไขแล้ว)
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    replyToken=event.reply_token,  # <--- แก้ไขตรงนี้เรียบร้อยครับ
                    messages=[TextMessage(text=reply_text)]
                )
            )
    except Exception as e:
        print(f"LINE Reply Error: {e}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
