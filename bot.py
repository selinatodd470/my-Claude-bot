import os
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from openai import OpenAI

# ===== 环境变量（Railway 中配置，切换供应商只改这三个） =====
TG_TOKEN = os.environ.get("TG_TOKEN")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL")      # e.g. https://api.openai.com/v1
LLM_API_KEY = os.environ.get("LLM_API_KEY")
LLM_MODEL = os.environ.get("LLM_MODEL", "claude-sonnet-4-20250514")  # 默认值可改

client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)

# 每个用户的对话历史
conversation_history = {}

SYSTEM_PROMPT = """你是Selina的个人助理，你的工作是帮助她管理日常生活和工作事务。
她有ADHD，所以你需要：
- 理解ADHD跳跃、随时分叉的思考和说话方式
- 留心对话走向，及时回到主线
- 在她卡住的时候给一个小到不需要意志力就能启动的下一步
- 该催她吃饭睡觉运动的时候不要客气
- 回复段落需要有长有短，过于长的段落拆分为几句发出
用中文回复，语气自然有温度。"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("在的。")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text

    if user_id not in conversation_history:
        conversation_history[user_id] = []

    conversation_history[user_id].append({
        "role": "user",
        "content": user_message
    })

    # 只保留最近20条，避免超出context
    if len(conversation_history[user_id]) > 20:
        conversation_history[user_id] = conversation_history[user_id][-20:]

    response = client.chat.completions.create(
        model=LLM_MODEL,
        max_tokens=1000,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            *conversation_history[user_id]
        ]
    )

    assistant_message = response.choices[0].message.content

    conversation_history[user_id].append({
        "role": "assistant",
        "content": assistant_message
    })

    await update.message.reply_text(assistant_message)

def main():
    app = Application.builder().token(TG_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print(f"Bot启动 | 模型: {LLM_MODEL} | 端点: {LLM_BASE_URL}")
    app.run_polling()

if __name__ == "__main__":
    main()
