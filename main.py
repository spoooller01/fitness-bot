import os
import requests
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage, PushMessageRequest, MessagingApiBlob
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

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# 1. จัดการข้อความตัวอักษร
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text(event):
    user_text = event.message.text
    
    # กรณีขอเปรียบเทียบรูปเก่า-ใหม่
    if "เปรียบเทียบ" in user_text:
        # ดึง 2 รูป ล่าสุดจาก Supabase
        res = supabase.table('user_logs').select('image_url').eq('type', 'progress_pic').order('created_at', desc=True).limit(2).execute()
        if len(res.data) >= 2:
            img1_url = res.data[0]['image_url'] # รูปใหม่
            img2_url = res.data[1]['image_url'] # รูปเก่า
            
            # โหลด bytes จาก URL รูปภาพ
            img1_bytes = requests.get(img1_url).content
            img2_bytes = requests.get(img2_url).content
            
            prompt = "ช่วยวิเคราะห์ความต่างของรูปร่าง 2 รูปนี้ให้หน่อยครับ รูปแรกคือรูปปัจจุบัน รูปที่สองคือรูปอดีต"
            
            response = model.generate_content([
                prompt,
                {"mime_type": "image/jpeg", "data": img1_bytes},
                {"mime_type": "image/jpeg", "data": img2_bytes}
            ])
            reply_text = response.text
        else:
            reply_text = "คุณยังมีรูปบันทึกไว้ไม่พอเปรียบเทียบครับ (ต้องมีอย่างน้อย 2 รูปในระบบ)"
    else:
        # ตอบข้อความทั่วไปพร้อม Context การเป็นเทรนเนอร์
        prompt = f"คุณคือ AI ฟิตเนสเทรนเนอร์ส่วนตัว ตอบคำถามนี้แบบให้กำลังใจ มีความรู้เรื่องโภชนาการและการออกกำลังกาย ตอบเป็นกันเอง: {user_text}"
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

# 2. จัดการรูปภาพที่ส่งเข้ามา
@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image(event):
    message_id = event.message.id
    
    # โหลดรูปภาพจาก LINE Server
    with ApiClient(configuration) as api_client:
        line_bot_blob_api = MessagingApiBlob(api_client)
        image_bytes = line_bot_blob_api.get_message_content(message_id)
        
    # บันทึกรูปลง Supabase Storage
    file_path = f"progress/{message_id}.jpg"
    supabase.storage.from_("fitness-photos").upload(
        path=file_path,
        file=image_bytes,
        file_options={"content-type": "image/jpeg"}
    )
    
    # ดึง Public URL ของรูปที่เพิ่งอัปโหลด
    image_url = supabase.storage.from_("fitness-photos").get_public_url(file_path)
    
    # ให้ Gemini วิเคราะห์รูปภาพ
    response = model.generate_content([
        "วิเคราะห์รูปนี้: ถ้าเป็นอาหาร ให้ประเมินเมนู แคลอรี และสารอาหาร (โปรตีน/คาร์บ/ไขมัน) "
        "ถ้าเป็นรูปคน/หุ่น ให้ประเมินลักษณะรูปร่าง และให้คำแนะนำฟิตเนสสั้นๆ",
        {"mime_type": "image/jpeg", "data": image_bytes}
    ])
    ai_analysis = response.text
    
    # บันทึกข้อมูลลง Database (user_logs)
    supabase.table("user_logs").insert({
        "type": "progress_pic",
        "details": ai_analysis,
        "image_url": image_url
    }).execute()
    
    # ตอบกลับผู้ใช้ทาง LINE
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                replyToken=event.replyToken,
                messages=[TextMessage(text=f"บันทึกรูปเรียบร้อยครับ! 📸\n\nผลการวิเคราะห์:\n{ai_analysis}")]
            )
        )

# 3. Endpoint สำหรับให้ Cron-job ทักมาเตือนอัตโนมัติ
@app.route("/remind", methods=['GET'])
def remind_user():
    user_id = "U6a38069ebb54d742af66cc1b09cc0ee0"
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

# วางไว้บรรทัดล่างสุดเสมอ
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
