import os
import asyncio
from datetime import datetime, timezone, timedelta
from aiohttp import web, ClientSession
import discord
from discord.ext import commands, tasks

# --- НАЛАШТУВАННЯ КАНАЛІВ DISCORD ---
NEW_ORDERS_CHANNEL_ID = 1546490061854351422
ARCHIVE_CHANNEL_ID = 1546603811643334757

# Дані OLX
OLX_CLIENT_ID = os.getenv("OLX_CLIENT_ID", "203013")
OLX_CLIENT_SECRET = os.getenv("OLX_CLIENT_SECRET", "SPqxrdxWxZBErpr4BKC31TudsW2Zhp2yNTqnkriF6OSTEXaR")
REDIRECT_URI = "https://olx-discord-bot-ppys.onrender.com/callback"

# Токени доступу OLX
olx_tokens = {
    "access_token": None,
    "refresh_token": None
}
processed_message_ids = set()

# --- 1. ВЕБ-СЕРВЕР (UPTIMEROBOT ТА АВТОРИЗАЦІЯ OAUTH) ---
async def handle_ping(request):
    return web.Response(text="OLX Discord Bot is online!")

async def handle_auth(request):
    auth_url = (
        f"https://www.olx.ua/oauth/authorize/?"
        f"client_id={OLX_CLIENT_ID}&response_type=code&"
        f"scope=read+write+v2&redirect_uri={REDIRECT_URI}"
    )
    return web.HTTPFound(auth_url)

async def handle_callback(request):
    code = request.query.get("code")
    if not code:
        return web.Response(text="Помилка: код авторизації не отримано.", status=400)

    # Обмін коду на Access Token
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
            if "access_token" in data:
                olx_tokens["access_token"] = data["access_token"]
                olx_tokens["refresh_token"] = data.get("refresh_token")
                return web.Response(text="✅ Авторизація OLX успішна! Бот підключений і збирає повідомлення.")
            else:
                return web.Response(text=f"Помилка отримання токена: {data}", status=400)

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

# --- 2. БОТ DISCORD ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# --- 3. ФОНОВИЙ СКАНЕР OLX (ПОШУК НОВИХ ПОВІДОМЛЕНЬ) ---
@tasks.loop(seconds=30)
async def olx_checker_task():
    token = olx_tokens.get("access_token")
    if not token:
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Version": "2.0"
    }

    async with ClientSession() as session:
        try:
            # Отримуємо список діалогів
            async with session.get("https://www.olx.ua/api/partner/threads", headers=headers) as resp:
                if resp.status != 200:
                    return
                threads_data = await resp.json()

            threads = threads_data.get("data", [])
            for thread in threads:
                thread_id = thread.get("id")
                # Запитуємо повідомлення в діалозі
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

                # Фільтр: ігноруємо системні замовлення OLX Доставка без тексту
                if msg_type in ["order", "delivery_order", "system"]:
                    continue

                # Якщо повідомлення вже обробляли або воно від нас самих
                if msg_id in processed_message_ids or last_msg.get("is_author"):
                    continue

                processed_message_ids.add(msg_id)
                msg_text = last_msg.get("text", "")
                
                # Аналіз тригерів
                triggers = [
                    "замов", "оплат", "відправ", "наявн", "ціна",
                    "картк", "реквізит", "наложк", "післяплат",
                    "пошт", "доставк", "актуальн", "знижк"
                ]
                found_trigger = next((word for word in triggers if word in msg_text.lower()), None)
                auto_reply_text = "Вітаю! Дякуємо за замовлення. Відправка сьогодні о 16:00."

                # Автоматична відповідь в OLX
                if found_trigger:
                    reply_payload = {"text": auto_reply_text}
                    await session.post(
                        f"https://www.olx.ua/api/partner/threads/{thread_id}/messages",
                        headers=headers,
                        json=reply_payload
                    )

                # Відправка картки в Discord
                new_channel = bot.get_channel(NEW_ORDERS_CHANNEL_ID)
                if new_channel:
                    embed = discord.Embed(
                        title="Нове повідомлення на OLX! 📩",
                        url=f"https://www.olx.ua/my/chat/",
                        description="Отримано нове вхідне звернення від клієнта.",
                        color=0x00FF00 if found_trigger else 0x3498db,
                        timestamp=datetime.now(timezone.utc)
                    )
                    if found_trigger:
                        embed.add_field(name="⚠️ Увага (Тригер)", value=f"Спрацювало на: *{found_trigger}*", inline=False)
                    embed.add_field(name="💬 Клієнт пише", value=f"> {msg_text}", inline=False)
                    if found_trigger:
                        embed.add_field(name="🤖 Автовідповідач надіслав", value=f"> {auto_reply_text}", inline=False)
                    embed.set_footer(text="OLX Manager Bot")
                    await new_channel.send(embed=embed)

        except Exception as e:
            print(f"Помилка під час опитування OLX API: {e}")

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

# --- 5. ЗАПУСК БОТА ---
@bot.event
async def on_ready():
    print(f'Бот {bot.user} активний!')
    bot.loop.create_task(start_web_server())
    if not auto_archive_task.is_running():
        auto_archive_task.start()
    if not olx_checker_task.is_running():
        olx_checker_task.start()

bot.run(os.getenv('DISCORD_TOKEN'))
