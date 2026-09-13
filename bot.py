import os
import stat
import urllib.request
import zipfile
import sqlite3
import asyncio
from telethon import TelegramClient, events, Button

# --- Environment & Security Variables ---
# Set these in your Kuberns Environment Variables dashboard
API_ID = int(os.getenv('API_ID', 0))
API_HASH = os.getenv('API_HASH', '')
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
OWNER_ID = int(os.getenv('OWNER_ID', 0))  # Your Telegram ID (Whitelist)


def setup_rclone():
    """Dynamically download Rclone v1.73+ for native Filen support on boot."""
    if not os.path.exists('./rclone'):
        print("Downloading Rclone binary...")
        url = "https://downloads.rclone.org/v1.73.0/rclone-v1.73.0-linux-amd64.zip"
        urllib.request.urlretrieve(url, "rclone.zip")
        with zipfile.ZipFile("rclone.zip", 'r') as zip_ref:
            zip_ref.extract("rclone-v1.73.0-linux-amd64/rclone")
        os.rename("rclone-v1.73.0-linux-amd64/rclone", "./rclone")
        os.chmod("./rclone", os.stat("./rclone").st_mode | stat.S_IEXEC)
        os.remove("rclone.zip")
        os.rmdir("rclone-v1.73.0-linux-amd64")


# --- SQLite State Management ---
conn = sqlite3.connect("bot_state.db", check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS config 
             (id INT PRIMARY KEY, target_chat INT, active_folder TEXT, mode TEXT)''')
c.execute("INSERT OR IGNORE INTO config VALUES (1, 0, 'Telegram_Uploads', 'auto')")
conn.commit()

# --- Bot Client ---
client = TelegramClient('filen_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)


async def upload_to_filen(file_path, folder_name, progress_msg):
    """Executes Rclone as a subprocess to securely push the file."""
    await progress_msg.edit(f"☁️ Uploading to Filen directory: `{folder_name}`...")

    cmd = [
        "./rclone", "--config", "rclone.conf",
        "copy", file_path, f"filen:{folder_name}"
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()

    if os.path.exists(file_path):
        os.remove(file_path)  # Auto-cleanup

    if process.returncode == 0:
        await progress_msg.edit(f"✅ **Success!** File secured in `filen:{folder_name}`")
    else:
        await progress_msg.edit(f"❌ **Upload Failed:**\n`{stderr.decode('utf-8')[:200]}`")


# --- Commands ---
@client.on(events.NewMessage(pattern='/start'))
async def cmd_start(event):
    if event.sender_id != OWNER_ID: return
    await event.respond("🛡️ **Filen Uploader Ready.** Send a file to begin.")


@client.on(events.NewMessage(pattern=r'/folder (.+)'))
async def cmd_folder(event):
    if event.sender_id != OWNER_ID: return
    new_folder = event.pattern_match.group(1)
    c.execute("UPDATE config SET active_folder = ? WHERE id = 1", (new_folder,))
    conn.commit()
    await event.respond(f"📁 Active destination updated to: `{new_folder}`")


@client.on(events.NewMessage(pattern='/settarget'))
async def cmd_settarget(event):
    if event.sender_id != OWNER_ID: return
    chat_id = event.chat_id if not event.is_private else 0
    c.execute("UPDATE config SET target_chat = ? WHERE id = 1", (chat_id,))
    conn.commit()
    await event.respond(f"🎯 Target monitoring chat set to: `{chat_id}`")


@client.on(events.NewMessage(pattern='/mode'))
async def cmd_mode(event):
    if event.sender_id != OWNER_ID: return
    c.execute("SELECT mode FROM config WHERE id = 1")
    current_mode = c.fetchone()[0]
    new_mode = 'ask' if current_mode == 'auto' else 'auto'
    c.execute("UPDATE config SET mode = ? WHERE id = 1", (new_mode,))
    conn.commit()
    await event.respond(f"⚙️ Upload mode toggled to: **{new_mode.upper()}**")


# --- File Interceptor ---
@client.on(events.NewMessage)
async def handle_files(event):
    if not event.media: return

    c.execute("SELECT target_chat, active_folder, mode FROM config WHERE id = 1")
    target_chat, active_folder, mode = c.fetchone()

    is_owner_pm = (event.is_private and event.sender_id == OWNER_ID)
    is_target_chat = (event.chat_id == target_chat and target_chat != 0)

    if is_owner_pm or is_target_chat:
        if mode == 'ask' and event.is_private:
            buttons = [
                [Button.inline(f"Upload to /{active_folder}", data=b"up_current")],
                [Button.inline("Cancel", data=b"cancel")]
            ]
            await event.reply("Intercepted file. Action?", buttons=buttons)
            return

        msg = await event.reply("⏳ **Downloading from Telegram...**")
        file_path = await event.download_media(file="downloads/")
        await upload_to_filen(file_path, active_folder, msg)


@client.on(events.CallbackQuery)
async def callback_handler(event):
    if event.sender_id != OWNER_ID: return

    if event.data == b"cancel":
        await event.edit("❌ Upload cancelled.")
        return

    if event.data == b"up_current":
        c.execute("SELECT active_folder FROM config WHERE id = 1")
        active_folder = c.fetchone()[0]

        original_msg = await event.get_message()
        replied_to = await original_msg.get_reply_message()
        await event.edit("⏳ **Downloading...**")
        file_path = await replied_to.download_media(file="downloads/")
        await upload_to_filen(file_path, active_folder, original_msg)


if __name__ == '__main__':
    setup_rclone()
    print("Bot is running...")
    client.run_until_disconnected()