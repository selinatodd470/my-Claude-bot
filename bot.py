import os
from datetime import datetime, time, timedelta, timezone
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from openai import OpenAI

# ===== 环境变量 =====
TG_TOKEN = os.environ.get("TG_TOKEN")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL")
LLM_API_KEY = os.environ.get("LLM_API_KEY")
LLM_MODEL = os.environ.get("LLM_MODEL", "claude-sonnet-4-20250514")

client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)

TZ = timezone(timedelta(hours=8))  # Asia/Shanghai

conversation_history = {}
reminders = {}  # {reminder_id: {chat_id, time_str, text, job_name, repeating}}
reminder_counter = 0

SYSTEM_PROMPT = """你是Selina的个人助理，你的工作是帮助她管理日常生活和工作事务。
她有ADHD，所以你需要：
- 理解ADHD跳跃、随时分叉的思考和说话方式
- 留心对话走向，及时回到主线
- 在她卡住的时候给一个小到不需要意志力就能启动的下一步
- 该催她吃饭睡觉运动的时候不要客气
- 回复段落需要有长有短，过于长的段落拆分为几句发出
用中文回复，语气自然有温度。"""


async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.data["chat_id"]
    text = job.data["text"]
    reminder_id = job.data["reminder_id"]
    await context.bot.send_message(chat_id=chat_id, text=f"⏰ 提醒：{text}")
    if not job.data.get("repeating", False):
        reminders.pop(reminder_id, None)


async def remind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """每日重复提醒：/remind HH:MM 提醒内容"""
    await _set_reminder(update, context, repeating=True)


async def once_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """一次性提醒：/once HH:MM 提醒内容"""
    await _set_reminder(update, context, repeating=False)


async def _set_reminder(update, context, repeating):
    global reminder_counter
    args = context.args
    if not args or len(args) < 2:
        usage = "/remind HH:MM 提醒内容" if repeating else "/once HH:MM 提醒内容"
        await update.message.reply_text(f"格式：{usage}")
        return

    time_str = args[0]
    text = " ".join(args[1:])

    try:
        hour, minute = map(int, time_str.split(":"))
        target_time = time(hour=hour, minute=minute, tzinfo=TZ)
    except (ValueError, IndexError):
        await update.message.reply_text("时间格式不对，用 HH:MM，比如 09:30")
        return

    reminder_counter += 1
    rid = reminder_counter
    chat_id = update.effective_chat.id
    job_name = f"reminder_{rid}"
    job_data = {"chat_id": chat_id, "text": text, "reminder_id": rid, "repeating": repeating}

    if repeating:
        context.application.job_queue.run_daily(
            send_reminder, time=target_time, data=job_data, name=job_name
        )
        reminders[rid] = {"chat_id": chat_id, "time_str": time_str, "text": text, "job_name": job_name, "repeating": True}
        await update.message.reply_text(f"✅ 每日提醒 #{rid}：{time_str} — {text}")
    else:
        now = datetime.now(TZ)
        target_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target_dt <= now:
            target_dt += timedelta(days=1)
        delay = (target_dt - now).total_seconds()
        context.application.job_queue.run_once(
            send_reminder, when=delay, data=job_data, name=job_name
        )
        date_label = "今天" if target_dt.date() == now.date() else "明天"
        reminders[rid] = {"chat_id": chat_id, "time_str": time_str, "text": text, "job_name": job_name, "repeating": False}
        await update.message.reply_text(f"✅ 一次性提醒 #{rid}：{date_label} {time_str} — {text}")


async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_reminders = {k: v for k, v in reminders.items() if v["chat_id"] == chat_id}
    if not user_reminders:
        await update.message.reply_text("当前没有提醒。")
        return
    lines = []
    for rid, r in user_reminders.items():
        rtype = "🔁 每日" if r["repeating"] else "1️⃣ 一次"
        lines.append(f"#{rid}  {rtype}  {r['time_str']}  {r['text']}")
    await update.message.reply_text("当前提醒：\n" + "\n".join(lines))


async def cancel_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("格式：/cancel 编号")
        return
    try:
        rid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("编号必须是数字。")
        return
    if rid not in reminders:
        await update.message.reply_text(f"提醒 #{rid} 不存在。")
        return
    job_name = reminders[rid]["job_name"]
    jobs = context.application.job_queue.get_jobs_by_name(job_name)
    for job in jobs:
        job.schedule_removal()
    del reminders[rid]
    await update.message.reply_text(f"已取消提醒 #{rid}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "在的。\n\n"
        "提醒命令：\n"
        "/remind HH:MM 内容 — 每日提醒\n"
        "/once HH:MM 内容 — 一次性提醒\n"
        "/reminders — 查看所有提醒\n"
        "/cancel 编号 — 取消提醒"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text

    if user_id not in conversation_history:
        conversation_history[user_id] = []

    conversation_history[user_id].append({"role": "user", "content": user_message})

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
    conversation_history[user_id].append({"role": "assistant", "content": assistant_message})
    await update.message.reply_text(assistant_message)


def main():
    app = Application.builder().token(TG_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("remind", remind_cmd))
    app.add_handler(CommandHandler("once", once_cmd))
    app.add_handler(CommandHandler("reminders", list_reminders))
    app.add_handler(CommandHandler("cancel", cancel_reminder))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print(f"Bot启动 | 模型: {LLM_MODEL} | 端点: {LLM_BASE_URL}")
    app.run_polling()


if __name__ == "__main__":
    main()
