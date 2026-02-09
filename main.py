"""
Telegram Media Downloader Bot
Deploy on Railway with Python 3.11+
"""

import os
import re
import asyncio
import logging
from datetime import datetime
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.constants import ParseMode
import yt_dlp
import aiohttp
import aiofiles

# Configuration
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BASE44_API_URL = os.getenv("BASE44_API_URL", "https://api.base44.com")
BASE44_APP_ID = os.getenv("BASE44_APP_ID")
BASE44_API_KEY = os.getenv("BASE44_API_KEY")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

# Limits
FREE_MAX_SIZE = 1 * 1024 * 1024 * 1024  # 1GB
PREMIUM_MAX_SIZE = 5 * 1024 * 1024 * 1024  # 5GB

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Platform detection patterns
PLATFORM_PATTERNS = {
    "youtube": r"(youtube\.com|youtu\.be)",
    "instagram": r"instagram\.com",
    "tiktok": r"tiktok\.com",
    "twitter": r"(twitter\.com|x\.com)",
    "facebook": r"facebook\.com",
    "vimeo": r"vimeo\.com",
    "soundcloud": r"soundcloud\.com",
    "spotify": r"spotify\.com",
}

# Quality options
QUALITY_OPTIONS = ["180p", "240p", "360p", "480p", "720p", "1080p", "1440p", "4k", "best"]

class Base44Client:
    """Client for syncing data with Base44 dashboard"""
    
    def __init__(self):
        self.base_url = BASE44_API_URL
        self.headers = {
            "Authorization": f"Bearer {BASE44_API_KEY}",
            "X-App-ID": BASE44_APP_ID
        }
    
    async def sync_user(self, user_data: dict):
        async with aiohttp.ClientSession() as session:
            # Check if user exists
            async with session.get(
                f"{self.base_url}/entities/TelegramUser",
                headers=self.headers,
                params={"telegram_id": user_data["telegram_id"]}
            ) as resp:
                users = await resp.json()
            
            if users:
                # Update existing
                await session.patch(
                    f"{self.base_url}/entities/TelegramUser/{users[0]['id']}",
                    headers=self.headers,
                    json=user_data
                )
                return users[0]
            else:
                # Create new
                async with session.post(
                    f"{self.base_url}/entities/TelegramUser",
                    headers=self.headers,
                    json=user_data
                ) as resp:
                    return await resp.json()
    
    async def get_user(self, telegram_id: str) -> Optional[dict]:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}/entities/TelegramUser",
                headers=self.headers,
                params={"telegram_id": telegram_id}
            ) as resp:
                users = await resp.json()
                return users[0] if users else None
    
    async def log_download(self, download_data: dict):
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/entities/Download",
                headers=self.headers,
                json=download_data
            ) as resp:
                return await resp.json()
    
    async def update_download(self, download_id: str, data: dict):
        async with aiohttp.ClientSession() as session:
            await session.patch(
                f"{self.base_url}/entities/Download/{download_id}",
                headers=self.headers,
                json=data
            )
    
    async def get_settings(self) -> dict:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}/entities/BotSettings",
                headers=self.headers
            ) as resp:
                settings = await resp.json()
                return {s["setting_key"]: s["setting_value"] for s in settings}
    
    async def get_pending_broadcasts(self) -> list:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}/entities/Broadcast",
                headers=self.headers,
                params={"status": "draft"}
            ) as resp:
                return await resp.json()

base44 = Base44Client()

def detect_platform(url: str) -> str:
    for platform, pattern in PLATFORM_PATTERNS.items():
        if re.search(pattern, url, re.IGNORECASE):
            return platform
    return "other"

