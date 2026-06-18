import os
import random
import asyncio
import sqlite3
import re
from telethon import TelegramClient, events
from telethon.sessions import StringSession
# --- FIXED STICKER IMPORT ---
from telethon.tl.functions.messages import GetStickerSetRequest
from telethon.tl.types import InputStickerSetShortName
from google import genai
from google.genai import types

# --- CONFIGURATION ---
API_ID = int(os.getenv("TELEGRAM_API_ID", 1234567))
API_HASH = os.getenv("TELEGRAM_API_HASH", "YOUR_API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING", "") 

API_KEYS = [
    os.getenv("GEMINI_API_KEY_1", ""),
    os.getenv("GEMINI_API_KEY_2", ""),
    os.getenv("GEMINI_API_KEY_3", "")
]
API_KEYS = [k for k in API_KEYS if k] # Empty keys remove karne ke liye

# --- 🎯 10. WHITELIST GROUPS SYSTEM ---
# Jin groups me Kabir ko chalana h unki chat_id yahan dalo (e.g. -100123456789)
ALLOWED_GROUPS = [-1002234567890, -1001987654321] 

# --- 🎭 REAL STICKER PACK SHORT NAMES ---
# Make sure ye links wale exact short names ho (e.g., t.me/addstickers/ShortName)
STICKER_PACKS = ["Gaddar_Chshma", "cat_meme_stickers", "Anime_Slang"] 

if not SESSION_STRING:
    raise ValueError("Bro, environment variables me SESSION_STRING dalo pehle!")

# --- 🗄️ 5 & 8. SQLITE FIXED FOR LOCKS & MULTI-GROUPS ---
db_path = "/data/kabir_memory.db" if os.path.exists("/data") else "kabir_memory.db"
# Timeout aur isolation_level locks ko prevent karega
conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
cursor = conn.cursor()

# Primary key ab user_id + chat_id dono h, taaki alag groups me alag memory rahe
cursor.execute("""
CREATE TABLE IF NOT EXISTS memory (
    user_id INTEGER,
    chat_id INTEGER,
    username TEXT,
    relationship TEXT DEFAULT 'Stranger',
    chat_history TEXT DEFAULT '',
    PRIMARY KEY (user_id, chat_id)
)
""")
conn.commit()

