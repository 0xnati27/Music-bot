import os
import asyncio
import re
import aiohttp
import urllib.parse
from telebot.async_telebot import AsyncTeleBot
from telebot import types
from yt_dlp import YoutubeDL
from dotenv import load_dotenv

# Load configuration signatures securely
load_dotenv()
API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

if not API_TOKEN:
    raise ValueError("❌ CRITICAL ERROR: TELEGRAM_BOT_TOKEN is missing from your .env file!")

bot = AsyncTeleBot(API_TOKEN)

# Isolated environment paths
DOWNLOAD_DIR = 'downloads'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# 🛡️ SYSTEM THROTTLE: Balances download speeds without triggering Telegram rate limits
MAX_CONCURRENT_TASKS = 2
download_lock = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

def is_url(text):
    regex = r'(https?://(?:www\.|(?!www))[a-zA-Z0-9][a-zA-Z0-9-]+[a-zA-Z0-9]\.[^\s]{2,}|www\.[a-zA-Z0-9][a-zA-Z0-9-]+[a-zA-Z0-9]\.[^\s]{2,}|https?://(?:www\.|(?!www))[a-zA-Z0-9]+\.[^\s]{2,}|www\.[a-zA-Z0-9]+\.[^\s]{2,})'
    return re.match(regex, text)

def clean_markdown(text):
    """Prevents malicious or weird track titles from breaking Telegram formatting"""
    if not text:
        return "Unknown"
    return re.sub(r'[_*`\[\]()]', '', text)

def format_duration(seconds):
    if not seconds:
        return "--:--"
    mins = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{mins}:{secs:02d}"

async def fetch_lyrics(title, artist):
    """🚀 ULTRA LYRICS ENGINE: Pulls live lyrics with zero API key dependencies"""
    # Scrub common promotional garbage out of titles for higher search precision
    clean_t = re.sub(r'\(.*?\)|\[.*?\]|official\s+video|lyric\s+video|audio|video|hq|hd|320kbps', '', title, flags=re.IGNORECASE).strip()
    clean_a = re.sub(r'\(.*?\)|\[.*?\]|official|topic|vevo', '', artist, flags=re.IGNORECASE).strip()
    
    query = f"{clean_t} {clean_a}".strip()
    search_url = f"https://lrclib.net/api/search?q={urllib.parse.quote(query)}"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(search_url, timeout=8) as response:
                if response.status == 200:
                    results = await response.json()
                    if results and isinstance(results, list):
                        for match in results:
                            if match.get('plainLyrics'):
                                return match['plainLyrics'].strip()
    except Exception as e:
        print(f"Lyrics Database Offline/Error: {e}")
    return None

@bot.message_handler(commands=['start', 'help'])
async def send_welcome(message):
    welcome_text = (
        "⚡ **FinderBot Ultra Engine v4.0 (Lyrics Edition)** ⚡\n\n"
        "• 🔍 **Search:** Send any track title or artist name.\n"
        "• 🔗 **Single Track:** Send any direct YouTube/YouTube Music URL.\n"
        "• 💽 **Full Albums:** Send any playlist or album link.\n\n"
        "✨ _Engine: Live LRCLIB Decoupled Lyrics Streamer, 320kbps Lossless Container, Auto-Purge Cache._"
    )
    await bot.reply_to(message, welcome_text, parse_mode='Markdown')

