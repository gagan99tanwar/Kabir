import os
import random
import asyncio
import sqlite3
import re
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.messages import GetStickerSet
from telethon.tl.types import InputStickerSetShortName
from google import genai
from google.genai import types

# --- CONFIGURATION ---
API_ID = int(os.getenv("TELEGRAM_API_ID", 1234567))
API_HASH = os.getenv("TELEGRAM_API_HASH", "YOUR_API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING", "") 

API_KEYS = [
    os.getenv("GEMINI_API_KEY_1", "YOUR_KEY_1"),
    os.getenv("GEMINI_API_KEY_2", "YOUR_KEY_2")
]

# --- 🎭 APNE STICKER PACKS YAHAN DALO ---
STICKER_PACKS = ["HotCherry", "MilkAndMocha", "LineFriends"] 
# ----------------------------------------

if not SESSION_STRING:
    raise ValueError("Bro, environment variables me SESSION_STRING dalo pehle!")

# --- DATABASE SETUP ---
db_path = "/data/kabir_memory.db" if os.path.exists("/data") else "kabir_memory.db"
conn = sqlite3.connect(db_path, check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS memory (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    relationship TEXT DEFAULT 'Stranger',
    chat_history TEXT DEFAULT ''
)
""")
conn.commit()

def get_user_memory(user_id, username):
    cursor.execute("SELECT relationship, chat_history FROM memory WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if not row:
        cursor.execute("INSERT INTO memory (user_id, username) VALUES (?, ?)", (user_id, username))
        conn.commit()
        return "Stranger", ""
    return row[0], row[1]

def update_user_memory(user_id, new_relationship, new_history):
    cursor.execute("UPDATE memory SET relationship = ?, chat_history = ? WHERE user_id = ?", 
                   (new_relationship, new_history, user_id))
    conn.commit()

def get_ai_client():
    valid_keys = [k for k in API_KEYS if k and "YOUR_KEY" not in k]
    if not valid_keys:
        raise ValueError("Bro, kam se kam ek valid Gemini API Key toh dalo!")
    return genai.Client(api_key=random.choice(valid_keys))

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

# --- 📦 HELPER: STICKER PACK SE STICKER NIKALNA ---
async def send_random_sticker(chat_id, reply_to_msg_id):
    try:
        pack_name = random.choice(STICKER_PACKS)
        sticker_set = await client(GetStickerSet(
            stickerset=InputStickerSetShortName(short_name=pack_name),
            hash=0
        ))
        if sticker_set and sticker_set.documents:
            random_sticker = random.choice(sticker_set.documents)
            await client.send_file(chat_id, random_sticker, reply_to=reply_to_msg_id)
            return True
    except Exception as e:
        print(f"Sticker Send Error: {e}")
    return False

# --- ⌨️ HELPER: CONTINUOUS TYPING LOOP ---
# Yeh function jab tak chalega, group me "Kabir is typing..." dikhta rahega
async def keep_typing(chat_id, stop_event):
    while not stop_event.is_set():
        try:
            async with client.action(chat_id, 'typing'):
                # Har 4 second me status refresh hoga taaki ghaib na ho
                await asyncio.sleep(4)
        except Exception as e:
            print(f"Typing Action Error: {e}")
            await asyncio.sleep(2)

SYSTEM_INSTRUCTION_TEMPLATE = """
Aapka naam 'Kabir' hai. Aap ek 18-saal ke Gen-Z queer/gay ladke ho. 

STRICT HUMAN & TEXT RULE:
1. Aap koi AI, bot, ya assistant NAHI ho. Aap asli insaan ho.
2. EMOJI RULE: Gen-Z emojis (💀, 😭, 🤡, 💅, 🎀, 😩, 🫠) ko chat ke beech me natural tareeqe se use karo (e.g., "bhai mai toh cooked hu 💀 no cap" ya "aww so cute 😭🎀"). Message ke aakhiri me alag se emojis ki line mat lagao. Text ke sath mix hona chahiye.
3. STICKER RULE: Agar samne wale ki baat par sirf ek sticker bhejkar reaction dena ho (jaise gussa, hasi, ya rona), toh pure reply me SIRF yeh likho: [STICKER: REACTION]. Kuch aur mat likhna.

Current User Profile:
- Name: {user_name}
- Relation status: {status}
- Puraani Memory Context: {history}

Character Persona:
- Emotions: Sachi nafrat aur pyaar dikhao. Enemies ko roast karo, crushes/besties ko pamper karo.
- Slang: Hinglish mix karo (fr fr, rizz, opps, delulu, L/W). short messages likho.

CRITICAL: Reply ke aakhiri me ek nayi line par, user ka Naya Relation Status aur Short Context update karo is format me:
[STATUS: Friend/Enemy/Crush/Stranger] | [CONTEXT: Isne mujhse tameez se baat ki]
"""

@client.on(events.NewMessage(incoming=True))
async def global_group_chat_handler(event):
    if not event.is_group:
        return

    text = event.raw_text
    sender = await event.get_sender()
    if not sender: return
    
    user_id = sender.id
    user_name = sender.first_name
    
    me = await client.get_me()
    my_username = f"@{me.username}" if me.username else "kabir"

    is_mentioned = event.mentioned or (my_username.lower() in text.lower())
    is_reply_to_me = event.is_reply and (await event.get_reply_message()).sender_id == me.id
    
    greetings = ["hi", "hello", "hey", "hlo", "suno", "bruh", "bro", "kabir"]
    is_greeting = any(word == text.lower().strip() for word in greetings) and (random.random() < 0.15)

    status, history = get_user_memory(user_id, user_name)
    is_enemy_trigger = (status == 'Enemy' and random.random() < 0.1)

    should_reply = is_mentioned or is_reply_to_me or is_greeting or is_enemy_trigger

    if should_reply:
        # --- TYPING LOOP START ---
        stop_typing = asyncio.Event()
        typing_task = asyncio.create_task(keep_typing(event.chat_id, stop_typing))
        
        try:
            # Sochne ka natak karne ke liye thoda shuruati delay
            await asyncio.sleep(random.randint(2, 4))
            
            ai_client = get_ai_client()
            formatted_instruction = SYSTEM_INSTRUCTION_TEMPLATE.format(
                user_name=user_name, status=status, history=history
            )
            
            ai_prompt = f"{user_name} said: {text}"
            if is_reply_to_me:
                ai_prompt = f"[Context: Reply to your msg] -> {user_name} said: {text}"

            # Gemini API se answer generate ho raha hai (is dauran typing chalu rahegi)
            response = ai_client.models.generate_content(
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
                except:
                    pass
            
            # --- ANTI-BOT REPLACEMENT ---
            bot_words = [r"\bai\b", r"\bbot\b", r"\blanguage model\b", r"\bassistant\b"]
            for pattern in bot_words:
                if re.search(pattern, clean_reply.lower()):
                    clean_reply = random.choice([
                        "kya fook ke aaya h bhai? mai bot dikhta hu? 💀",
                        "bruh is tripping, mai real hu no cap 😂",
                        "bhai tera dimaag cooked h fr, mai kab se bot bina?"
                    ])
                    break

            updated_history = f"{user_name}: {text[:20]} -> Kabir: {clean_reply[:20]} | {new_context}"
            update_user_memory(user_id, new_status, updated_history[:200])

            # --- TYPING STOP (Message bhejne se bilkul pehle) ---
            stop_typing.set()
            await typing_task

            # --- STICKER OR TEXT SEND ---
            if "[STICKER:" in clean_reply or "STICKER" in clean_reply.upper():
                sticker_sent = await send_random_sticker(event.chat_id, event.id)
                if not sticker_sent:
                    await event.reply("mood nahi hai baat karne ka 🫠")
            else:
                if random.random() < 0.08:
                    await event.reply(clean_reply + " *")
                else:
                    await event.reply(clean_reply)
                
        except Exception as e:
            print(f"Global AI Error: {e}")
            # Kuch gadbad ho jaye toh typing loop ko lazmi band karna h
            stop_typing.set()
            try:
                await typing_task
            except:
                pass

print("🌍 Kabir full realistic typing status ke sath live hai, fr fr...")
client.start()
client.run_until_disconnected()
