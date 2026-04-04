import os
import asyncio
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
import anthropic

# 从环境变量读取，不要在代码里直接填key
TG_TOKEN = os.environ.get("TG_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

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

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=conversation_history[user_id]
    )

    assistant_message = response.content[0].text

    conversation_history[user_id].append({
        "role": "assistant",
        "content": assistant_message
    })

    await update.message.reply_text(assistant_message)

def main():
    app = Application.builder().token(TG_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot启动了")
    app.run_polling()

if __name__ == "__main__":
    main()