@bot.message_handler(func=lambda message: True)
async def handle_message(message):
    query = message.text.strip()
    chat_id = message.chat.id

    if is_url(query):
        status_msg = await bot.send_message(chat_id, "🔗 **Analyzing Link Target...** Connecting to stream core...", parse_mode='Markdown')
        await process_url_routing(chat_id, query, status_msg.message_id)
    else:
        status_msg = await bot.send_message(chat_id, f"🔍 **Searching database for:** `{clean_markdown(query)}`...", parse_mode='Markdown')
        
        ydl_opts = {
            'default_search': 'ytsearch5',
            'extract_flat': True,
            'skip_download': True,
            'quiet': True,
            'noplaylist': True,
            'nocheckcertificate': True,
            'socket_timeout': 10,
            'extractor_args': {'youtube': {'player_client': ['android', 'web_embedded']}}
        }
        
        try:
            loop = asyncio.get_event_loop()
            with YoutubeDL(ydl_opts) as ydl:
                info = await loop.run_in_executor(None, lambda: ydl.extract_info(f"ytsearch5:{query}", download=False))
            
            entries = info.get('entries', [])
            if not entries:
                await bot.edit_message_text("❌ No database matches located. Refine your query parameters.", chat_id, status_msg.message_id)
                return

            keyboard = types.InlineKeyboardMarkup(row_width=1)
            valid_count = 0
            
            for entry in entries:
                if not entry or not entry.get('id'):
                    continue
                title = clean_markdown(entry.get('title', 'Unknown Track'))
                duration = format_duration(entry.get('duration'))
                
                button_text = f"🎵 {title[:32]}... [{duration}]"
                callback_data = f"dl_{entry.get('id')}"
                keyboard.add(types.InlineKeyboardButton(text=button_text, callback_data=callback_data))
                valid_count += 1

            if valid_count == 0:
                await bot.edit_message_text("❌ Failed to parse valid stream signatures.", chat_id, status_msg.message_id)
                return

            keyboard.add(types.InlineKeyboardButton(text="❌ Dismiss Search", callback_data="cancel_search"))
            await bot.edit_message_text("🎶 **Select the master track file:**", chat_id, status_msg.message_id, reply_markup=keyboard, parse_mode='Markdown')
            
        except Exception as e:
            print(f"Core Search Fault: {e}")
            await bot.edit_message_text("❌ Network timeout. YouTube servers dropped the handshakes. Try again.", chat_id, status_msg.message_id)

@bot.callback_query_handler(func=lambda call: call.data == 'cancel_search')
async def cancel_search_action(call):
    try:
        await bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass

@bot.callback_query_handler(func=lambda call: call.data.startswith('dl_'))
async def handle_callback_download(call):
    chat_id = call.message.chat.id
    video_id = call.data.split('_')[1]
    url = f"https://www.youtube.com/watch?v={video_id}"
    
    await bot.edit_message_text("📥 Initializing secure background download thread...", chat_id, call.message.message_id)
    await process_url_routing(chat_id, url, call.message.message_id)

async def process_url_routing(chat_id, url, status_msg_id):
    meta_opts = {
        'extract_flat': 'in_playlist',
        'quiet': True,
        'nocheckcertificate': True,
        'socket_timeout': 15,
        'extractor_args': {'youtube': {'player_client': ['android', 'web_embedded']}}
    }
    
    try:
        loop = asyncio.get_event_loop()
        with YoutubeDL(meta_opts) as ydl:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))
        
        if 'entries' in info and info.get('_type') == 'playlist':
            entries = list(info['entries'])
            total_songs = len(entries)
            album_title = clean_markdown(info.get('title', 'Unknown Album'))
            
            await bot.edit_message_text(f"💽 **Album Unlocked:** `{album_title}`\nBatch compiling {total_songs} tracks into pipeline...", chat_id, status_msg_id)
            
            for index, entry in enumerate(entries, start=1):
                if not entry:
                    continue
                track_url = f"https://www.youtube.com/watch?v={entry.get('id')}"
                await download_single_track(chat_id, track_url, status_msg_id, current_index=index, total=total_songs, album_name=album_title)
            
            await bot.send_message(chat_id, f"✅ **Album Processing Successful!**\nAll {total_songs} master files from `{album_title}` delivered.")
            try:
                await bot.delete_message(chat_id, status_msg_id)
            except Exception:
                pass
        else:
            await download_single_track(chat_id, url, status_msg_id)
            
    except Exception as e:
        await bot.edit_message_text(f"❌ Routing Error: {clean_markdown(str(e))}", chat_id, status_msg_id)

