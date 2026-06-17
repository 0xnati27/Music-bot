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

DOWNLOAD_DIR = 'downloads'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# 🛡️ SPEED THROTTLE
MAX_CONCURRENT_TASKS = 3
download_lock = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

def is_url(text):
    regex = r'(https?://(?:www\.|(?!www))[a-zA-Z0-9][a-zA-Z0-9-]+[a-zA-Z0-9]\.[^\s]{2,}|www\.[a-zA-Z0-9][a-zA-Z0-9-]+[a-zA-Z0-9]\.[^\s]{2,}|https?://(?:www\.|(?!www))[a-zA-Z0-9]+\.[^\s]{2,}|www\.[a-zA-Z0-9]+\.[^\s]{2,})'
    return re.match(regex, text)

def clean_markdown(text):
    if not text:
        return "Unknown"
    return re.sub(r'[_*`\[\]()]', '', text)

def format_duration(seconds):
    if not seconds:
        return "--:--"
    mins = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{mins}:{secs:02d}"

async def resolve_spotify_link(url):
    try:
        api_url = f"https://open.spotify.com/oembed?url={urllib.parse.quote(url)}"
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    title = data.get('title', '')
                    artist = data.get('author_name', '')
                    return f"{title} {artist}".strip()
    except Exception:
        pass
    return None

def clean_song_info(title, uploader):
    """🛠️ Smart-Scrubber: Removes YouTube garbage from titles so the lyrics API can read it"""
    # If the title is formatted like "Artist - Song", split it
    if " - " in title:
        parts = title.split(" - ", 1)
        c_artist = parts[0]
        c_title = parts[1]
    else:
        c_title = title
        c_artist = uploader

    # Aggressively strip out brackets, parentheses, and common YouTube junk words
    c_title = re.sub(r'\(.*?\)|\[.*?\]|【.*?】', '', c_title)
    garbage = ['official', 'video', 'audio', 'lyric', 'lyrics', 'hq', 'hd', '320kbps', 'music', 'feat', 'ft']
    pattern = re.compile(r'\b(' + '|'.join(garbage) + r')\b', flags=re.IGNORECASE)
    c_title = pattern.sub('', c_title).strip()
    
    # Clean the artist string
    if ' - ' in c_artist:
        c_artist = c_artist.split(' - ')[0]
    c_artist = re.sub(r'\(.*?\)|\[.*?\]', '', c_artist).strip()

    # Fallback if scrubbing wiped the title entirely
    if not c_title:
        c_title = title.split(' - ')[-1] if ' - ' in title else title
        
    return c_title, c_artist

async def fetch_lyrics(title, artist):
    """🔍 Two-Pass Lyrics Engine"""
    clean_t, clean_a = clean_song_info(title, artist)
    
    # Pass 1: Try highly accurate Artist + Title search
    query_1 = f"{clean_t} {clean_a}".strip()
    url_1 = f"https://lrclib.net/api/search?q={urllib.parse.quote(query_1)}"
    
    # Pass 2: Fallback to broad Title-only search
    query_2 = f"{clean_t}".strip()
    url_2 = f"https://lrclib.net/api/search?q={urllib.parse.quote(query_2)}"

    urls_to_try = [url_1, url_2]

    try:
        async with aiohttp.ClientSession() as session:
            for search_url in urls_to_try:
                async with session.get(search_url, timeout=8) as response:
                    if response.status == 200:
                        results = await response.json()
                        if results and isinstance(results, list):
                            for match in results:
                                if match.get('plainLyrics'):
                                    return match['plainLyrics'].strip()
    except Exception:
        pass
    return None

def get_main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("🔍 Quick Search Guide"),
        types.KeyboardButton("💽 Album Master Info"),
        types.KeyboardButton("ℹ️ System Health Status"),
        types.KeyboardButton("❌ Dismiss Control Panel")
    )
    return markup

@bot.message_handler(commands=['start', 'help'])
async def send_welcome(message):
    welcome_text = (
        "⚡ **Welcome to FinderBot Ultra v7.1 (Smart Lyrics Edition)** ⚡\n\n"
        "Send a Song Name, YouTube Link, or **Spotify Link** to instantly extract the audio and lyrics."
    )
    await bot.send_message(message.chat.id, welcome_text, parse_mode='Markdown', reply_markup=get_main_menu())

