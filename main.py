import os
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent
import google.generativeai as genai
from supabase import create_client

app = Flask(__name__)

# 1. ดึง Keys ทั้งหมดจาก Environment Variables
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', '').strip()
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', '').strip()
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '').strip()
SUPABASE_URL = os.getenv('SUPABASE_URL', '').strip()
SUPABASE_KEY = os.getenv('SUPABASE_KEY', '').strip()

# 2. ตั้งค่าการเชื่อมต่อ Clients
genai.configure(api_key=GEMINI_API_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

handler = WebhookHandler(LINE_CHANNEL_SECRET)
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

# ฟังก์ชันเลือกเรียกใช้ Gemini API
def get_gemini_response(prompt):
    try:
        model = genai.GenerativeModel('gemini-2.0-flash')
        return model.generate_content(prompt).text
    except Exception:
        model = genai.GenerativeModel('gemini-1.5-flash')
        return model.generate_content(prompt).text

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
    user_id = event.source.user_id
    reply_text = ""

    try:
        # กรณีผู้ใช้พิมพ์ขอเปรียบเทียบรูป
        if "เปรียบเทียบ" in user_text:
            # ดึงข้อมูล API จาก Supabase Database (ดึง 2 รูปล่าสุดของคุณ)
            res = supabase.table('user_logs') \
                .select('image_url') \
                .eq('type', 'progress_pic') \
                .order('created_at', desc=True) \
                .limit(2) \
                .execute()
            
            if len(res.data) >= 2:
                img_new = res.data[0]['image_url']
                img_old = res.data[1]['image_url']
                prompt = f"วิเคราะห์ความเปลี่ยนไปของรูปร่างจาก 2 ภาพนี้ ภาพปัจจุบัน: {img_new} ภาพอดีต: {img_old}"
                reply_text = get_gemini_response(prompt)
            else:
                reply_text = "ยังมีรูปภาพในฐานข้อมูลไม่เพียงพอสำหรับเปรียบเทียบครับ (ต้องมีอย่างน้อย 2 รูป)"
        
        else:
            # ตอบคำถามทั่วไป และบันทึกข้อความลง Database
            prompt = f"คุณคือ AI เทรนเนอร์ฟิตเนส ให้คำแนะนำอย่างเป็นกันเองและสั้นกระชับ: {user_text}"
            reply_text = get_gemini_response(prompt)
            
            # บันทึกประวัติการคุยลง Supabase
            supabase.table('user_logs').insert({
                'type': 'text_chat',
                'details': f"User: {user_text} | AI: {reply_text}"
            }).execute()

    except Exception as e:
        print(f"Error: {e}")
        reply_text = f"เกิดข้อผิดพลาดในการประมวลผล: {str(e)}"

    # ส่งข้อความตอบกลับ LINE
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    replyToken=event.replyToken,
                    messages=[TextMessage(text=reply_text)]
                )
            )
    except Exception as e:
        print(f"LINE Reply Error: {e}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