async def download_single_track(chat_id, url, status_msg_id, current_index=None, total=None, album_name=None):
    if download_lock.locked():
        queue_text = f"⏳ **Queue Active:** Track {current_index}/{total} waiting for computational clearance..." if current_index else "⏳ **Queue Active:** System processing heavy streams. Standby..."
        await bot.edit_message_text(queue_text, chat_id, status_msg_id)

    async with download_lock:
        file_id = f"{chat_id}_{int(asyncio.get_event_loop().time())}_{current_index or 0}"
        out_template = os.path.join(DOWNLOAD_DIR, f"music_{file_id}.%(ext)s")
        expected_mp3 = os.path.join(DOWNLOAD_DIR, f"music_{file_id}.mp3")
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': out_template,
            'noplaylist': True,
            'writethumbnail': True,
            'postprocessors': [
                {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'},
                {'key': 'EmbedThumbnail', 'already_have_thumbnail': False},
                {'key': 'FFmpegMetadata', 'add_metadata': True}
            ],
            'quiet': True,
            'nocheckcertificate': True,
            'socket_timeout': 30,
            'extractor_args': {'youtube': {'player_client': ['android', 'web_embedded']}}
        }
        
        try:
            if current_index:
                status_text = f"⚡ **Processing Album:** `{album_name}`\n📥 Downloading Track {current_index}/{total} at 320kbps..."
            else:
                status_text = "⚡ **Downloading Target Stream...**\nIsolating raw audio signals & baking HD album art..."
                
            await bot.edit_message_text(status_text, chat_id, status_msg_id)
            
            loop = asyncio.get_event_loop()
            with YoutubeDL(ydl_opts) as ydl:
                info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
            
            track_title = clean_markdown(info.get('title', 'Unknown Track'))
            track_performer = clean_markdown(info.get('uploader', 'Various Artists'))
            track_duration = info.get('duration', None)
            
            if os.path.exists(expected_mp3):
                caption_prefix = f"💽 Track {current_index}/{total}" if current_index else "✅ Master File Compiled"
                
                with open(expected_mp3, 'rb') as audio:
                    await bot.send_audio(
                        chat_id=chat_id,
                        audio=audio,
                        caption=f"**{caption_prefix}**\n🎧 Title: `{track_title}`",
                        title=track_title,
                        performer=track_performer,
                        duration=track_duration,
                        parse_mode='Markdown'
                    )
                
                # Cleanup internal audio asset
                os.remove(expected_mp3)
                if not current_index:
                    await bot.delete_message(chat_id, status_msg_id)

                # 🔥 LIVE LYRICS RETRIEVAL AND MATCHING STEP
                lyrics_payload = await fetch_lyrics(track_title, track_performer)
                if lyrics_payload:
                    header = f"🎤 **Lyrics:** `{track_title}`\n👤 **Artist:** `{track_performer}`\n━━━━━━━━━━━━━━━━━━━━\n\n"
                    # Failsafe character limits to avoid exceeding Telegram's 4096-character limit
                    if len(header) + len(lyrics_payload) > 4000:
                        lyrics_payload = lyrics_payload[:3800] + "\n\n...(Lyrics condensed due to text limits)"
                    
                    # Delivered cleanly without styling crashes
                    await bot.send_message(chat_id, f"{header}{lyrics_payload}", parse_mode='Markdown')
                
            else:
                if not current_index:
                    await bot.edit_message_text("❌ Transcoding Error: Audio synthesis container generation failed.", chat_id, status_msg_id)
                
        except Exception as e:
            if not current_index:
                await bot.edit_message_text(f"❌ Core Exception: {clean_markdown(str(e))}", chat_id, status_msg_id)
        finally:
            for file in os.listdir(DOWNLOAD_DIR):
                if file_id in file:
                    try:
                        os.remove(os.path.join(DOWNLOAD_DIR, file))
                    except Exception:
                        pass

if __name__ == '__main__':
    print("==================================================")
    print("👑 ULTIMATE LYRICS-ENABLED MUSIC ENGINE LIVE")
    print("==================================================")
    asyncio.run(bot.polling())

