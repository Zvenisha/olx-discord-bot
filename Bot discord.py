import discord
from discord.ext import commands
import datetime
import asyncio
from aiohttp import web

# --- ЧАСТИНА 1: МІНІ-ВЕБ-СЕРВЕР ДЛЯ RENDER ---
# Це потрібно для того, щоб безкоштовний тариф Render бачив "сайт" і не вимикав бота
async def handle(request):
    return web.Response(text="OLX Discord Bot is running online!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    # Render передає порт через системні змінні, або використовуємо 8080 за замовчуванням
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    print("Веб-сервер-обманка успішно запущено на порту 8080!")

# --- ЧАСТИНА 2: ДИСКОРД БОТ ---
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'Бот {bot.user} успішно запущений і готовий до роботи!')
    # Запускаємо веб-сервер паралельно з ботом
    bot.loop.create_task(start_web_server())

@bot.command()
async def order(ctx):
    # --- ІМІТАЦІЯ ДАНИХ ВІД OLX ---
    olx_account_name = "Дропшипінг - Основний"
    product_title = "Настільна гра Манчкін"
    buyer_name = "Марія"
    message_text = "Добрий день! Я оформила олх доставку, відправте сьогодні, будь ласка."
    auto_reply_text = "Вітаю! Дякуємо за замовлення. Відправка сьогодні о 16:00."
    
    # ЛОГІКА ТРИГЕРІВ
    is_first_message = False 
    triggers = [
        "замов", "оплат", "відправ", "наявн", "ціна", 
        "картк", "реквізит", "наложк", "післяплат",
        "пошт", "доставк", "актуальн", "знижк"
    ]
    
    found_trigger = None
    for word in triggers:
        if word in message_text.lower():
            found_trigger = word
            break
            
    if not is_first_message and not found_trigger:
        await ctx.send("Повідомлення проігноровано (не перше і без тригерів).")
        return

    # СТВОРЕННЯ КАРТКИ ДЛЯ ДИСКОРДУ
    embed = discord.Embed(
        title="Нове повідомлення на OLX! 📩",
        url="https://olx.ua/",
        description=f"Оголошення: **[{product_title}](https://olx.ua/)**",
        color=0x00FF00 if found_trigger else 0x3498db,
        timestamp=datetime.datetime.utcnow()
    )
    
    embed.set_author(name=f"👤 {buyer_name}", icon_url="https://cdn.icon-icons.com/icons2/1378/PNG/512/avatardefault_92824.png")
    embed.add_field(name="🏪 Ваш акаунт", value=f"**{olx_account_name}**", inline=False)
    
    if found_trigger:
        embed.add_field(name="⚠️ Увага (Тригер)", value=f"Спрацювало на: *{found_trigger}*", inline=False)
        
    embed.add_field(name="💬 Клієнт пише", value=f"> {message_text}", inline=False)
    embed.add_field(name="🤖 Автовідповідач відповів", value=f"> {auto_reply_text}", inline=False)
    
    embed.set_thumbnail(url="https://logowik.com/content/uploads/images/olx-new-20224097.jpg")
    embed.set_footer(text="OLX Manager Bot", icon_url="https://cdn.icon-icons.com/icons2/2351/PNG/512/logo_discord_bot_icon_143196.png")

    await ctx.send(embed=embed)
import os
bot.run(os.getenv('DISCORD_TOKEN'))