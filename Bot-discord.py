import os
import asyncio
from datetime import datetime, timezone, timedelta
from aiohttp import web
import discord
from discord.ext import commands, tasks

# --- НАЛАШТУВАННЯ КАНАЛІВ ДИСКОРДУ ---
NEW_ORDERS_CHANNEL_ID = 1546490061854351422  # Канал "Нові повідомлення"
ARCHIVE_CHANNEL_ID = 1546603811643334757     # Канал "Архів"

# --- 1. ВЕБ-СЕРВЕР ДЛЯ RENDER (ЩОБ БОТ НЕ ЗАСИНАВ) ---
async def handle(request):
    return web.Response(text="OLX Discord Bot is online!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    print("Веб-сервер успішно запущено на порту 8080.")

# --- 2. НАЛАШТУВАННЯ БОТА ---
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

# --- 3. ФОНОВИЙ СКАНЕР АВТО-АРХІВАЦІЇ (ПЕРЕВІРКА КОЖНУ ГОДИНУ) ---
@tasks.loop(hours=1)
async def auto_archive_task():
    new_channel = bot.get_channel(NEW_ORDERS_CHANNEL_ID)
    archive_channel = bot.get_channel(ARCHIVE_CHANNEL_ID)

    if not new_channel or not archive_channel:
        return

    # Часова межа: рівно 24 години тому
    cutoff_time = datetime.now(timezone.utc) - timedelta(days=1)

    # Скануємо останні 100 повідомлень у каналі нових повідомлень
    async for message in new_channel.history(limit=100):
        # Переносимо тільки власні повідомлення бота, яким більше доби
        if message.author == bot.user and message.created_at < cutoff_time:
            if message.embeds:
                await archive_channel.send(
                    content="📦 *Повідомлення перенесено в архів (минуло понад 24 години)*",
                    embed=message.embeds[0]
                )
            # Видаляємо повідомлення з каналу нових
            await message.delete()

# --- 4. ПОДІЇ ТА КОМАНДИ ---
@bot.event
async def on_ready():
    print(f'Бот {bot.user} успішно увійшов у мережу!')
    bot.loop.create_task(start_web_server())
    if not auto_archive_task.is_running():
        auto_archive_task.start()

@bot.command()
async def order(ctx):
    # Завжди відправляємо повідомлення в канал "Нові повідомлення"
    target_channel = bot.get_channel(NEW_ORDERS_CHANNEL_ID) or ctx.channel

    olx_account_name = "Дропшипінг - Основний"
    product_title = "Настільна гра Манчкін"
    buyer_name = "Марія"
    message_text = "Добрий день! Я оформила олх доставку, відправте сьогодні, будь ласка."
    auto_reply_text = "Вітаю! Дякуємо за замовлення. Відправка сьогодні о 16:00."
    
    triggers = [
        "замов", "оплат", "відправ", "наявн", "ціна", 
        "картк", "реквізит", "наложк", "післяплат",
        "пошт", "доставк", "актуальн", "знижк"
    ]
    
    found_trigger = next((word for word in triggers if word in message_text.lower()), None)

    embed = discord.Embed(
        title="Нове повідомлення на OLX! 📩",
        url="https://olx.ua/",
        description=f"Оголошення: **[{product_title}](https://olx.ua/)**",
        color=0x00FF00 if found_trigger else 0x3498db,
        timestamp=datetime.now(timezone.utc)
    )
    
    embed.set_author(name=f"👤 {buyer_name}", icon_url="https://cdn.icon-icons.com/icons2/1378/PNG/512/avatardefault_92824.png")
    embed.add_field(name="🏪 Ваш акаунт", value=f"**{olx_account_name}**", inline=False)
    
    if found_trigger:
        embed.add_field(name="⚠️ Увага (Тригер)", value=f"Спрацювало на: *{found_trigger}*", inline=False)
        
    embed.add_field(name="💬 Клієнт пише", value=f"> {message_text}", inline=False)
    embed.add_field(name="🤖 Автовідповідач відповів", value=f"> {auto_reply_text}", inline=False)
    
    embed.set_thumbnail(url="https://logowik.com/content/uploads/images/olx-new-20224097.jpg")
    embed.set_footer(text="OLX Manager Bot", icon_url="https://cdn.icon-icons.com/icons2/2351/PNG/512/logo_discord_bot_icon_143196.png")

    await target_channel.send(embed=embed)

# --- 5. ЗАПУСК БОТА ---
bot.run(os.getenv('DISCORD_TOKEN'))
