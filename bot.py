import os
import logging
import sqlite3
import asyncio
import html
import json
from datetime import datetime, time, timedelta, timezone
from openai import OpenAI
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from telegram.constants import ParseMode
from sleep_schema import WAKEUP_TOOLS
from notion_sleep import log_wakeup_record

# ═══ 环境变量 ═══
TG_TOKEN    = os.environ["TG_TOKEN"]
LLM_API_KEY = os.environ["LLM_API_KEY"]
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL   = os.environ.get("LLM_MODEL", "gpt-4o-mini")
DB_PATH     = os.environ.get("DB_PATH", "/app/data/bot.db")
MY_CHAT_ID  = int(os.environ.get("MY_CHAT_ID", "0"))
TZ          = timezone(timedelta(hours=8))  # Asia/Shanghai

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

SYSTEM_PROMPT_BASE = (
    "你是Selina的个人助理，你的工作是帮助她管理日常生活和工作事务。"
    "她有ADHD，所以你需要："
    "理解ADHD跳跃、随时分叉的思考和说话方式；"
    "留心对话走向，及时回到主线；"
    "在她卡住的时候给一个小到不需要意志力就能启动的下一步；"
    "该催她吃饭睡觉运动的时候不要客气。"
    "用中文回复，语气自然有温度。"
    "重要：你的回复会被逐条发送到聊天窗口。请在需要分段的地方插入 |||，"
    "每个 ||| 之间的内容会作为一条独立消息发出。不要在开头或结尾放 |||。"
)

# ═══ 内存数据 ═══
conversation_history: dict[int, list] = {}
conversation_summaries: dict[int, str] = {}
last_message_time: dict[int, datetime] = {}
HISTORY_MAX = 30
HISTORY_KEEP = 20
SUMMARY_MAX_CHARS = 300

# ═══════════════════════════════════════
#  SQLite 初始化
# ═══════════════════════════════════════
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        time_str TEXT NOT NULL,
        text TEXT NOT NULL,
        repeating INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS conversation_summaries (
        user_id INTEGER PRIMARY KEY,
        summary TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS last_message_times (
        user_id INTEGER PRIMARY KEY,
        timestamp TEXT NOT NULL
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

def save_summary_to_db(user_id, summary):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO conversation_summaries (user_id, summary, updated_at) VALUES (?, ?, ?)",
        (user_id, summary, datetime.now(TZ).isoformat()),
    )
    conn.commit()
    conn.close()

def load_all_summaries():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT user_id, summary FROM conversation_summaries").fetchall()
    conn.close()
    return {uid: s for uid, s in rows}

def save_last_message_time(user_id, dt):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO last_message_times (user_id, timestamp) VALUES (?, ?)",
        (user_id, dt.isoformat()),
    )
    conn.commit()
    conn.close()

def load_all_last_message_times():
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute("SELECT user_id, timestamp FROM last_message_times").fetchall()
    except sqlite3.OperationalError:
        rows = []
    conn.close()
    result = {}
    for uid, ts in rows:
        try:
            result[uid] = datetime.fromisoformat(ts)
        except ValueError:
            pass
    return result

# ═══════════════════════════════════════
#  自然提醒（LLM 美化 + 写入对话历史）
# ═══════════════════════════════════════
async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.data["chat_id"]
    text = job.data["text"]
    rid = job.data["reminder_id"]
    repeating = job.data.get("repeating", False)

    now_str = datetime.now(TZ).strftime("%H:%M")
    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": (
                    "你是Selina的个人助理。现在需要提醒她一件事。"
                    "用简短、自然、有温度的语气提醒，像朋友发消息一样。"
                    "不要用'提醒'这个词开头，不要加emoji前缀。"
                    "一两句话就好，可以根据时间点加点关心的话。"
                    f"当前时间：{now_str}"
                )},
                {"role": "user", "content": f"提醒内容：{text}"}
            ],
            max_tokens=150,
        )
        natural_text = resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"提醒美化失败: {e}")
        natural_text = f"⏰ {text}"

    await context.bot.send_message(chat_id=chat_id, text=natural_text)

    user_id = chat_id
    if user_id not in conversation_history:
        conversation_history[user_id] = []
    conversation_history[user_id].append({"role": "assistant", "content": natural_text})

    if not repeating:
        db_deactivate_once(rid)

def schedule_reminder(app, rid, chat_id, time_str, text, repeating):
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

