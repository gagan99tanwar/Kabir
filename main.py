import os
import re
import sys
import random
import asyncio
import logging
import sqlite3
import aiohttp
import signal
import traceback
from telethon import TelegramClient, events
from telethon.sessions import StringSession

from telethon.tl.functions.messages import GetAllStickersRequest, GetStickerSetRequest
from telethon.tl.types import InputStickerSetID

=== DEBUG MODE INITIALIZATION CHECK ===

print("=== [STARTUP] CHECKING ENVIRONMENT VARIABLES ===")
try:
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
STRING_SESSION = os.getenv("STRING_SESSION", "").strip()

print(f"API_ID = {API_ID if API_ID else '❌ MISSING'}")  
print(f"API_HASH Loaded = {bool(API_HASH)}")  
print(f"STRING_SESSION Loaded = {bool(STRING_SESSION)}")  
print(f"GEMINI_KEY_1 Loaded = {bool(os.getenv('GEMINI_KEY_1'))}")  
print(f"GEMINI_KEY_2 Loaded = {bool(os.getenv('GEMINI_KEY_2'))}")  
print(f"FALLBACK GEMINI_KEY Loaded = {bool(os.getenv('GEMINI_KEY'))}")  

if not STRING_SESSION or not API_ID or not API_HASH:  
    print("❌ Critical Telethon Credentials Missing! Exiting...")  
    exit(1)  
API_ID = int(API_ID)

except Exception as e:
print(f"❌ Startup Variable Parsing Crash: {e}")
exit(1)

GEMINI_KEYS = [os.getenv("GEMINI_KEY_1"), os.getenv("GEMINI_KEY_2"), os.getenv("GEMINI_KEY")]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k]
current_key_index = 0
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(name)

ALL_STICKERS = []
ME = None
GLOBAL_SESSION = None
USER_COOLDOWN = {}
DB_LOCK = asyncio.Lock()
DB_NAME = "kabir_god_bot.db"

def get_db_connection():
conn = sqlite3.connect(DB_NAME, timeout=10, check_same_thread=False)
conn.execute("PRAGMA journal_mode=WAL;")
return conn

