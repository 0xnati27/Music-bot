cat << 'EOF' > finderbot.py
import os
import asyncio
import re
import aiohttp
import urllib.parse
from telebot.async_telebot import AsyncTeleBot
from telebot import types
from yt_dlp import YoutubeDL
from dotenv import load_dotenv

load_dotenv()
API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

if not API_TOKEN:
    raise ValueError("❌ CRITICAL ERROR: TELEGRAM_BOT_TOKEN is missing!")

bot = AsyncTeleBot(API_TOKEN)
DOWNLOAD_DIR = 'downloads'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
download_lock = asyncio.Semaphore(3)

def is_url(text):
    return re.match(r'(https?://[^\s]+)', text)

def clean_markdown(text):
    return re.sub(r'[_*`\[\]()]', '', text) if text else "Unknown"

def format_duration(seconds):
    if not seconds: return "--:--"
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"

def clean_song_info(title, uploader):
    if " - " in title:
        parts = title.split(" - ", 1)
        c_artist, c_title = parts[0], parts[1]
    else:
        c_title, c_artist = title, uploader
    c_title = re.sub(r'\(.*?\)|\[.*?\]|【.*?】', '', c_title)
    garbage = ['official', 'video', 'audio', 'lyric', 'lyrics', 'hq', 'hd', 'music', 'feat', 'ft']
    c_title = re.sub(r'\b(' + '|'.join(garbage) + r')\b', '', c_title, flags=re.IGNORECASE).strip()
    return c_title, c_artist

async def fetch_lyrics(title, artist):
    clean_t, clean_a = clean_song_info(title, artist)
    urls = [
        f"https://lrclib.net/api/search?q={urllib.parse.quote(f'{clean_t} {clean_a}')}",
        f"https://lrclib.net/api/search?q={urllib.parse.quote(clean_t)}"
    ]
    try:
        async with aiohttp.ClientSession() as session:
            for url in urls:
                async with session.get(url, timeout=8) as res:
                    if res.status == 200:
                        data = await res.json()
                        if data and data[0].get('plainLyrics'):
                            return data[0]['plainLyrics'].strip()
    except Exception: pass
    return None

@bot.message_handler(commands=['start', 'help'])
async def send_welcome(message):
    await bot.send_message(message.chat.id, "⚡ **Welcome to FinderBot Ultra v7.1**\n\nSend a Song Name, YouTube Link, or Spotify Link to instantly extract the audio and lyrics.", parse_mode='Markdown')

@bot.message_handler(func=lambda message: True)
async def handle_message(message):
    query = message.text.strip()
    chat_id = message.chat.id
    status_msg = await bot.send_message(chat_id, f"🔍 **Searching:** `{clean_markdown(query)}`...", parse_mode='Markdown')

    ydl_opts = {
        'default_search': 'ytsearch3', 
        'extract_flat': True, 
        'skip_download': True, 
        'quiet': True,
        'extractor_args': {'youtube': {'player_client': ['android']}}
    }
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.get_event_loop().run_in_executor(None, lambda: ydl.extract_info(f"ytsearch3:{query}", download=False))
        
        entries = info.get('entries', [])
        if not entries:
            await bot.edit_message_text("❌ አልተገኘም::", chat_id, status_msg.message_id)
            return

        keyboard = types.InlineKeyboardMarkup(row_width=1)
        for entry in entries:
            if entry:
                keyboard.add(types.InlineKeyboardButton(text=f"🎵 {clean_markdown(entry.get('title'))[:30]}", callback_data=f"aud_{entry.get('id')}"))
        await bot.edit_message_text("🎶 **ይምረጡ:**", chat_id, status_msg.message_id, reply_markup=keyboard, parse_mode='Markdown')
    except Exception as e:
        await bot.edit_message_text(f"❌ Error: YouTube bypass failed.", chat_id, status_msg.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('aud_'))
async def handle_download(call):
    chat_id = call.message.chat.id
    video_id = call.data.split('_')[1]
    url = f"https://www.youtube.com/watch?v={video_id}"
    await bot.edit_message_text("📥 በመካሄድ ላይ ነው...", chat_id, call.message.message_id)
    
    async with download_lock:
        file_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.mp3")
        ydl_opts = {
            'format': 'bestaudio/best', 'outtmpl': os.path.join(DOWNLOAD_DIR, f"{video_id}.%(ext)s"),
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'}],
            'quiet': True,
            'extractor_args': {'youtube': {'player_client': ['android']}}
        }
        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.get_event_loop().run_in_executor(None, lambda: ydl.extract_info(url, download=True))
            
            title, uploader = info.get('title', 'Track'), info.get('uploader', 'Artist')
            lyrics_task = asyncio.create_task(fetch_lyrics(title, uploader))
            
            if os.path.exists(file_path):
                with open(file_path, 'rb') as f:
                    await bot.send_audio(chat_id, f, title=title, performer=uploader, caption=f"✅ `{title}`", parse_mode='Markdown')
                os.remove(file_path)
                await bot.delete_message(chat_id, call.message.message_id)
                
                lyrics = await lyrics_task
                if lyrics:
                    await bot.send_message(chat_id, f"🎤 **Lyrics:**\n━━━━━━━━━\n\n{lyrics}"[:4090], parse_mode='Markdown')
                else:
                    await bot.send_message(chat_id, "⚠️ ግጥሙ በዳታቤዙ ውስጥ አልተገኘም::")
        except Exception as e:
            await bot.send_message(chat_id, f"❌ Download Error: YouTube blocked the request.")

if __name__ == '__main__':
    asyncio.run(bot.polling())
EOF

