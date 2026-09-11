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
    "1": {"name": "Магазин 1 (Основний)", "access_token": None, "refresh_token": None},
    "2": {"name": "Магазин 2", "access_token": None, "refresh_token": None},
    "3": {"name": "Магазин 3", "access_token": None, "refresh_token": None}
}

# Зберігаємо ID вже оброблених повідомлень, щоб не дублювати сповіщення
processed_message_ids = set()

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
                ACCOUNTS[acc_id]["access_token"] = data["access_token"]
                ACCOUNTS[acc_id]["refresh_token"] = data.get("refresh_token")
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

# --- 3. ОПИТУВАННЯ АКАУНТІВ (БЕЗ АВТОВІДПОВІДАЧА) ---
@tasks.loop(seconds=30)
async def olx_checker_task():
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
                    is_author = last_msg.get("is_author", False)

                    # Ігноруємо системні повідомлення доставки
                    if msg_type in ["order", "delivery_order", "system"]:
                        continue

                    # Якщо це повідомлення вже обробляли або його написав ти (is_author=True) — ігноруємо
                    if msg_id in processed_message_ids or is_author:
                        continue

                    # Запам'ятовуємо ID, щоб більше ніколи його не чіпати
                    processed_message_ids.add(msg_id)
                    msg_text = last_msg.get("text", "")

                    # Пошук тригерів (тільки для підсвічування в Discord)
                    triggers = [
                        "замов", "оплат", "відправ", "наявн", "ціна",
                        "картк", "реквізит", "наложк", "післяплат",
                        "пошт", "доставк", "актуальн", "знижк"
                    ]
                    found_trigger = next((w for w in triggers if w in msg_text.lower()), None)

                    # Надсилаємо сповіщення в Discord (жодних запитів на відправку повідомлень в OLX немає)
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

# --- 4. АВТО-АРХІВАЦІЯ (РАЗ НА ГОДИНУ) ---
@tasks.loop(hours=1)
async def auto_archive_task():
    new_channel = bot.get_channel(NEW_ORDERS_CHANNEL_ID)
    archive_channel = bot.get_channel(ARCHIVE_CHANNEL_ID)
    if not new_channel or not archive_channel:
        return

    cutoff_time = datetime.now(timezone.utc) - timedelta(days=1)
    async for message in new_channel.history(limit=100):
        if message.author == bot.user and message.created_at < cutoff_time:
            if message.embeds:
                await archive_channel.send(
                    content="📦 *Повідомлення перенесено в архів (минуло понад 24 години)*",
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
