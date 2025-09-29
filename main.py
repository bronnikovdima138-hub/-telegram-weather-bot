import logging
import datetime as dt
import pytz
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from aiohttp import web

from config import TELEGRAM_BOT_TOKEN, WEBHOOK_URL, PORT
from coord_parser import parse_coordinates
from geocode import reverse_geocode
from weather import fetch_surface, fetch_winds_aloft, derive_winds_profile, slice_intervals, format_report

logging.basicConfig(
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("tg-weather-bot")

def _coords_to_dms(lat: float, lon: float) -> str:
    def conv(x: float, latlon: str) -> str:
        sign = 1 if x >= 0 else -1
        x = abs(x)
        deg = int(x)
        m_float = (x - deg) * 60
        minute = int(m_float)
        sec = int(round((m_float - minute) * 60))
        if sec == 60:
            sec = 0
            minute += 1
        if minute == 60:
            minute = 0
            deg += 1
        if latlon == 'lat':
            suffix = 'с. ш.' if sign > 0 else 'ю. ш.'
        else:
            suffix = 'в. д.' if sign > 0 else 'з. д.'
        return f"{deg}°{minute}'{suffix}"
    return f"{conv(lat, 'lat')}, {conv(lon, 'lon')}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Привет! Пришли координаты в формате:\n"
        "Широта: 47°41'с. ш. / Долгота: 36°49'в. д. / Высота: 119 m\n"
        "Часовой пояс: Europe/Kyiv (UTC+3)\n\n"
        "Я пришлю сводку по погоде с интервалами, оформленную столбиком."
    )
    if update.message:
        await update.message.reply_text(text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    raw = update.message.text.strip()
    
    try:
        lat, lon, alt, tz = parse_coordinates(raw)
    except Exception:
        await update.message.reply_text(
            "Не удалось разобрать координаты. Пришлите, пожалуйста, строку как в примере с широтой/долготой."
        )
        return
    
    def normalize_tz(s: str) -> str:
        if not s:
            return "auto"
        s = s.strip()
        # Extract IANA zone if provided with extra text like "Europe/Kyiv (UTC+3)"
        import re
        m = re.search(r"([A-Za-z_]+\/[A-Za-z_+-]+)", s)
        if m:
            zone = m.group(1)
        else:
            zone = s.split()[0]
        if zone == "Europe/Kiev":
            zone = "Europe/Kyiv"
        return zone or "auto"
    
    timezone = normalize_tz(tz) if tz else "auto"
    
    try:
        if timezone and timezone != "auto":
            now_local = dt.datetime.now(pytz.timezone(timezone))
        else:
            now_local = dt.datetime.now(dt.timezone.utc).astimezone()
    except Exception:
        now_local = dt.datetime.now()
    date_local = now_local.date()
    
    try:
        surface = fetch_surface(lat, lon, timezone, date_local)
        winds_all = fetch_winds_aloft(lat, lon, timezone, date_local)
        times = surface.get("hourly", {}).get("time", [])
        winds_profile = derive_winds_profile(
            times, winds_all.get("gfs_seamless", {}), winds_all.get("icon_seamless", {})
        )
        intervals = slice_intervals(surface, winds_profile)
    except Exception:
        logger.exception("fetch/compute failed")
        await update.message.reply_text("Не удалось получить данные погоды. Попробуйте ещё раз чуть позже.")
        return
    
    _, place_short = reverse_geocode(lat, lon)
    coords_text = _coords_to_dms(lat, lon)
    place_text = place_short or "неизвестно"
    
    report = format_report(date_local, coords_text, place_text, intervals)
    await update.message.reply_text(report)

async def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        print("Ошибка: отсутствует TELEGRAM_BOT_TOKEN. Добавьте его в .env и перезапустите.")
        return
    
    # Нормализуем URL и логируем параметры окружения
    url = (WEBHOOK_URL or "").strip()
    logger.info("Env check: WEBHOOK_URL=%r, PORT=%s", url, PORT)
    
    if url:
        # ПРОСТЕЙШИЙ ПОДХОД: Запускаем стандартный HTTP сервер первым
        logger.info("🚀 Starting SIMPLE HTTP server first...")
        
        # Создаем web application с минимальными endpoints
        web_app = web.Application()
        
        async def health_check(request):
            return web.Response(text="OK", status=200, headers={"Content-Type": "text/plain"})
        
        async def root_handler(request):
            return web.Response(text="Telegram Weather Bot is running", status=200, headers={"Content-Type": "text/plain"})
        
        web_app.router.add_get("/", root_handler)
        web_app.router.add_get("/healthz", health_check)
        web_app.router.add_get("/health", health_check)
        
        # Запускаем сервер СРАЗУ
        runner = web.AppRunner(web_app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        
        logger.info("✅ SIMPLE HTTP SERVER STARTED AND LISTENING!")
        logger.info(f"✅ Server running on 0.0.0.0:{PORT}")
        logger.info("✅ Render should detect open port now")
        
        # Теперь настраиваем Telegram
        try:
            app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
            app.add_handler(CommandHandler("start", start))
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
            
            await app.initialize()
            await app.start()
            
            base = url.rstrip('/')
            hook_url = f"{base}/{TELEGRAM_BOT_TOKEN}"
            
            # Устанавливаем webhook
            await app.bot.set_webhook(hook_url)
            logger.info(f"✅ Webhook set to: {hook_url}")
            
            # Добавляем webhook endpoint ПОСЛЕ того как сервер уже работает
            async def webhook_handler(request):
                try:
                    update_data = await request.json()
                    from telegram import Update
                    update = Update.de_json(update_data, app.bot)
                    await app.process_update(update)
                    return web.Response(text="OK", status=200)
                except Exception as e:
                    logger.error(f"Webhook error: {e}")
                    return web.Response(text="Error", status=500)
            
            web_app.router.add_post(f"/{TELEGRAM_BOT_TOKEN}", webhook_handler)
            
        except Exception as e:
            logger.error(f"Telegram setup failed: {e}")
        
        # Держим сервер запущенным
        logger.info("✅ ALL SYSTEMS GO - Server ready for traffic!")
        try:
            while True:
                import asyncio
                await asyncio.sleep(10)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            await runner.cleanup()
    else:
        logger.info("Starting in polling mode (no WEBHOOK_URL set or blank after strip)...")
        app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        await app.run_polling(close_loop=False)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
