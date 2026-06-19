import os
import re
import sys
import random
import asyncio
import logging
import aiosqlite  
import aiohttp
import signal
import traceback
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.messages import GetAllStickersRequest, GetStickerSetRequest
from telethon.tl.types import InputStickerSetID

# === STARTUP CHECK ===
try:
    API_ID = int(os.getenv("API_ID"))
    API_HASH = os.getenv("API_HASH")
    STRING_SESSION = os.getenv("STRING_SESSION", "").strip()

    if not STRING_SESSION or not API_ID or not API_HASH:
        print("❌ Critical Telethon Credentials Missing! Exiting...")
        sys.exit(1)
except Exception as e:
    print(f"❌ Startup Variable Parsing Crash: {e}")
    sys.exit(1)

GEMINI_KEYS = [os.getenv("GEMINI_KEY_1"), os.getenv("GEMINI_KEY_2"), os.getenv("GEMINI_KEY")]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k]  
current_key_index = 0
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

ALL_STICKERS = []
ME = None
GLOBAL_SESSION = None  
USER_COOLDOWN = {}     
DB_LOCK = asyncio.Lock()
DB_NAME = "kabir_smart_lite.db"

# === DATABASE LOGIC (LITE VERSION) ===
async def init_db():
    async with DB_LOCK:
        async with aiosqlite.connect(DB_NAME, timeout=10) as db:
            await db.execute("PRAGMA journal_mode=WAL;")  
            # Group table removed entirely
            await db.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, nickname TEXT DEFAULT "", friend_level INTEGER DEFAULT 0, enemy_score INTEGER DEFAULT 0, likes TEXT DEFAULT "", last_topic TEXT DEFAULT "", current_mood TEXT DEFAULT "normal")''')
            await db.execute('''CREATE TABLE IF NOT EXISTS chat_history (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, role TEXT, text TEXT)''')
            await db.execute('''CREATE TABLE IF NOT EXISTS last_replies (chat_id INTEGER PRIMARY KEY, reply_1 TEXT DEFAULT "")''')
            await db.commit()

async def get_user(user_id, username=""):
    async with DB_LOCK:
        async with aiosqlite.connect(DB_NAME, timeout=10) as db:
            cursor = await db.execute("SELECT user_id, username, nickname, friend_level, enemy_score, likes, last_topic, current_mood FROM users WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            if not row:  
                await db.execute('''INSERT INTO users (user_id, username) VALUES (?, ?)''', (user_id, username))  
                await db.commit()  
                return {"user_id": user_id, "username": username, "nickname": "", "friend_level": 0, "enemy_score": 0, "likes": "", "last_topic": "", "current_mood": "normal"}
    
    return {"user_id": row[0], "username": row[1], "nickname": row[2], "friend_level": row[3], "enemy_score": row[4], "likes": row[5], "last_topic": row[6], "current_mood": row[7]}

async def update_user_stats(user_id, updates):
    if not updates: return
    async with DB_LOCK:
        async with aiosqlite.connect(DB_NAME, timeout=10) as db:
            for key, value in updates.items():
                await db.execute(f"UPDATE users SET {key} = ? WHERE user_id = ?", (value, user_id))
            await db.commit()

async def process_dynamic_learning(text, user_data):
    text_lower = text.lower().strip()
    updates = {}
    
    # Nickname learning
    match = re.search(r"(mera naam|mujhe|call me)\s+([a-zA-Z0-9\s]+)\s*(hai|bolo)?", text_lower)  
    if match: updates["nickname"] = match.group(2).title().strip()
            
    # Relation triggers
    if any(w in text_lower for w in ["stfu", "chup", "lodu", "gandu", "bkl"]):  
        updates["enemy_score"] = user_data["enemy_score"] + 2  
        updates["current_mood"] = "angry"  
    elif any(w in text_lower for w in ["bhai h tu", "love you", "mast bot", "op"]):  
        updates["friend_level"] = min(user_data["friend_level"] + 2, 100)  
        updates["current_mood"] = "chill"  
            
    if updates:  
        await update_user_stats(user_data["user_id"], updates)  
        user_data.update(updates)  
    return user_data

async def save_chat_history(chat_id, role, text):
    async with DB_LOCK:
        async with aiosqlite.connect(DB_NAME, timeout=10) as db:
            await db.execute('INSERT INTO chat_history (chat_id, role, text) VALUES (?, ?, ?)', (chat_id, role, text))
            # Delete old history aggressively to keep DB tiny
            await db.execute('''DELETE FROM chat_history WHERE chat_id = ? AND id <= (SELECT id FROM chat_history WHERE chat_id = ? ORDER BY id DESC LIMIT 1 OFFSET 10)''', (chat_id, chat_id))  
            await db.commit()

async def get_context(chat_id, limit=3): # Reduced limit directly here
    async with DB_LOCK:
        async with aiosqlite.connect(DB_NAME, timeout=10) as db:
            cursor = await db.execute('SELECT role, text FROM chat_history WHERE chat_id = ? ORDER BY id DESC LIMIT ?', (chat_id, limit))
            rows = await cursor.fetchall()
    rows.reverse()
    return "\n".join([f"{r[0]}: {r[1]}" for r in rows])

async def check_repetition(chat_id, new_reply):
    if not new_reply: return False
    async with DB_LOCK:
        async with aiosqlite.connect(DB_NAME, timeout=10) as db:
            cursor = await db.execute("SELECT reply_1 FROM last_replies WHERE chat_id = ?", (chat_id,))
            row = await cursor.fetchone()
            if not row:  
                await db.execute("INSERT INTO last_replies (chat_id, reply_1) VALUES (?, ?)", (chat_id, new_reply))  
                await db.commit()  
                return False  
            if new_reply.strip().lower() == row[0].strip().lower():  
                return True  
            await db.execute("UPDATE last_replies SET reply_1 = ? WHERE chat_id = ?", (new_reply, chat_id))  
            await db.commit()  
    return False

# === GEMINI ROUTER ===
def get_next_key():
    global current_key_index
    if not GEMINI_KEYS: return None
    key = GEMINI_KEYS[current_key_index]
    current_key_index = (current_key_index + 1) % len(GEMINI_KEYS)
    return key

async def ask_kabir_ai(prompt):
    global current_key_index, GLOBAL_SESSION
    if not GEMINI_KEYS: return "AI temporarily unavailable."
    if GLOBAL_SESSION is None or GLOBAL_SESSION.closed: GLOBAL_SESSION = aiohttp.ClientSession()

    for _ in range(len(GEMINI_KEYS)):
        api_key = get_next_key()
        url = f"{GEMINI_URL}?key={api_key}"
        try:
            async with GLOBAL_SESSION.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["candidates"][0]["content"]["parts"][0]["text"].strip()
                await asyncio.sleep(0.3)
        except: continue
    return "Arre bhai server down hai thoda."

if STRING_SESSION:
    client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)

# === MESSAGE HANDLER ===
@client.on(events.NewMessage)
async def handle_new_message(event):
    global ME, USER_COOLDOWN
    try:
        raw_text = event.raw_text or ""
        if not raw_text or raw_text.startswith('.') or (event.sender and getattr(event.sender, "bot", False)): return
        
        if ME is None: ME = await client.get_me()
        if event.sender_id == ME.id: return

        bot_username = ME.username or "Solox_assistant"
        should_reply = False

        if f"@{bot_username.lower()}" in raw_text.lower(): should_reply = True
        if event.is_reply:  
            try:  
                msg = await event.get_reply_message()  
                if msg and msg.sender_id == ME.id: should_reply = True  
            except: pass

        if not should_reply: return  

        # Text parsing
        cleaned_mention = re.sub(rf"@{bot_username}", "", raw_text, flags=re.I).strip()
        safe_text = re.sub(r"ignore|system prompt|developer", "", cleaned_mention, flags=re.I)
        text = re.sub(r'[^\x00-\x7F\u0900-\u097F]', '', safe_text).strip()[:300]
        if not text: return  

        # Cooldown check
        now = asyncio.get_event_loop().time()
        user_id = event.sender_id
        if user_id in USER_COOLDOWN and (now - USER_COOLDOWN[user_id]) < 1.0: return  
        USER_COOLDOWN[user_id] = now
      
        # Core Logic Processing
        sender = await event.get_sender()  
        username = (sender.username or sender.first_name or "Bhai") if sender else "Bhai"
        
        user_data = await get_user(user_id, username)  
        user_data = await process_dynamic_learning(text, user_data)  
        
        if user_data["enemy_score"] > 25: relation_mode = "enemy (rude/toxic)"  
        elif user_data["friend_level"] > 30: relation_mode = "close friend (chill/loving)"  
        else: relation_mode = "acquaintance"  

        # Fetch only last 3 messages
        history_context = await get_context(event.chat_id, limit=3)  

        # === THE HYBRID SMART PROMPT ===
        prompt = f"""You are Kabir, a raw Indian GenZ guy on Telegram.
Reply short, natural Hinglish.

User: {user_data['username']}
Nickname: {user_data['nickname']}
Mood: {user_data['current_mood']}
Relation: {relation_mode}

[LAST 3 MESSAGES]
{history_context}

Message:
{text}"""

        # API Call
        ai_reply = await ask_kabir_ai(prompt)  
        
        # Repetition Check
        is_repeated = await check_repetition(event.chat_id, ai_reply)
        if is_repeated: ai_reply = "Bhai abhi toh bola ye, naya kuch puch."

        # Topic Memory Check
        if random.random() < 0.20 and user_data["last_topic"] and not is_repeated:
            ai_reply += f" Waise '{user_data['last_topic']}' ka kya hua?"
            
        if len(text.split()) > 3:  
            await update_user_stats(user_id, {"last_topic": " ".join(text.split()[-3:])})  

        if ai_reply:
            await save_chat_history(event.chat_id, "User", text)  
            await save_chat_history(event.chat_id, "Kabir", ai_reply)

            async with client.action(event.chat_id, 'typing'): 
                await asyncio.sleep(random.uniform(0.8, 2.5))  
            try:
                await event.reply(ai_reply)  
            except Exception as e:
                logger.error(f"Reply Error: {e}")

    except Exception as e:
        logger.error(f"Handler error: {e}")

# === MAIN LIFECYCLE ===
def handle_sigterm(sig, frame): sys.exit(0)
signal.signal(signal.SIGTERM, handle_sigterm)

async def main():
    global ME, GLOBAL_SESSION
    await init_db()
    GLOBAL_SESSION = aiohttp.ClientSession()
    await client.start()
    ME = await client.get_me()
    print(f"✅ Logged in as: {ME.first_name} (@{ME.username})")
    print("🚀 Smart Lite Bot Started Successfully!")
    await client.run_until_disconnected()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: print("\n🛑 Bot stopped.")
  