def get_quality_keyboard(url: str) -> InlineKeyboardMarkup:
    keyboard = []
    row = []
    for i, quality in enumerate(QUALITY_OPTIONS):
        row.append(InlineKeyboardButton(quality, callback_data=f"quality:{quality}:{url[:50]}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    # Add audio-only option
    keyboard.append([InlineKeyboardButton("🎵 Audio Only (MP3)", callback_data=f"quality:audio:{url[:50]}")])
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Sync user to Base44
    await base44.sync_user({
        "telegram_id": str(user.id),
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name or "",
        "last_active": datetime.utcnow().isoformat()
    })
    
    settings = await base44.get_settings()
    welcome_msg = settings.get("welcome_message", "Welcome! Send me a link to download.")
    
    await update.message.reply_text(welcome_msg, parse_mode=ParseMode.MARKDOWN)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = await base44.get_settings()
    help_msg = settings.get("help_message", "Send a video/audio link to download.")
    await update.message.reply_text(help_msg, parse_mode=ParseMode.MARKDOWN)

async def premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = await base44.get_settings()
    premium_msg = settings.get("premium_message", "Contact admin for premium.")
    await update.message.reply_text(premium_msg, parse_mode=ParseMode.MARKDOWN)

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    user = update.effective_user
    
    # Update last active
    await base44.sync_user({
        "telegram_id": str(user.id),
        "last_active": datetime.utcnow().isoformat()
    })
    
    # Detect platform
    platform = detect_platform(url)
    
    # Store URL in context for callback
    context.user_data["pending_url"] = url
    context.user_data["platform"] = platform
    
    # Show quality selection
    await update.message.reply_text(
        f"🎬 Detected: *{platform.upper()}*\n\nSelect quality:",
        reply_markup=get_quality_keyboard(url),
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_quality_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data.split(":")
    quality = data[1]
    url = context.user_data.get("pending_url", "")
    platform = context.user_data.get("platform", "other")
    user = update.effective_user
    
    if not url:
        await query.edit_message_text("❌ Session expired. Please send the link again.")
        return
    
    # Check user limits
    db_user = await base44.get_user(str(user.id))
    is_premium = db_user.get("is_premium", False) if db_user else False
    is_banned = db_user.get("is_banned", False) if db_user else False
    
    if is_banned:
        await query.edit_message_text("❌ You are banned from using this bot.")
        return
    
    max_size = PREMIUM_MAX_SIZE if is_premium else FREE_MAX_SIZE
    
    # Log download start
    download_record = await base44.log_download({
        "telegram_user_id": str(user.id),
        "url": url,
        "platform": platform,
        "quality": quality,
        "status": "downloading",
        "media_type": "audio" if quality == "audio" else "video",
        "format": "mp3" if quality == "audio" else "mp4"
    })
    download_id = download_record.get("id")
    
    status_msg = await query.edit_message_text("⏳ Starting download...")
    
    try:
        # Configure yt-dlp
        if quality == "audio":
            ydl_opts = {
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }],
                "outtmpl": f"/tmp/{user.id}_%(title)s.%(ext)s",
            }
        else:
            height = {"180p": 180, "240p": 240, "360p": 360, "480p": 480, 
                     "720p": 720, "1080p": 1080, "1440p": 1440, "4k": 2160, "best": None}[quality]
            
            format_str = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]" if height else "bestvideo+bestaudio/best"
            
            ydl_opts = {
                "format": format_str,
                "merge_output_format": "mp4",
                "outtmpl": f"/tmp/{user.id}_%(title)s.%(ext)s",
            }
        
        # Download
        await status_msg.edit_text("📥 Downloading... 0%")
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "video")
            filename = ydl.prepare_filename(info)
            
            # Handle audio conversion filename
            if quality == "audio":
                filename = filename.rsplit(".", 1)[0] + ".mp3"
        
        # Check file size
        file_size = os.path.getsize(filename)
        
        if file_size > max_size:
            os.remove(filename)
            limit_gb = max_size / (1024**3)
            await base44.update_download(download_id, {
                "status": "failed",
                "error_message": f"File too large ({file_size / (1024**3):.1f}GB > {limit_gb:.0f}GB limit)"
            })
            
            if not is_premium:
                await status_msg.edit_text(
                    f"❌ File is {file_size / (1024**3):.1f}GB, exceeds free limit of {limit_gb:.0f}GB.\n\n"
                    "⭐ Upgrade to Premium for larger files!"
                )
            else:
                await status_msg.edit_text(f"❌ File too large ({file_size / (1024**3):.1f}GB)")
            return
        
        # Upload to Telegram
        await status_msg.edit_text("📤 Uploading to Telegram...")
        
        async with aiofiles.open(filename, "rb") as f:
            file_data = await f.read()
        
        if quality == "audio":
            await update.effective_chat.send_audio(
                audio=file_data,
                title=title,
                filename=f"{title}.mp3",
                caption=f"🎵 {title}"
            )
        else:
            await update.effective_chat.send_video(
                video=file_data,
                filename=f"{title}.mp4",
                caption=f"🎬 {title}\n📊 Quality: {quality}"
            )
        
        # Update download record
        await base44.update_download(download_id, {
            "status": "completed",
            "title": title,
            "file_size": file_size,
            "duration": info.get("duration")
        })
        
        # Update user stats
        if db_user:
            await base44.sync_user({
                "telegram_id": str(user.id),
                "total_downloads": (db_user.get("total_downloads", 0) or 0) + 1,
                "total_data_downloaded": (db_user.get("total_data_downloaded", 0) or 0) + file_size
            })
        
        await status_msg.edit_text("✅ Download complete!")
        
        # Cleanup
        os.remove(filename)
        
    except Exception as e:
        logger.error(f"Download error: {e}")
        await base44.update_download(download_id, {
            "status": "failed",
            "error_message": str(e)[:500]
        })
        
        settings = await base44.get_settings()
        error_msg = settings.get("error_message", "❌ Download failed.")
        await status_msg.edit_text(f"{error_msg}\n\nError: {str(e)[:100]}")

# Admin commands
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        return
    
    # Broadcast logic - get from Base44 dashboard
    await update.message.reply_text("📡 Broadcasts are managed from the dashboard.")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = await base44.get_user(str(user.id))
    
    if db_user:
        downloads = db_user.get("total_downloads", 0) or 0
        data = db_user.get("total_data_downloaded", 0) or 0
        data_gb = data / (1024**3)
        premium = "⭐ Premium" if db_user.get("is_premium") else "Free"
        
        await update.message.reply_text(
            f"📊 *Your Stats*\n\n"
            f"Status: {premium}\n"
            f"Downloads: {downloads}\n"
            f"Data: {data_gb:.2f} GB",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text("No stats yet. Start downloading!")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("premium", premium_command))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CallbackQueryHandler(handle_quality_selection, pattern="^quality:"))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"https?://"), handle_url))
    
    logger.info("Bot starting...")
    app.run_polling()

if __name__ == "__main__":
    main()
