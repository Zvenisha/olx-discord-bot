import os
import asyncio
import json
import base64
from datetime import datetime, timezone, timedelta
from aiohttp import web, ClientSession
import discord
from discord.ext import commands, tasks

# --- НАЛАШТУВАННЯ КАНАЛІВ DISCORD ---
NEW_ORDERS_CHANNEL_ID = 1546490061854351422
ARCHIVE_CHANNEL_ID = 1546603811643334757

# Дані OLX API
OLX_CLIENT_ID = os.getenv("OLX_CLIENT_ID", "203013")
OLX_CLIENT_SECRET = os.getenv("OLX_CLIENT_SECRET", "SPqxrdxWxZBErpr4BKC31TudsW2Zhp2yNTqnkriF6OSTEXaR")
REDIRECT_URI = "https://olx-discord-bot-ppys.onrender.com/callback"

# Пошти тепер підтягуються автоматично, тут залишаються лише назви магазинів
ACCOUNTS = {
    "1": {"name": "Магазин 1", "email": "Очікує входу...", "access_token": None, "refresh_token": None},
    "2": {"name": "Магазин 2", "email": "Очікує входу...", "access_token": None, "refresh_token": None},
    "3": {"name": "Магазин 3", "email": "Очікує входу...", "access_token": None, "refresh_token": None}
}

processed_message_ids = set()
is_initialized = False
advert_cache = {}

# --- GITHUB СИНХРОНІЗАЦІЯ ТОКЕНІВ ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")  # Наприклад: "Zvenisha/olx-discord-bot"
FILE_PATH = "sessions/tokens.json"
SESSION_DIR = "sessions"
TOKENS_FILE = os.path.join(SESSION_DIR, "tokens.json")

def save_tokens_locally():
    if not os.path.exists(SESSION_DIR):
        os.makedirs(SESSION_DIR)
    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(ACCOUNTS, f, ensure_ascii=False, indent=4)

def load_tokens_locally():
    global ACCOUNTS
    if os.path.exists(TOKENS_FILE):
        try:
            with open(TOKENS_FILE, "r", encoding="utf-8") as f:
                saved_accounts = json.load(f)
                for acc_id, data in saved_accounts.items():
                    if acc_id in ACCOUNTS:
                        ACCOUNTS[acc_id].update(data)
            print("Дані акаунтів успішно відновлені з локального файлу!", flush=True)
        except Exception as e:
            print(f"Помилка читання локальних токенів: {e}", flush=True)

async def restore_sessions_from_drive():
    try:
        if not GITHUB_TOKEN or not GITHUB_REPO:
            print("GitHub токен або репозиторій не налаштовані.", flush=True)
            return
        
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{FILE_PATH}"
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json"
        }

        async with ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    content_base64 = data.get("content", "")
                    content_bytes = base64.b64decode(content_base64)

                    if not os.path.exists(SESSION_DIR):
                        os.makedirs(SESSION_DIR)
                    with open(TOKENS_FILE, "wb") as f:
                        f.write(content_bytes)

                    load_tokens_locally()
                    print("Токени успішно відновлені з GitHub репозиторію!", flush=True)
                else:
                    print("Файл токенів на GitHub ще не створено. Потрібен новий вхід.", flush=True)
    except Exception as e:
        print(f"Помилка при відновленні з GitHub: {e}", flush=True)

async def backup_sessions_to_drive():
    try:
        if not GITHUB_TOKEN or not GITHUB_REPO:
            print("GitHub токен або репозиторій не налаштовані для бекапу.", flush=True)
            return

        save_tokens_locally()
        if not os.path.exists(TOKENS_FILE):
            return

        with open(TOKENS_FILE, "r", encoding="utf-8") as f:
            file_content = f.read()

        encoded_content = base64.b64encode(file_content.encode("utf-8")).decode("utf-8")

        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{FILE_PATH}"
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json"
        }

        async with ClientSession() as session:
            sha = None
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    sha = data.get("sha")

            payload = {
                "message": "Update tokens.json automatically",
                "content": encoded_content
            }
            if sha:
                payload["sha"] = sha

            async with session.put(url, headers=headers, json=payload) as put_resp:
                if put_resp.status in [200, 201]:
                    print("Токени успішно збережені на GitHub!", flush=True)
                else:
                    err_text = await put_resp.text()
                    print(f"Помилка збереження токенів на GitHub: {err_text}", flush=True)
    except Exception as e:
        print(f"ПОМИЛКА при збереженні на GitHub: {e}", flush=True)