# ═══════════════════════════════════════
#  主动消息
# ═══════════════════════════════════════
async def proactive_message(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.data["chat_id"]
    msg_type = job.data["type"]
    user_id = job.data.get("user_id", chat_id)

    summary = conversation_summaries.get(user_id, "")
    now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M %A")

    type_hints = {
        "morning": "早安问候，简短温暖，可以提一下今天是星期几，如果有最近聊过的事可以提一句",
        "lunch": "提醒吃午饭喝水，轻松自然，别太啰嗦",
        "goodnight": "提醒该睡觉了，温柔但坚定，如果很晚了语气可以更直接",
    }
    hint = type_hints.get(msg_type, "自然地打个招呼")

    prompt = (
        f"你是Selina的个人助理。现在需要主动给她发一条消息。\n"
        f"场景：{hint}\n"
        f"当前时间：{now_str}\n"
        f"{'她最近聊过的内容摘要：' + summary if summary else '（暂无最近对话记录）'}\n\n"
        f"要求：\n"
        f"- 一两句话就好，像朋友发微信\n"
        f"- 可以结合她最近聊的内容，但不要硬凑\n"
        f"- 不要用'提醒'这个词\n"
        f"- 根据前面聊过的话题，必要时加emoji前缀"
    )

    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT_BASE},
                {"role": "user", "content": prompt}
            ],
            max_tokens=150,
        )
        text = resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"主动消息生成失败: {e}")
        return

    await context.bot.send_message(chat_id=chat_id, text=text)

    if user_id not in conversation_history:
        conversation_history[user_id] = []
    conversation_history[user_id].append({"role": "assistant", "content": text})

async def silence_check(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.data["chat_id"]
    user_id = job.data.get("user_id", chat_id)

    now = datetime.now(TZ)
    if now.hour < 10 or now.hour >= 22:
        return

    last_time = last_message_time.get(user_id)
    if last_time is None:
        return

    silence_hours = (now - last_time).total_seconds() / 3600
    if silence_hours < 4:
        return

    summary = conversation_summaries.get(user_id, "")
    now_str = now.strftime("%H:%M")

    prompt = (
        f"你是Selina的个人助理。她已经 {silence_hours:.0f} 小时没有发消息了。\n"
        f"现在是 {now_str}，主动关心一下她。\n"
        f"{'她最近聊过的内容摘要：' + summary if summary else ''}\n\n"
        f"要求：\n"
        f"- 简短自然，一句话就好\n"
        f"- 可以问她在忙什么，或者提醒她休息一下\n"
        f"- 不要太正式，像朋友发消息"
    )

    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT_BASE},
                {"role": "user", "content": prompt}
            ],
            max_tokens=100,
        )
        text = resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"沉默检测消息生成失败: {e}")
        return

    await context.bot.send_message(chat_id=chat_id, text=text)

    if user_id not in conversation_history:
        conversation_history[user_id] = []
    conversation_history[user_id].append({"role": "assistant", "content": text})
    last_message_time[user_id] = now
    save_last_message_time(user_id, now)

# ═══════════════════════════════════════
#  启动初始化
# ═══════════════════════════════════════
async def post_init(app: Application):
    summaries = load_all_summaries()
    conversation_summaries.update(summaries)
    logger.info(f"从数据库恢复了 {len(summaries)} 条对话摘要")

    times = load_all_last_message_times()
    last_message_time.update(times)
    logger.info(f"从数据库恢复了 {len(times)} 条最后消息时间")

    rows = db_get_active_reminders()
    restored = 0
    for rid, chat_id, time_str, text, repeating in rows:
        try:
            schedule_reminder(app, rid, chat_id, time_str, text, bool(repeating))
            restored += 1
        except Exception as e:
            logger.error(f"恢复提醒 #{rid} 失败: {e}")
    logger.info(f"从数据库恢复了 {restored} 个提醒")

    if MY_CHAT_ID:
        uid = MY_CHAT_ID
        app.job_queue.run_daily(
            proactive_message,
            time=time(hour=8, minute=0, tzinfo=TZ),
            data={"chat_id": uid, "user_id": uid, "type": "morning"},
            name="proactive_morning"
        )
        app.job_queue.run_daily(
            proactive_message,
            time=time(hour=12, minute=30, tzinfo=TZ),
            data={"chat_id": uid, "user_id": uid, "type": "lunch"},
            name="proactive_lunch"
        )
        app.job_queue.run_daily(
            proactive_message,
            time=time(hour=23, minute=30, tzinfo=TZ),
            data={"chat_id": uid, "user_id": uid, "type": "goodnight"},
            name="proactive_goodnight"
        )
        app.job_queue.run_repeating(
            silence_check,
            interval=3600,
            first=60,
            data={"chat_id": uid, "user_id": uid},
            name="silence_check"
        )
        logger.info(f"已注册主动消息任务，chat_id={uid}")