@bot.message_handler(func=lambda message: message.text in ["🔍 Quick Search Guide", "💽 Album Master Info", "ℹ️ System Health Status", "❌ Dismiss Control Panel"])
async def handle_menu_buttons(message):
    chat_id = message.chat.id
    if message.text == "🔍 Quick Search Guide":
        await bot.send_message(chat_id, "🎵 **How to Search:**\nType an artist/song name, or paste YouTube/Spotify links directly.")
    elif message.text == "💽 Album Master Info":
        await bot.send_message(chat_id, "💽 **Batch Operations:**\nPaste a YouTube Playlist link to download every song sequentially.")
    elif message.text == "ℹ️ System Health Status":
        await bot.send_message(chat_id, "🟢 **Core:** Operational\n⚡ **Speed:** Hyper-Optimized\n🟢 **Spotify Bridge:** Active\n🎤 **Smart Lyrics:** Active (Two-Pass)")
    elif message.text == "❌ Dismiss Control Panel":
        await bot.send_message(chat_id, "Control Panel hidden. Use `/start` to recall it anytime.", reply_markup=types.ReplyKeyboardRemove())

@bot.message_handler(func=lambda message: True)
async def handle_message(message):
    query = message.text.strip()
    chat_id = message.chat.id

    if "spotify.com" in query:
        status_msg = await bot.send_message(chat_id, "🟢 **Spotify Link Detected:** Translating...", parse_mode='Markdown')
        spotify_query = await resolve_spotify_link(query)
        if spotify_query:
            query = spotify_query
            await bot.edit_message_text(f"🔍 **Translated Search:** `{clean_markdown(query)}`...", chat_id, status_msg.message_id, parse_mode='Markdown')
        else:
            await bot.edit_message_text("❌ Failed to parse Spotify metadata. Link might be private.", chat_id, status_msg.message_id)
            return
    elif is_url(query):
        status_msg = await bot.send_message(chat_id, "🔗 **Analyzing Link...** Fast-tracking...", parse_mode='Markdown')
        await process_url_routing(chat_id, query, status_msg.message_id)
        return
    else:
        status_msg = await bot.send_message(chat_id, f"🔍 **Fast-Searching:** `{clean_markdown(query)}`...", parse_mode='Markdown')

    ydl_opts = {
        'default_search': 'ytsearch3',
        'extract_flat': True,
        'skip_download': True,
        'quiet': True,
        'noplaylist': True,
        'nocheckcertificate': True,
        'socket_timeout': 5,
        'extractor_args': {'youtube': {'player_client': ['android', 'web_embedded']}}
    }
    
    try:
        loop = asyncio.get_event_loop()
        with YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(f"ytsearch3:{query}", download=False))
        
        entries = info.get('entries', [])
        if not entries:
            await bot.edit_message_text("❌ No database matches located.", chat_id, status_msg.message_id)
            return

        keyboard = types.InlineKeyboardMarkup(row_width=1)
        for entry in entries:
            if not entry or not entry.get('id'):
                continue
            title = clean_markdown(entry.get('title', 'Unknown Track'))
            duration = format_duration(entry.get('duration'))
            keyboard.add(types.InlineKeyboardButton(text=f"🎵 {title[:32]}... [{duration}]", callback_data=f"sel_{entry.get('id')}"))

        keyboard.add(types.InlineKeyboardButton(text="❌ Close Menu", callback_data="cancel_search"))
        await bot.edit_message_text("🎶 **Select your track:**", chat_id, status_msg.message_id, reply_markup=keyboard, parse_mode='Markdown')
        
    except Exception:
        await bot.edit_message_text("❌ Network timeout. Try searching again.", chat_id, status_msg.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('sel_'))
async def open_track_dashboard(call):
    chat_id = call.message.chat.id
    video_id = call.data.split('_')[1]
    
    options_keyboard = types.InlineKeyboardMarkup(row_width=2)
    options_keyboard.add(
        types.InlineKeyboardButton(text="🎵 MP3 Audio + Auto Lyrics", callback_data=f"aud_{video_id}"),
        types.InlineKeyboardButton(text="🎥 HD MP4 Video", callback_data=f"vid_{video_id}"),
        types.InlineKeyboardButton(text="⬅️ Cancel", callback_data="cancel_search")
    )
    
    await bot.edit_message_text(
        text=f"🛠️ **Operations Ready.**\nChoose your download format:", 
        chat_id=chat_id, 
        message_id=call.message.message_id, 
        reply_markup=options_keyboard,
        parse_mode='Markdown'
    )

@bot.callback_query_handler(func=lambda call: call.data == 'cancel_search')
async def cancel_search_action(call):
    try:
        await bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass

@bot.callback_query_handler(func=lambda call: call.data.startswith(('aud_', 'vid_')))
async def handle_dashboard_execution(call):
    chat_id = call.message.chat.id
    action, video_id = call.data.split('_')
    url = f"https://www.youtube.com/watch?v={video_id}"
    
    mode_text = "Audio" if action == "aud" else "Video"
    await bot.edit_message_text(f"📥 High-Speed {mode_text} Extraction Started...", chat_id, call.message.message_id)
    await process_url_routing(chat_id, url, call.message.message_id, mode=("audio" if action == "aud" else "video"))