# --- 1. ВЕБ-СЕРВЕР ТА АВТОРИЗАЦІЯ ---
async def handle_ping(request):
    status_lines = [f"• {acc['name']} ({acc['email']}): {'🟢 Підключено' if acc['access_token'] else '⚪ Очікує входу'}" for acc in ACCOUNTS.values()]
    return web.Response(text="OLX Discord Bot is online!\n\nСтатус підключення акаунтів:\n" + "\n".join(status_lines))

async def handle_auth(request):
    acc_id = request.query.get("acc", "1")
    if acc_id not in ACCOUNTS:
        return web.Response(text="Невідомий номер акаунта. Доступні: 1, 2, 3", status=400)

    auth_url = (
        f"https://www.olx.ua/oauth/authorize/?"
        f"client_id={OLX_CLIENT_ID}&response_type=code&"
        f"scope=read+write+v2&redirect_uri={REDIRECT_URI}&state={acc_id}"
    )
    return web.HTTPFound(auth_url)

async def handle_callback(request):
    code = request.query.get("code")
    acc_id = request.query.get("state", "1")

    if not code:
        return web.Response(text="Помилка: код авторизації не передано.", status=400)

    token_url = "https://www.olx.ua/api/open/oauth/token"
    payload = {
        "grant_type": "authorization_code",
        "client_id": OLX_CLIENT_ID,
        "client_secret": OLX_CLIENT_SECRET,
        "code": code,
        "scope": "read write v2",
        "redirect_uri": REDIRECT_URI
    }

    async with ClientSession() as session:
        async with session.post(token_url, json=payload) as resp:
            data = await resp.json()
            if "access_token" in data and acc_id in ACCOUNTS:
                access_token = data["access_token"]
                ACCOUNTS[acc_id]["access_token"] = access_token
                ACCOUNTS[acc_id]["refresh_token"] = data.get("refresh_token")
                
                headers = {"Authorization": f"Bearer {access_token}", "Version": "2.0"}
                async with session.get("https://www.olx.ua/api/partner/users/me", headers=headers) as user_resp:
                    if user_resp.status == 200:
                        user_data = await user_resp.json()
                        email = user_data.get("data", {}).get("email")
                        if email:
                            ACCOUNTS[acc_id]["email"] = email

                acc_name = ACCOUNTS[acc_id]["name"]
                acc_email = ACCOUNTS[acc_id]["email"]
                print(f"Успішно авторизовано: {acc_name} ({acc_email})", flush=True)
                
                await backup_sessions_to_drive()
                
                return web.Response(text=f"✅ Успішно! {acc_name} ({acc_email}) підключено до бота.")
            else:
                print(f"Помилка авторизації для акка {acc_id}: {data}", flush=True)
                return web.Response(text=f"Помилка авторизації: {data}", status=400)

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/auth", handle_auth)
    app.router.add_get("/callback", handle_callback)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    print("Веб-сервер активний на порту 8080.", flush=True)

# --- 2. DISCORD БОТ ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.command(name='del', aliases=['видалити'])
async def clear_new_orders(ctx):
    if ctx.channel.id == NEW_ORDERS_CHANNEL_ID:
        await ctx.channel.purge(limit=100)
        await ctx.send("🗑️ Канал нових замовлень повністю очищено!", delete_after=3)
    else:
        await ctx.send("Цю команду можна використовувати лише в каналі нових замовлень!", delete_after=3)