# ═══════════════════════════════════════
#  提醒命令
# ═══════════════════════════════════════
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
        _ = time(hour=hour, minute=minute)
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
    rows = db_get_active_reminders()
    if not any(r[0] == rid for r in rows):
        await update.message.reply_text(f"提醒 #{rid} 不存在。")
        return
    job_name = f"reminder_{rid}"
    jobs = context.application.job_queue.get_jobs_by_name(job_name)
    for job in jobs:
        job.schedule_removal()
    db_remove_reminder(rid)
    await update.message.reply_text(f"已取消提醒 #{rid}")

# ═══════════════════════════════════════
#  对话摘要滚动
# ═══════════════════════════════════════
def summarize_conversation(user_id: int):
    history = conversation_history.get(user_id, [])
    if len(history) <= HISTORY_MAX:
        return
    old_messages = history[:-HISTORY_KEEP]
    keep_messages = history[-HISTORY_KEEP:]
    old_text = "\n".join(
        f"{'用户' if m['role'] == 'user' else '助手'}: {m['content']}"
        for m in old_messages
    )
    existing_summary = conversation_summaries.get(user_id, "")
    prompt = (
        f"请将以下对话压缩为不超过{SUMMARY_MAX_CHARS}字的中文摘要，"
        f"保留关键信息、用户偏好和重要结论。只输出摘要本身，不要加前缀。\n\n"
    )
    if existing_summary:
        prompt += f"之前的摘要：\n{existing_summary}\n\n"
    prompt += f"新的对话内容：\n{old_text}"
    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
        )
        summary = resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"摘要生成失败: {e}")
        summary = existing_summary
    conversation_summaries[user_id] = summary
    conversation_history[user_id] = keep_messages
    save_summary_to_db(user_id, summary)
    logger.info(f"用户 {user_id} 对话已摘要，压缩 {len(old_messages)} 条 → 保留 {len(keep_messages)} 条")

