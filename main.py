import os
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage, PushMessageRequest
from linebot.v3.webhooks import MessageEvent, TextMessageContent, ImageMessageContent
import google.generativeai as genai
from supabase import create_client

app = Flask(__name__)

# Config ค่าต่างๆ จาก environment variables
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# ตั้งค่า Client
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

handler = WebhookHandler(LINE_CHANNEL_SECRET)
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

# 1. รับข้อความและวิเคราะห์
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text(event):
    user_text = event.message.text
    
    # กรณีขอเปรียบเทียบรูปเก่า-ใหม่
    if "เปรียบเทียบ" in user_text:
        # ดึง 2 รูปโรล่าสุดจาก Supabase
        res = supabase.table('user_logs').select('image_url').eq('type', 'progress_pic').order('created_at', desc=True).limit(2).execute()
        if len(res.data) >= 2:
            img1_url = res.data[0]['image_url'] # รูปใหม่
            img2_url = res.data[1]['image_url'] # รูปเก่า
            
            prompt = "ช่วยวิเคราะห์ความต่างของรูปร่าง 2 รูปนี้ให้หน่อยครับ รูปแรกคือรูปปัจจุบัน รูปที่สองคือรูปอดีต"
            # ส่งรูปและ prompt ให้ Gemini วิเคราะห์...
            response = model.generate_content([prompt, img1_url, img2_url])
            reply_text = response.text
        else:
            reply_text = "คุณยังมีรูปบันทึกไว้ไม่พอเปรียบเทียบครับ (ต้องมีอย่างน้อย 2 รูป)"
    else:
        # ตอบข้อความทั่วไปพร้อม Context การเป็นเทรนเนอร์
        prompt = f"คุณคือ AI ฟิตเนสเทรนเนอร์ส่วนตัว ตอบคำถามนี้แบบให้กำลังใจและเป็นกันเอง: {user_text}"
        response = model.generate_content(prompt)
        reply_text = response.text

    # ส่งข้อความกลับ LINE
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                replyToken=event.replyToken,
                messages=[TextMessage(text=reply_text)]
            )
        )

# 2. Endpoint สำหรับให้ Cron-job ทักมาเตือนอัตโนมัติ
@app.route("/remind", methods=['GET'])
def remind_user():
    user_id = "YOUR_LINE_USER_ID" # ใส่ User ID ของคุณ
    reminder_msg = "อย่าลืมอัปเดตมื้ออาหารเย็น และชั่งน้ำหนักวันนี้ด้วยนะ!"
    
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.push_message(
            PushMessageRequest(
                to=user_id,
                messages=[TextMessage(text=reminder_msg)]
            )
        )
    return "Reminded!", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
