import os
import random
import asyncio
import sqlite3
import re
import traceback
from telethon import TelegramClient, events
from telethon.sessions import StringSession
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
API_KEYS = [k for k in API_KEYS if k]

# REAL STICKER PACK SHORT NAMES
STICKER_PACKS = ["Gaddar_Chshma", "cat_meme_stickers", "Anime_Slang"] 

if not SESSION_STRING:
    raise ValueError("Bro, environment variables me SESSION_STRING missing h!")

# --- DATABASE SETUP ---
db_path = "/data/kabir_memory.db" if os.path.exists("/data") else "kabir_memory.db"
conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
cursor = conn.cursor()
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
    except Exception:
        print("❌ DB Read Error Traceback:")
        traceback.print_exc()
        return "Stranger", ""

def update_user_memory(user_id, chat_id, new_relationship, new_history):
    try:
        c = conn.cursor()
        c.execute("UPDATE memory SET relationship = ?, chat_history = ? WHERE user_id = ? AND chat_id = ?", 
                  (new_relationship, new_history, user_id, chat_id))
        conn.commit()
    except Exception:
        print("❌ DB Write Error Traceback:")
        traceback.print_exc()

def get_ai_client():
    if not API_KEYS:
        raise ValueError("Bro, Railway me kam se kam ek valid Gemini API Key dalo!")
    return genai.Client(api_key=random.choice(API_KEYS))

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

# --- 📦 STICKER SYSTEM ---
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
    except Exception:
        print(f"❌ Sticker Send Error ({pack_name}) Traceback:")
        traceback.print_exc()
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
1. Aap koi AI, bot, ya assistant NAHI ho. Aap asli insaan ho jo phone se chat kar raha h fr.
2. EMOJI RULE: Gen-Z emojis (💀, 😭, 🤡, 💅, 🎀, 😩, 🫠) ko chat ke beech me use karo natural tareeqe se.
3. STICKER RULE: Agar reaction dena ho toh reply me SIRF likho: [STICKER: REACTION].

Current User Profile:
- Name: {user_name}
- Relation status: {status}
- Puraani Memory Context: {history}

CRITICAL: Reply ke aakhiri me ek naya line chor kar, status update karo:
[STATUS: Friend/Enemy/Crush/Stranger] | [CONTEXT: Short note]
"""

# --- 🔍 GLOBAL GROUP CHAT HANDLER ---
@client.on(events.NewMessage(incoming=True))
async def global_group_chat_handler(event):
    # # NEW: Whitelist completely removed, works on every group
    if not event.is_group:
        return

    text = event.raw_text
    sender = await event.get_sender()
    if not sender: return
    
    user_id = sender.id
    user_name = sender.first_name or "Someone"
    chat_id = event.chat_id
    
    me = await client.get_me()
    my_username = f"@{me.username}" if me.username else "kabir"

    # Strict Trigger evaluation as requested
    is_mentioned = event.mentioned or (my_username.lower() in text.lower())
    
    is_reply_to_me = False
    if event.is_reply:
        reply_msg = await event.get_reply_message()
        if reply_msg and reply_msg.sender_id == me.id:
            is_reply_to_me = True

    # DEBUG LOG ADD-ON
    print(
        f"GROUP={event.chat_id} "
        f"MENTIONED={is_mentioned} "
        f"REPLY={is_reply_to_me} "
        f"TEXT={event.raw_text}"
    )

    should_reply = is_mentioned or is_reply_to_me

    if should_reply:
        print(f"🚀 Kabir AI responding to target triggers in group {chat_id}")
        status, history = get_user_memory(user_id, chat_id, user_name)
        
        stop_typing = asyncio.Event()
        typing_task = asyncio.create_task(keep_typing(chat_id, stop_event=stop_typing))
        
        try:
            await asyncio.sleep(random.randint(1, 2))
            ai_client = get_ai_client()
            formatted_instruction = SYSTEM_INSTRUCTION_TEMPLATE.format(
                user_name=user_name, status=status, history=history
            )
            
            ai_prompt = f"{user_name} said: {text}"

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
            
            # Anti-Bot Filter
            if re.search(r"\bi am an ai\b|\bas a language model\b|\bi am a bot\b", clean_reply.lower()):
                clean_reply = "bhai tera dimaag cooked h fr, mai kab se bot bana? 💀"

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
                    
            print(f"📤 Successfully sent reply to group {chat_id}")
                
        except Exception:
            print("❌ CRITICAL ERROR IN KABIR PROCESSOR:")
            traceback.print_exc()
            stop_typing.set()
            try:
                await typing_task
            except Exception:
                pass

# --- 🚀 STARTUP SEQUENCE ADD-ON ---
async def main():
    await client.start()
    
    # Startup log add-on
    me = await client.get_me()
    print(f"Logged in as: {me.first_name} (@{me.username})")
    
    await client.run_until_disconnected()

if __name__ == '__main__':
    client.loop.run_until_complete(main())