# --- 3. ОПИТУВАННЯ АКАУНТІВ ---
@tasks.loop(seconds=30)
async def olx_checker_task():
    global is_initialized
    async with ClientSession() as session:
        for acc_id, acc_data in ACCOUNTS.items():
            token = acc_data.get("access_token")
            if not token:
                continue

            headers = {
                "Authorization": f"Bearer {token}",
                "Version": "2.0"
            }

            try:
                async with session.get("https://www.olx.ua/api/partner/threads", headers=headers) as resp:
                    if resp.status != 200:
                        continue
                    threads_data = await resp.json()

                threads = threads_data.get("data", [])
                for thread in threads:
                    thread_id = thread.get("id")
                    advert_id = thread.get("advert_id")

                    ad_title = "Оголошення OLX"
                    ad_image_url = None
                    ad_url = f"https://www.olx.ua/d/obyavlenie/-ID{advert_id}.html" if advert_id else "https://www.olx.ua/"

                    if advert_id:
                        if advert_id in advert_cache:
                            ad_title, ad_image_url, ad_url = advert_cache[advert_id]
                        else:
                            async with session.get(f"https://www.olx.ua/api/partner/adverts/{advert_id}", headers=headers) as ad_resp:
                                if ad_resp.status == 200:
                                    ad_json = await ad_resp.json()
                                    ad_data = ad_json.get("data", {})
                                    
                                    ad_title = ad_data.get("title", "Оголошення OLX")
                                    api_url = ad_data.get("url")
                                    if api_url:
                                        ad_url = api_url
                                    
                                    images = ad_data.get("images", [])
                                    if images and isinstance(images, list):
                                        first_img = images[0]
                                        if isinstance(first_img, dict):
                                            ad_image_url = first_img.get("url")
                                    
                                    advert_cache[advert_id] = (ad_title, ad_image_url, ad_url)

                    async with session.get(f"https://www.olx.ua/api/partner/threads/{thread_id}/messages", headers=headers) as msg_resp:
                        if msg_resp.status != 200:
                            continue
                        messages_data = await msg_resp.json()

                    messages = messages_data.get("data", [])
                    if not messages:
                        continue

                    last_msg = messages[-1]
                    msg_id = last_msg.get("id")
                    msg_type = last_msg.get("type", "")
                    msg_time = last_msg.get("created_at", "Невідомо")

                    if not is_initialized:
                        processed_message_ids.add(msg_id)
                        continue

                    if msg_type != "received":
                        processed_message_ids.add(msg_id)
                        continue

                    if msg_id in processed_message_ids:
                        continue

                    processed_message_ids.add(msg_id)
                    msg_text = last_msg.get("text", "")

                    triggers = [
                        "замов", "оплат", "відправ", "наявн", "ціна",
                        "картк", "реквізит", "наложк", "післяплат",
                        "пошт", "доставк", "актуальн", "знижк"
                    ]
                    found_trigger = next((w for w in triggers if w in msg_text.lower()), None)

                    new_channel = bot.get_channel(NEW_ORDERS_CHANNEL_ID)
                    if new_channel:
                        embed = discord.Embed(
                            title=f"📦 {ad_title}",
                            url=ad_url,
                            description="Отримано нове вхідне звернення від клієнта.",
                            color=0x00FF00 if found_trigger else 0x3498db,
                            timestamp=datetime.now(timezone.utc)
                        )
                        
                        account_info = f"**{acc_data['name']}**\n📧 `{acc_data['email']}`"
                        embed.add_field(name="🏪 Ваш акаунт", value=account_info, inline=False)
                        
                        embed.add_field(name="⏱️ Час на OLX", value=msg_time, inline=True)

                        if ad_image_url:
                            embed.set_image(url=ad_image_url)

                        if found_trigger:
                            embed.add_field(name="⚠️ Увага (Тригер)", value=f"Спрацювало на: *{found_trigger}*", inline=False)
                        
                        embed.add_field(name="👤 Написав клієнт", value=f"> {msg_text}", inline=False)
                            
                        embed.set_footer(text="OLX Manager Bot")
                        await new_channel.send(embed=embed)

            except Exception as e:
                print(f"Помилка опитування для {acc_data['name']}: {e}", flush=True)

        if not is_initialized:
            is_initialized = True
            print("Бот ініціалізований та готовий до роботи.", flush=True)

# --- 4. АВТО-АРХІВАЦІЯ ТА ОЧИЩЕННЯ ---
@tasks.loop(minutes=5)
async def auto_archive_task():
    new_channel = bot.get_channel(NEW_ORDERS_CHANNEL_ID)
    archive_channel = bot.get_channel(ARCHIVE_CHANNEL_ID)
    if not new_channel or not archive_channel:
        return

    cutoff_archive = datetime.now(timezone.utc) - timedelta(minutes=20)
    cutoff_delete = datetime.now(timezone.utc) - timedelta(hours=5)

    async for message in new_channel.history(limit=100):
        if message.author == bot.user and message.created_at < cutoff_archive:
            if message.embeds:
                await archive_channel.send(
                    content="📦 *Повідомлення перенесено в архів (минуло 20 хвилин)*",
                    embed=message.embeds[0]
                )
            await message.delete()

    async for message in archive_channel.history(limit=100):
        if message.author == bot.user and message.created_at < cutoff_delete:
            await message.delete()

# --- 5. ЗАПУСК ---
@bot.event
async def on_ready():
    print(f'Бот {bot.user} активний!', flush=True)
    
    await restore_sessions_from_drive()
    
    bot.loop.create_task(start_web_server())
    if not auto_archive_task.is_running():
        auto_archive_task.start()
    if not olx_checker_task.is_running():
        olx_checker_task.start()

bot.run(os.getenv('DISOURCE_TOKEN') or os.getenv('DISCORD_TOKEN'))