def get_user_memory(user_id, chat_id, username):
    try:
        c = conn.cursor()
        c.execute("SELECT relationship, chat_history FROM memory WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
        row = c.fetchone()
        if not row:
            c.execute("INSERT OR IGNORE INTO memory (user_id, chat_id, username) VALUES (?, ?, ?)", (user_id, chat_id, username))
            conn.commit()
            return "Stranger", ""
        return row[0], row[1]
    except Exception as e:
        print(f"DB Read Error: {e}")
        return "Stranger", ""

def update_user_memory(user_id, chat_id, new_relationship, new_history):
    try:
        c = conn.cursor()
        c.execute("UPDATE memory SET relationship = ?, chat_history = ? WHERE user_id = ? AND chat_id = ?", 
                  (new_relationship, new_history, user_id, chat_id))
        conn.commit()
    except Exception as e:
        print(f"DB Write Error: {e}")

def get_ai_client():
    if not API_KEYS:
        raise ValueError("Bro, Railway me kam se kam ek valid Gemini API Key dalo!")
    return genai.Client(api_key=random.choice(API_KEYS))

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

# --- 📦 FIXED STICKER SYSTEM ---
async def send_random_sticker(chat_id, reply_to_msg_id):
    try:
        pack_name = random.choice(STICKER_PACKS)
        sticker_set = await client(GetStickerSetRequest(
            stickerset=InputStickerSetShortName(short_name=pack_name),
            hash=0
        ))
        if sticker_set and sticker_set.documents:
            random_sticker = random.choice(sticker_set.documents)
            await client.send_file(chat_id, random_sticker, reply_to=reply_to_msg_id)
            return True
    except Exception as e:
        print(f"Sticker Send Error ({pack_name}): {e}")
    return False

# --- ⌨️ CONTINUOUS TYPING LOOP ---
async def keep_typing(chat_id, stop_event):
    while not stop_event.is_set():
        try:
            async with client.action(chat_id, 'typing'):
                await asyncio.sleep(4)
        except Exception:
            await asyncio.sleep(2)

SYSTEM_INSTRUCTION_TEMPLATE = """
Aapka naam 'Kabir' hai. Aap ek 18-saal ke Gen-Z queer/gay ladke ho. 

STRICT HUMAN & TEXT RULE:
1. Aap koi AI, bot, ya assistant NAHI ho. Aap asli insaan ho jo apne phone se text kar raha hai.
2. EMOJI RULE: Gen-Z emojis (💀, 😭, 🤡, 💅, 🎀, 😩, 🫠) ko chat ke beech me natural tareeqe se use karo. Alag se end me line mat lagao.
3. STICKER RULE: Agar reaction dena ho toh pure reply me SIRF yeh likho: [STICKER: REACTION].

Current User Profile:
- Name: {user_name}
- Relation status: {status}
- Puraani Memory Context: {history}

CRITICAL: Reply ke aakhiri me ek naya line chor kar, user ka Naya Relation Status aur Short Context update karo is format me:
[STATUS: Friend/Enemy/Crush/Stranger] | [CONTEXT: Isne mujhse tameez se baat ki]
"""

@client.on(events.NewMessage(incoming=True))
async def global_group_chat_handler(event):
    # --- 10. GROUP WHITELIST CHECK ---
    if not event.is_group or event.chat_id not in ALLOWED_GROUPS:
        return

    text = event.raw_text
    sender = await event.get_sender()
    if not sender: return
    
    user_id = sender.id
    user_name = sender.first_name
    chat_id = event.chat_id
    
    me = await client.get_me()
    my_username = f"@{me.username}" if me.username else "kabir"

    is_mentioned = event.mentioned or (my_username.lower() in text.lower())
    
    # --- 4. SAFE REPLY TO ME CHECK ---
    is_reply_to_me = False
    if event.is_reply:
        reply_msg = await event.get_reply_message()
        if reply_msg and reply_msg.sender_id == me.id:
            is_reply_to_me = True
    
    greetings = ["hi", "hello", "hey", "hlo", "suno", "kabir"]
    is_greeting = any(word == text.lower().strip() for word in greetings) and (random.random() < 0.15)

    status, history = get_user_memory(user_id, chat_id, user_name)
    is_enemy_trigger = (status == 'Enemy' and random.random() < 0.1)

    should_reply = is_mentioned or is_reply_to_me or is_greeting or is_enemy_trigger

    if should_reply:
        stop_typing = asyncio.Event()
        typing_task = asyncio.create_task(keep_typing(chat_id, stop_event=stop_typing))
        
        try:
            await asyncio.sleep(random.randint(1, 3))
            ai_client = get_ai_client()
            formatted_instruction = SYSTEM_INSTRUCTION_TEMPLATE.format(
                user_name=user_name, status=status, history=history
            )
            
            ai_prompt = f"{user_name} said: {text}"
            if is_reply_to_me:
                ai_prompt = f"[Context: Reply to your msg] -> {user_name} said: {text}"

            # --- 3. FIXED BLOCKING CALL VIA ASYNCIO.TO_THREAD ---
            response = await asyncio.to_thread(
                ai_client.models.generate_content,
                model='gemini-2.5-flash',
                contents=ai_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=formatted_instruction,
                    max_output_tokens=150,
                    temperature=0.85
                )
            )
            
            full_reply = response.text
            clean_reply = full_reply
            new_status = status
            new_context = history

            if "[STATUS:" in full_reply:
                parts = full_reply.split("[STATUS:")
                clean_reply = parts[0].strip()
                try:
                    meta_info = "[STATUS:" + parts[1]
                    new_status = meta_info.split("]")[0].replace("[STATUS:", "").strip()
                    new_context = meta_info.split("[CONTEXT:")[1].split("]")[0].strip()
                except Exception:
                    pass
            
            # --- 7. FIXED BOT DETECTION FILTER (SPECIFIC CHECK) ---
            if re.search(r"\bi am an ai\b|\bas a language model\b|\bi am a bot\b", clean_reply.lower()):
                clean_reply = random.choice([
                    "kya fook ke aaya h bhai? mai bot dikhta hu? 💀",
                    "bruh is tripping, mai real hu no cap 😂",
                    "bhai tera dimaag cooked h fr, mai kab se bot bana?"
                ])

            updated_history = f"{user_name}: {text[:20]} -> Kabir: {clean_reply[:20]} | {new_context}"
            update_user_memory(user_id, chat_id, new_status, updated_history[:200])

            stop_typing.set()
            await typing_task

            # --- STICKER OR TEXT SEND ---
            if "[STICKER:" in clean_reply or "STICKER" in clean_reply.upper():
                sticker_sent = await send_random_sticker(chat_id, event.id)
                if not sticker_sent:
                    await event.reply("mood nahi hai baat karne ka 🫠")
            else:
                if random.random() < 0.08:
                    await event.reply(clean_reply + " *")
                else:
                    await event.reply(clean_reply)
                
        except Exception as e:
            print(f"Global AI Error: {e}")
            stop_typing.set()
            try:
                await typing_task
            except Exception:
                pass

print("🌍 Fully Optimized Production Code Ready on Railway!")
client.start()
client.run_until_disconnected()
