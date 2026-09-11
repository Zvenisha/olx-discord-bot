import os
import asyncio
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

ACCOUNTS = {
    "1": {"name": "Магазин 1 (Основний)", "access_token": None, "refresh_token": None, "user_id": None},
    "2": {"name": "Магазин 2", "access_token": None, "refresh_token": None, "user_id": None},
    "3": {"name": "Магазин 3", "access_token": None, "refresh_token": None, "user_id": None}
}

processed_message_ids = set()
is_initialized = False

# --- 1. ВЕБ-СЕРВЕР ТА АВТОРИЗАЦІЯ ---
async def handle_ping(request):
    status_lines = [f"• {acc['name']}: {'🟢 Підключено' if acc['access_token'] else '⚪ Очікує входу'}" for acc in ACCOUNTS.values()]
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
                        user_json = await user_resp.json()
                        ACCOUNTS[acc_id]["user_id"] = user_json.get("data", {}).get("id")

                acc_name = ACCOUNTS[acc_id]["name"]
                return web.Response(text=f"✅ Успішно! {acc_name} підключено до бота.")
            else:
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
    print("Веб-сервер активний на порту 8080.")

# --- 2. DISCORD БОТ ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

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
                if not acc_data.get("user_id"):
                    async with session.get("https://www.olx.ua/api/partner/users/me", headers=headers) as user_resp:
                        if user_resp.status == 200:
                            user_json = await user_resp.json()
                            acc_data["user_id"] = user_json.get("data", {}).get("id")

                my_user_id = acc_data.get("user_id")

                async with session.get("https://www.olx.ua/api/partner/threads", headers=headers) as resp:
                    if resp.status != 200:
                        continue
                    threads_data = await resp.json()

                threads = threads_data.get("data", [])
                for thread in threads:
                    thread_id = thread.get("id")

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
                    
                    # Логування структури для аналізу в Render
                    print(f"OLX MESSAGE DATA: {last_msg}")

                    if not is_initialized:
                        processed_message_ids.add(msg_id)
                        continue

                    if msg_type in ["order", "delivery_order", "system"]:
                        processed_message_ids.add(msg_id)
                        continue

                    is_author = last_msg.get("is_author", False)
                    sender_id = last_msg.get("user_id") or last_msg.get("sender_id")
                    direction = last_msg.get("direction", "")

                    is_mine = (
                        is_author or 
                        (my_user_id and sender_id and str(sender_id) == str(my_user_id)) or
                        direction in ["out", "outgoing", "sent"]
                    )

                    if is_mine:
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
                            title="Нове повідомлення на OLX! 📩",
                            url="https://www.olx.ua/my/chat/",
                            description="Отримано нове вхідне звернення від клієнта.",
                            color=0x00FF00 if found_trigger else 0x3498db,
                            timestamp=datetime.now(timezone.utc)
                        )
                        embed.add_field(name="🏪 Ваш акаунт", value=f"**{acc_data['name']}**", inline=False)
                        
                        if found_trigger:
                            embed.add_field(name="⚠️ Увага (Тригер)", value=f"Спрацювало на: *{found_trigger}*", inline=False)
                        
                        embed.add_field(name="👤 Написав клієнт", value=f"> {msg_text}", inline=False)
                            
                        embed.set_footer(text="OLX Manager Bot")
                        await new_channel.send(embed=embed)

            except Exception as e:
                print(f"Помилка опитування для {acc_data['name']}: {e}")

        if not is_initialized:
            is_initialized = True
            print("Бот ініціалізований.")

# --- 4. АВТО-АРХІВАЦІЯ (ЧЕРЕЗ 2 ГОДИНИ) ---
@tasks.loop(hours=1)
async def auto_archive_task():
    new_channel = bot.get_channel(NEW_ORDERS_CHANNEL_ID)
    archive_channel = bot.get_channel(ARCHIVE_CHANNEL_ID)
    if not new_channel or not archive_channel:
        return

    # Змінено з 24 годин на 2 години
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=2)
    async for message in new_channel.history(limit=100):
        if message.author == bot.user and message.created_at < cutoff_time:
            if message.embeds:
                await archive_channel.send(
                    content="📦 *Повідомлення перенесено в архів (минуло понад 2 години)*",
                    embed=message.embeds[0]
                )
            await message.delete()

# --- 5. ЗАПУСК ---
@bot.event
async def on_ready():
    print(f'Бот {bot.user} активний!')
    bot.loop.create_task(start_web_server())
    if not auto_archive_task.is_running():
        auto_archive_task.start()
    if not olx_checker_task.is_running():
        olx_checker_task.start()

bot.run(os.getenv('DISOURCE_TOKEN') or os.getenv('DISCORD_TOKEN'))