# ═══════════════════════════════════════
#  思考链 → Telegram expandable blockquote
# ═══════════════════════════════════════
async def send_thinking_message(chat_id: int, thinking: str, context: ContextTypes.DEFAULT_TYPE):
    if not thinking or not thinking.strip():
        return
    max_len = 3800
    if len(thinking) > max_len:
        thinking = thinking[:max_len] + "…"
    escaped = html.escape(thinking)
    html_text = f"💭 <blockquote expandable>{escaped}</blockquote>"
    try:
        await context.bot.send_message(
            chat_id=chat_id, text=html_text, parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.warning(f"思考链发送失败: {e}")
        fallback = "💭 思考过程：\n" + thinking[:1000]
        try:
            await context.bot.send_message(chat_id=chat_id, text=fallback)
        except Exception:
            pass

# ═══════════════════════════════════════
#  分句发送
# ═══════════════════════════════════════
async def send_split_messages(chat_id: int, text: str, context: ContextTypes.DEFAULT_TYPE):
    parts = [p.strip() for p in text.split("|||") if p.strip()]
    if len(parts) <= 1:
        await context.bot.send_message(chat_id=chat_id, text=text)
        return
    for part in parts:
        await context.bot.send_message(chat_id=chat_id, text=part)
        await asyncio.sleep(0.6)

# ═══════════════════════════════════════
#  核心消息处理（含 tool calling）
# ═══════════════════════════════════════
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    user_text = update.message.text

    reply_msg = update.message.reply_to_message
    if reply_msg and reply_msg.text:
        quoted = reply_msg.text
        if len(quoted) > 200:
            quoted = quoted[:200] + "…"
        user_text = f"[引用消息: \"{quoted}\"]\n{user_text}"

    if user_id not in conversation_history:
        conversation_history[user_id] = []

    conversation_history[user_id].append({"role": "user", "content": user_text})

    now = datetime.now(TZ)
    last_message_time[user_id] = now
    save_last_message_time(user_id, now)

    summarize_conversation(user_id)

    now_str = now.strftime("%Y-%m-%d %H:%M %A")
    system_prompt = f"{SYSTEM_PROMPT_BASE}\n当前时间：{now_str}"

    summary = conversation_summaries.get(user_id)
    if summary:
        system_prompt += f"\n\n之前的对话摘要：\n{summary}"

    rows = db_get_active_reminders()
    user_rows = [(rid, ts, txt, rep) for rid, cid, ts, txt, rep in rows if cid == chat_id]
    if user_rows:
        lines = []
        for rid, ts, txt, rep in user_rows:
            rtype = "每日" if rep else "一次性"
            lines.append(f"  #{rid} {rtype} {ts} {txt}")
        system_prompt += "\n\n当前已设置的提醒：\n" + "\n".join(lines)

    messages = [{"role": "system", "content": system_prompt}] + conversation_history[user_id]

    # ── 1st LLM call: 带 tools，让模型决定是否提取醒后数据 ──
    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            tools=WAKEUP_TOOLS,
            tool_choice="auto",
            max_tokens=1024,
        )
        choice = response.choices[0]
        msg = choice.message
    except Exception as e:
        logger.error(f"LLM 调用失败: {e}")
        await context.bot.send_message(chat_id=chat_id, text=f"出错了：{e}")
        return

    # ── 处理 function calling ──
    if msg.tool_calls:
        # 把 assistant 的 tool_calls 消息写入历史
        assistant_entry = {"role": "assistant", "content": msg.content or ""}
        assistant_entry["tool_calls"] = [tc.model_dump() for tc in msg.tool_calls]
        conversation_history[user_id].append(assistant_entry)

        for tc in msg.tool_calls:
            if tc.function.name == "log_wakeup_record":
                args = json.loads(tc.function.arguments)
                logger.info(f"醒后记录: {json.dumps(args, ensure_ascii=False)}")

                try:
                    log_wakeup_record(args)
                    tool_result = "✅ 醒后数据已成功写入 Notion。"
                except Exception as e:
                    logger.error(f"Notion 写入失败: {e}")
                    tool_result = f"⚠️ 数据已提取但写入 Notion 失败：{e}"

                conversation_history[user_id].append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                })

        # ── 2nd LLM call: 带 tool 结果，生成自然回复 ──
        try:
            response2 = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[{"role": "system", "content": system_prompt}] + conversation_history[user_id],
                max_tokens=512,
            )
            choice2 = response2.choices[0]
            assistant_message = choice2.message.content

            thinking = getattr(choice2.message, "reasoning_content", None)
            if thinking is None:
                extra = getattr(choice2.message, "model_extra", {}) or {}
                thinking = extra.get("reasoning_content")
        except Exception as e:
            logger.error(f"第二次 LLM 调用失败: {e}")
            assistant_message = "已记录。"
            thinking = None
    else:
        # 普通对话，无 tool call
        assistant_message = msg.content
        thinking = getattr(msg, "reasoning_content", None)
        if thinking is None:
            extra = getattr(msg, "model_extra", {}) or {}
            thinking = extra.get("reasoning_content")

    conversation_history[user_id].append({"role": "assistant", "content": assistant_message})

    if thinking:
        await send_thinking_message(chat_id, thinking, context)

    await send_split_messages(chat_id, assistant_message, context)

# ═══════════════════════════════════════
#  start 命令
# ═══════════════════════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "在的。\n\n"
        "提醒命令：\n"
        "/remind HH:MM 内容 — 每日提醒\n"
        "/once HH:MM 内容 — 一次性提醒\n"
        "/reminders — 查看所有提醒\n"
        "/cancel 编号 — 取消提醒\n\n"
        "我还会自动记录你的醒后数据（睡眠状态、用药、咖啡因等）。"
    )

# ═══════════════════════════════════════
#  启动
# ═══════════════════════════════════════
def main():
    init_db()
    logger.info(f"Bot8启动 | 模型: {LLM_MODEL} | 端点: {LLM_BASE_URL} | 数据库: {DB_PATH}")
    app = Application.builder().token(TG_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("remind", remind_cmd))
    app.add_handler(CommandHandler("once", once_cmd))
    app.add_handler(CommandHandler("reminders", list_reminders))
    app.add_handler(CommandHandler("cancel", cancel_reminder))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