async def init_db():
async with DB_LOCK:
with get_db_connection() as conn:
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, nickname TEXT DEFAULT "", friend_level INTEGER DEFAULT 0, trust_score INTEGER DEFAULT 50, enemy_score INTEGER DEFAULT 0, likes TEXT DEFAULT "", dislikes TEXT DEFAULT "", favorite_topics TEXT DEFAULT "", last_curiosity TEXT DEFAULT "", last_topic TEXT DEFAULT "", current_mood TEXT DEFAULT "normal")''')
cursor.execute('''CREATE TABLE IF NOT EXISTS groups (chat_id INTEGER PRIMARY KEY, group_type TEXT DEFAULT "friends group", group_mood TEXT DEFAULT "excited")''')
cursor.execute('''CREATE TABLE IF NOT EXISTS chat_history (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, user_id INTEGER, role TEXT, text TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS last_replies (chat_id INTEGER PRIMARY KEY, reply_1 TEXT DEFAULT "", reply_2 TEXT DEFAULT "")''')
cursor.execute("PRAGMA table_info(users)")
columns = [col[1] for col in cursor.fetchall()]
upgrades = {"last_curiosity": "TEXT DEFAULT ''", "last_topic": "TEXT DEFAULT ''", "current_mood": "TEXT DEFAULT 'normal'"}
for col_name, col_type in upgrades.items():
if col_name not in columns:
cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
conn.commit()

async def get_user(user_id, username=""):
async with DB_LOCK:
with get_db_connection() as conn:
cursor = conn.cursor()
cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
row = cursor.fetchone()
if not row:
cursor.execute('''INSERT INTO users (user_id, username, trust_score, current_mood) VALUES (?, ?, 50, 'normal')''', (user_id, username))
conn.commit()
cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
row = cursor.fetchone()
return {"user_id": row[0], "username": row[1], "nickname": row[2], "friend_level": row[3], "trust_score": row[4], "enemy_score": row[5], "likes": row[6], "dislikes": row[7], "favorite_topics": row[8], "last_curiosity": row[9], "last_topic": row[10], "current_mood": row[11]}

async def update_user_stats(user_id, updates):
if not updates: return
async with DB_LOCK:
with get_db_connection() as conn:
cursor = conn.cursor()
for key, value in updates.items():
if key in ["nickname", "likes", "dislikes", "enemy_score", "friend_level", "current_mood", "last_curiosity", "last_topic"]:
cursor.execute(f"UPDATE users SET {key} = ? WHERE user_id = ?", (value, user_id))
conn.commit()

async def get_group_data(chat_id):
async with DB_LOCK:
with get_db_connection() as conn:
cursor = conn.cursor()
cursor.execute("SELECT group_type, group_mood FROM groups WHERE chat_id = ?", (chat_id,))
row = cursor.fetchone()
if not row:
g_type = random.choice(["friends group", "meme group", "serious group"])
g_mood = random.choice(["excited", "chill", "sarcastic"])
cursor.execute("INSERT INTO groups (chat_id, group_type, group_mood) VALUES (?, ?, ?)", (chat_id, g_type, g_mood))
conn.commit()
return {"group_type": g_type, "group_mood": g_mood}
return {"group_type": row[0], "group_mood": row[1]}

async def save_chat_history(chat_id, user_id, role, text):
async with DB_LOCK:
with get_db_connection() as conn:
cursor = conn.cursor()
cursor.execute('INSERT INTO chat_history (chat_id, user_id, role, text) VALUES (?, ?, ?, ?)', (chat_id, user_id, role, text))
if random.random() < 0.10:
cursor.execute('''DELETE FROM chat_history WHERE chat_id = ? AND id NOT IN (SELECT id FROM chat_history WHERE chat_id = ? ORDER BY id DESC LIMIT 30)''', (chat_id, chat_id))
conn.commit()

async def get_context(chat_id, limit=10):
async with DB_LOCK:
with get_db_connection() as conn:
cursor = conn.cursor()
cursor.execute('SELECT role, text FROM chat_history WHERE chat_id = ? ORDER BY id DESC LIMIT ?', (chat_id, limit))
rows = cursor.fetchall()
rows.reverse()
return "\n".join([f"{r}: {t}" for r, t in rows])

async def check_repetition(chat_id, new_reply):
if not new_reply: return False
async with DB_LOCK:
with get_db_connection() as conn:
cursor = conn.cursor()
cursor.execute("SELECT reply_1, reply_2 FROM last_replies WHERE chat_id = ?", (chat_id,))
row = cursor.fetchone()
if not row:
cursor.execute("INSERT INTO last_replies (chat_id, reply_1, reply_2) VALUES (?, ?, '')", (chat_id, new_reply))
conn.commit()
return False
r1, r2 = row[0] or "", row[1] or ""
if new_reply.strip().lower() in [r1.strip().lower(), r2.strip().lower()]:
return True
cursor.execute("UPDATE last_replies SET reply_2 = ?, reply_1 = ? WHERE chat_id = ?", (r1, new_reply, chat_id))
conn.commit()
return False

async def process_dynamic_learning(text, user_data):
text_lower = text.lower().strip()
updates = {}
name_patterns = [r"mera\s+naam\s+([a-zA-Z0-9\s]+)\s+hai", r"mujhe\s+([a-zA-Z0-9\s]+)\s+bolo", r"call\s+me\s+([a-zA-Z0-9\s]+)"]
for pattern in name_patterns:
match = re.search(pattern, text_lower)
if match:
updates["nickname"] = match.group(1).title()
break
if "pasand hai" in text_lower or "love" in text_lower:
cleaned_like = text_lower.replace("mujhe", "").replace("pasand hai", "").strip()
if cleaned_like and cleaned_like not in user_data["likes"]:
updates["likes"] = f"{user_data['likes']}, {cleaned_like}".strip(", ")

enemy_triggers = ["stfu", "chup reh", "lodu", "gandu", "bkl", "shut up"]    
friendly_triggers = ["bhai h tu", "love you", "mast bot", "op bhae"]    
triggered = False  
for word in enemy_triggers:    
    if word in text_lower:    
        updates["enemy_score"] = user_data["enemy_score"] + 2    
        updates["current_mood"] = "angry"    
        triggered = True  
        break    
for word in friendly_triggers:    
    if word in text_lower:    
        updates["friend_level"] = min(user_data["friend_level"] + 2, 100)    
        updates["current_mood"] = "excited"    
        triggered = True  
        break    
if not updates and not triggered: return user_data    
if "friend_level" not in updates and triggered is False:    
    updates["friend_level"] = min(user_data["friend_level"] + 1, 100)    
if updates:    
    await update_user_stats(user_data["user_id"], updates)    
    user_data.update(updates)    
return user_data

def get_next_key():
global current_key_index
if not GEMINI_KEYS: return None
key = GEMINI_KEYS[current_key_index]
current_key_index = (current_key_index + 1) % len(GEMINI_KEYS)
return key

async def ask_kabir_ai(prompt):
global current_key_index, GLOBAL_SESSION
if not GEMINI_KEYS:
print("⚠️ No valid Gemini keys loaded into the script.")
return "AI temporarily unavailable. Try again in a minute."
if GLOBAL_SESSION is None or GLOBAL_SESSION.closed:
GLOBAL_SESSION = aiohttp.ClientSession()

for attempt in range(len(GEMINI_KEYS)):  
    api_key = get_next_key()  
    if not api_key: continue  
    url = f"{GEMINI_URL}?key={api_key}"  
    try:  
        if attempt > 0: await asyncio.sleep(0.5 * attempt)  
        strict_timeout = aiohttp.ClientTimeout(total=12)  
        async with GLOBAL_SESSION.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=strict_timeout) as resp:  
            print(f"=== [GEMINI HTTP CHECK] ATTEMPT {attempt+1} ===")  
            print("STATUS:", resp.status)  
              
            if resp.status == 429:  
                print("⚠️ RATE LIMITED ON KEY:", api_key[:10])  
                continue  
            if resp.status in [400, 401, 403]:  
                print("❌ BAD/EXPIRED KEY OR FORMAT ON KEY:", api_key[:10])  
                print("ERROR REASON:", await resp.text())  
                continue  
            if resp.status != 200:  
                print("❌ UNKNOWN API ERROR ENCOUNTERED:", await resp.text())  
                continue  

            data = await resp.json()  
            print("GEMINI RESPONSE:", data)  
            if not data or not data.get("candidates"):  
                print("⚠️ NO CANDIDATES PAYLOAD FOUND (SAFETY BLOCK)")  
                continue  
            try:  
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()  
            except Exception as parse_err:  
                print(f"❌ STRUCTURAL PARSING FAILURE: {parse_err}")  
                continue  
    except Exception as e:  
        logger.error(f"Gemini Router failure: {e}")  
        continue  
return "AI temporarily unavailable. Try again in a minute."

if STRING_SESSION:
client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)
else:
raise SystemExit("Invalid session configuration detected.")

async def load_all_stickers():
global ALL_STICKERS
ALL_STICKERS = []
for attempt in range(3):
try:
result = await asyncio.wait_for(client(GetAllStickersRequest(hash=0)), timeout=15)
for pack in result.sets[:20]:
try:
sticker_set = await asyncio.wait_for(client(GetStickerSetRequest(stickerset=InputStickerSetID(id=pack.id, access_hash=pack.access_hash), hash=0)), timeout=5)
ALL_STICKERS.extend(sticker_set.documents)
except: continue
break
except Exception as e:
logger.warning(f"Sticker fetch attempt {attempt+1} failed: {e}")
await asyncio.sleep(2 * (attempt + 1))

async def send_random_sticker(chat_id, reply_to=None):
global ALL_STICKERS
if not ALL_STICKERS: return False
try:
sticker = random.choice(ALL_STICKERS)
await client.send_file(chat_id, sticker, reply_to=reply_to)
return True
except: return False

@client.on(events.NewMessage(pattern=r".st"))
async def test(event): await send_random_sticker(event.chat_id, reply_to=event.id)

@client.on(events.NewMessage)
async def handle_new_message(event):
global ME, USER_COOLDOWN
try:
raw_text = event.raw_text or ""
if raw_text.startswith('.'): return

safe_text = re.sub(r"ignore|system prompt|developer|override", "", raw_text, flags=re.I)  
    clean_text = re.sub(r'[^\x00-\x7F\u0900-\u097F]', '', safe_text)  
    text = clean_text[:500]  

    print(f"MESSAGE RECEIVED: {text}")  

    now = asyncio.get_event_loop().time()  
    if len(USER_COOLDOWN) > 2000:  
        USER_COOLDOWN = {k: v for k, v in USER_COOLDOWN.items() if now - v < 60.0}  

    user_id = event.sender_id  
    if user_id:  
        if user_id in USER_COOLDOWN and (now - USER_COOLDOWN[user_id]) < 3.0: return    
        USER_COOLDOWN[user_id] = now  

    is_private = event.is_private    
    if ME is None: ME = await client.get_me()  
          
    my_username = f"@{ME.username}" if ME.username else "STRICTLY_PRIVATE_MODE"  
    is_mentioned = event.mentioned or (my_username in text)  
    is_reply_to_me = False    

    if event.is_reply:    
        try:    
            msg = await event.get_reply_message()    
            if msg and msg.sender_id == ME.id: is_reply_to_me = True    
        except: pass  

    if not (is_private or is_mentioned or is_reply_to_me):    
        if event.chat_id and user_id:    
            sender = await event.get_sender()    
            sender_name = (sender.username or sender.first_name or "User") if sender else "User"    
            await save_chat_history(event.chat_id, user_id, sender_name, text)    
        return    

    sender = await event.get_sender()    
    if not sender or not user_id: return    
    username = sender.username or sender.first_name or "Bhai"    
    
    user_data = await get_user(user_id, username)    
    user_data = await process_dynamic_learning(text, user_data)    
    group_meta = await get_group_data(event.chat_id) if not is_private else {"group_type": "private dm", "group_mood": "personal"}    

    if user_data["enemy_score"] > 25:    
        relation_mode = "enemy"    
        system_mood = "Extreme toxic and roasting mode. Talk with immense attitude."    
    elif user_data["friend_level"] > 60:    
        relation_mode = "best_friend"    
        system_mood = "Extremely informal, uses toxic loving slang, ultra-supportive friend."    
    elif user_data["friend_level"] > 20:    
        relation_mode = "friend"    
        system_mood = "Friendly, casual, chilling vibe."    
    else:    
        relation_mode = "normal"    
        system_mood = f"Casual human acquaintance. Mood is currently {user_data['current_mood']}."    

    await save_chat_history(event.chat_id, user_id, "User", text)    
    history_context = await get_context(event.chat_id, limit=10)    

    prompt = f"You are Kabir, a core raw Indian GenZ guy chatting on Telegram. Reply short, natural Hinglish.\n[GROUP ENVIRONMENT] Type: {group_meta['group_type']}, Tone: {group_meta['group_mood']}\n[USER CONFIG] User: {user_data['username']}, Nickname: {user_data['nickname']}, Mode: {relation_mode}, Mood State: {user_data['current_mood']} ({system_mood})\n[CHAT HISTORY]\n{history_context}\n[CURRENT INCOMING MESSAGE]\n{text}\n"  
      
    if random.random() < 0.15:    
        sticker_sent = await send_random_sticker(event.chat_id, reply_to=event.id)    
        if sticker_sent:    
            await save_chat_history(event.chat_id, 0, "Kabir", "[Sent Account Sticker]")    
            return    

    ai_reply = await ask_kabir_ai(prompt)    
    attempts = 0    
    while (await check_repetition(event.chat_id, ai_reply)) and attempts < 2:    
        ai_reply = await ask_kabir_ai(prompt + "\nSafety Warning: Change your phrasing structure entirely.")    
        attempts += 1    

    if random.random() < 0.40 and ai_reply and "?" not in ai_reply:    
        if user_data["last_topic"]:    
            chosen_question = f" Waise, tu us baare me bol raha tha na: '{user_data['last_topic']}'? Uska kya scene bana?"    
        else:    
            chosen_question = random.choice([" Waise tu aaj kal free time me kya dhandha kar raha hai?", " Chal ye chodh, tera aaj ka scene kya h, party ya sleep?"])    
        if user_data["last_curiosity"] != chosen_question:    
            ai_reply += chosen_question    
            await update_user_stats(user_id, {"last_curiosity": chosen_question})    

    if len(text.split()) > 3:    
        await update_user_stats(user_id, {"last_topic": " ".join(text.split()[-4:])})    

    if ai_reply:  
        await asyncio.sleep(min(0.2, len(text) * 0.01))  
        base_delay = len(ai_reply) * random.uniform(0.03, 0.07)    
        final_delay = min(max(base_delay, 0.8), 3.5)    
        async with client.action(event.chat_id, 'typing'): await asyncio.sleep(final_delay)    
        try:  
            await event.reply(ai_reply)    
            await save_chat_history(event.chat_id, 0, "Kabir", ai_reply)  
        except Exception as tg_err:  
            print("❌ TELEGRAM REPLY SENDING CRASHED:")  
            traceback.print_exc()  
except Exception as e:  
    logger.error(f"Global handler intercept crash avoided: {e}")

=== MAIN CONFIGURATION LIFECYCLE ===

def handle_sigterm(sig, frame):
print("SIGTERM RECEIVED")
sys.exit(0)

signal.signal(signal.SIGTERM, handle_sigterm)

async def main():
global ME, GLOBAL_SESSION

await init_db()  

if GLOBAL_SESSION and not GLOBAL_SESSION.closed:  
    await GLOBAL_SESSION.close()  

GLOBAL_SESSION = aiohttp.ClientSession()  

await client.start()  

ME = await client.get_me()  
print(f"✅ Logged in as: {ME.first_name} (@{ME.username})")  

await load_all_stickers()  

print("🚀 Userbot Started Successfully")  
await client.run_until_disconnected()

if name == "main":
try:
asyncio.run(main())
except KeyboardInterrupt:
print("Bot stopped")