async def process_url_routing(chat_id, url, status_msg_id, mode="audio"):
    meta_opts = {
        'extract_flat': 'in_playlist',
        'quiet': True,
        'nocheckcertificate': True,
        'socket_timeout': 10,
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
            
            await bot.edit_message_text(f"💽 **Batch Mode:** `{album_title}`\nCompiling {total_songs} segments...", chat_id, status_msg_id)
            
            for index, entry in enumerate(entries, start=1):
                if not entry:
                    continue
                track_url = f"https://www.youtube.com/watch?v={entry.get('id')}"
                await download_single_track(chat_id, track_url, status_msg_id, current_index=index, total=total_songs, mode=mode)
            
            await bot.send_message(chat_id, f"✅ **Batch operations completed.**")
            try:
                await bot.delete_message(chat_id, status_msg_id)
            except Exception:
                pass
        else:
            await download_single_track(chat_id, url, status_msg_id, mode=mode)
            
    except Exception as e:
        await bot.edit_message_text(f"❌ Routing Fault: {clean_markdown(str(e))}", chat_id, status_msg_id)

async def download_single_track(chat_id, url, status_msg_id, current_index=None, total=None, mode="audio"):
    async with download_lock:
        file_id = f"{chat_id}_{int(asyncio.get_event_loop().time())}_{current_index or 0}"
        out_template = os.path.join(DOWNLOAD_DIR, f"media_{file_id}.%(ext)s")
        
        if mode == "audio":
            expected_file = os.path.join(DOWNLOAD_DIR, f"media_{file_id}.mp3")
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
                'socket_timeout': 15,
                'extractor_args': {'youtube': {'player_client': ['android', 'web_embedded']}}
            }
        else:
            expected_file = os.path.join(DOWNLOAD_DIR, f"media_{file_id}.mp4")
            ydl_opts = {
                'format': 'best[ext=mp4]/best',
                'outtmpl': out_template,
                'noplaylist': True,
                'quiet': True,
                'nocheckcertificate': True,
                'socket_timeout': 15,
                'extractor_args': {'youtube': {'player_client': ['android', 'web_embedded']}}
            }
        
        try:
            prog_text = f"⚡ **Downloading:** {current_index or 1}/{total or 1} [{mode.upper()}]..."
            await bot.edit_message_text(prog_text, chat_id, status_msg_id)
            
            loop = asyncio.get_event_loop()
            with YoutubeDL(ydl_opts) as ydl:
                info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
            
            track_title = clean_markdown(info.get('title', 'Unknown Track'))
            uploader = clean_markdown(info.get('uploader', 'Various Artists'))
            
            if os.path.exists(expected_file):
                # 🚀 Start fetching lyrics concurrently
                lyrics_task = asyncio.create_task(fetch_lyrics(track_title, uploader)) if mode == "audio" else None

                with open(expected_file, 'rb') as out_media:
                    if mode == "audio":
                        await bot.send_audio(
                            chat_id=chat_id, audio=out_media, title=track_title, performer=uploader,
                            caption=f"✅ **Extracted Successfully:** `{track_title}`", parse_mode='Markdown'
                        )
                    else:
                        await bot.send_video(
                            chat_id=chat_id, video=out_media,
                            caption=f"✅ **Extracted Successfully:** `{track_title}`", parse_mode='Markdown'
                        )
                
                os.remove(expected_file)
                try:
                    await bot.delete_message(chat_id, status_msg_id)
                except Exception:
                    pass

                # 🔥 AUTO-LYRICS: Send them, or notify if they couldn't be found
                if lyrics_task:
                    lyrics_payload = await lyrics_task
                    if lyrics_payload:
                        await bot.send_message(chat_id, f"🎤 **Lyrics:** `{track_title}`\n━━━━━━━━━━━━━━━━━━━━\n\n{lyrics_payload}"[:4090], parse_mode='Markdown')
                    else:
                        await bot.send_message(chat_id, f"⚠️ *Note: Lyrics database returned zero matches for* `{track_title}`.", parse_mode='Markdown')
            else:
                if not current_index:
                    await bot.edit_message_text("❌ Render processing failure.", chat_id, status_msg_id)
        except Exception as e:
            if not current_index:
                await bot.edit_message_text(f"❌ Execution failure: {clean_markdown(str(e))}", chat_id, status_msg_id)
        finally:
            for file in os.listdir(DOWNLOAD_DIR):
                if file_id in file:
                    try:
                        os.remove(os.path.join(DOWNLOAD_DIR, file))
                    except Exception:
                        pass

if __name__ == '__main__':
    print("==================================================")
    print("👑 V7.1 SMART-LYRICS EXTRACTOR ENGINE LIVE")
    print("==================================================")
    asyncio.run(bot.polling())

