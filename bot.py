import os
import sqlite3
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

SYSTEM_PROMPT = """你是Selina的个人助理，你的工作是帮助她管理日常生活和工作事务。
她有ADHD，所以你需要：
- 理解ADHD跳跃、随时分叉的思考和说话方式
- 留心对话走向，及时回到主线
- 在她卡住的时候给一个小到不需要意志力就能启动的下一步
- 该催她吃饭睡觉运动的时候不要客气
- 回复段落需要有长有短，过于长的段落拆分为几句发出
用中文回复，语气自然有温度。"""

# ===== SQLite 持久化 =====
DB_PATH = os.environ.get("DB_PATH", "/app/data/bot.db")

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        time_str TEXT NOT NULL,
        text TEXT NOT NULL,
        repeating INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1
    )""")
    conn.commit()
    conn.close()

def db_add_reminder(chat_id, time_str, text, repeating):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "INSERT INTO reminders (chat_id, time_str, text, repeating) VALUES (?, ?, ?, ?)",
        (chat_id, time_str, text, 1 if repeating else 0)
    )
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid

def db_remove_reminder(rid):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE reminders SET active = 0 WHERE id = ?", (rid,))
    conn.commit()
    conn.close()

def db_get_active_reminders():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id, chat_id, time_str, text, repeating FROM reminders WHERE active = 1"
    ).fetchall()
    conn.close()
    return rows

def db_deactivate_once(rid):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE reminders SET active = 0 WHERE id = ? AND repeating = 0", (rid,))
    conn.commit()
    conn.close()


# ===== 提醒回调 =====
async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.data["chat_id"]
    text = job.data["text"]
    rid = job.data["reminder_id"]
    repeating = job.data.get("repeating", False)
    await context.bot.send_message(chat_id=chat_id, text=f"⏰ 提醒：{text}")
    if not repeating:
        db_deactivate_once(rid)


def schedule_reminder(app, rid, chat_id, time_str, text, repeating):
    """注册一个 JobQueue 任务（启动时恢复 + 新增时都调用）"""
    hour, minute = map(int, time_str.split(":"))
    target_time = time(hour=hour, minute=minute, tzinfo=TZ)
    job_name = f"reminder_{rid}"
    job_data = {"chat_id": chat_id, "text": text, "reminder_id": rid, "repeating": repeating}

    if repeating:
        app.job_queue.run_daily(send_reminder, time=target_time, data=job_data, name=job_name)
    else:
        now = datetime.now(TZ)
        target_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target_dt <= now:
            target_dt += timedelta(days=1)
        delay = (target_dt - now).total_seconds()
        app.job_queue.run_once(send_reminder, when=delay, data=job_data, name=job_name)


async def post_init(app: Application):
    """启动后从数据库恢复所有活跃提醒"""
    rows = db_get_active_reminders()
    restored = 0
    for rid, chat_id, time_str, text, repeating in rows:
        try:
            schedule_reminder(app, rid, chat_id, time_str, text, bool(repeating))
            restored += 1
        except Exception as e:
            print(f"恢复提醒 #{rid} 失败: {e}")
    print(f"从数据库恢复了 {restored} 个提醒")


# ===== 命令处理 =====
async def remind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_reminder(update, context, repeating=True)

async def once_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_reminder(update, context, repeating=False)

async def _set_reminder(update, context, repeating):
    args = context.args
    if not args or len(args) < 2:
        usage = "/remind HH:MM 提醒内容" if repeating else "/once HH:MM 提醒内容"
        await update.message.reply_text(f"格式：{usage}")
        return

    time_str = args[0]
    text = " ".join(args[1:])

    try:
        hour, minute = map(int, time_str.split(":"))
        _ = time(hour=hour, minute=minute)  # 验证
    except (ValueError, IndexError):
        await update.message.reply_text("时间格式不对，用 HH:MM，比如 09:30")
        return

    chat_id = update.effective_chat.id
    rid = db_add_reminder(chat_id, time_str, text, repeating)
    schedule_reminder(context.application, rid, chat_id, time_str, text, repeating)

    if repeating:
        await update.message.reply_text(f"✅ 每日提醒 #{rid}：{time_str} — {text}")
    else:
        now = datetime.now(TZ)
        target_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target_dt <= now:
            target_dt += timedelta(days=1)
        date_label = "今天" if target_dt.date() == now.date() else "明天"
        await update.message.reply_text(f"✅ 一次性提醒 #{rid}：{date_label} {time_str} — {text}")


async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    rows = db_get_active_reminders()
    user_rows = [(rid, ts, txt, rep) for rid, cid, ts, txt, rep in rows if cid == chat_id]
    if not user_rows:
        await update.message.reply_text("当前没有提醒。")
        return
    lines = []
    for rid, ts, txt, rep in user_rows:
        rtype = "🔁 每日" if rep else "1️⃣ 一次"
        lines.append(f"#{rid}  {rtype}  {ts}  {txt}")
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

    # 检查是否存在
    rows = db_get_active_reminders()
    if not any(r[0] == rid for r in rows):
        await update.message.reply_text(f"提醒 #{rid} 不存在。")
        return

    # 从 JobQueue 移除
    job_name = f"reminder_{rid}"
    jobs = context.application.job_queue.get_jobs_by_name(job_name)
    for job in jobs:
        job.schedule_removal()

    db_remove_reminder(rid)
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

    # 把当前活跃提醒注入 system prompt，让 LLM 知道
    rows = db_get_active_reminders()
    user_rows = [(rid, ts, txt, rep) for rid, cid, ts, txt, rep in rows if cid == update.effective_chat.id]
    reminder_info = ""
    if user_rows:
        lines = []
        for rid, ts, txt, rep in user_rows:
            rtype = "每日" if rep else "一次性"
            lines.append(f"  #{rid} {rtype} {ts} {txt}")
        reminder_info = "\n\n当前已设置的提醒：\n" + "\n".join(lines)

    response = client.chat.completions.create(
        model=LLM_MODEL,
        max_tokens=1000,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT + reminder_info},
            *conversation_history[user_id]
        ]
    )

    assistant_message = response.choices[0].message.content
    conversation_history[user_id].append({"role": "assistant", "content": assistant_message})
    await update.message.reply_text(assistant_message)


def main():
    init_db()
    app = Application.builder().token(TG_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("remind", remind_cmd))
    app.add_handler(CommandHandler("once", once_cmd))
    app.add_handler(CommandHandler("reminders", list_reminders))
    app.add_handler(CommandHandler("cancel", cancel_reminder))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print(f"Bot启动 | 模型: {LLM_MODEL} | 端点: {LLM_BASE_URL} | 数据库: {DB_PATH}")
    app.run_polling()


if __name__ == "__main__":
    main()
