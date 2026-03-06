"""
Telegram-бот для Daulet Tennis Academy
- Каждый день в 07:00 автоматически бронирует ближайший вт/чт
- Сначала пробует 20:00, если занято — 21:00
- Уведомляет вас об успехе/ошибке + скриншот
- Напоминание за 2 часа до тренировки

Установка:
pip install python-telegram-bot==20.7 apscheduler==3.10.4 playwright==1.44.0
playwright install chromium
"""

import logging
import os
import asyncio
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from playwright.async_api import async_playwright
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)

# ─────────────────────────────────────────────
# НАСТРОЙКИ — задайте через Railway Variables
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "ВАШ_ТОКЕН")
ADMIN_CHAT_ID      = int(os.environ.get("ADMIN_CHAT_ID", "0"))  # ваш chat_id из @userinfobot
YOUR_NAME          = os.environ.get("YOUR_NAME", "Асия")
YOUR_PHONE         = os.environ.get("YOUR_PHONE", "+77777720466")

BOOKING_URL = "https://n551098.alteg.io/company/521176/personal/menu?o="

# Приоритетные слоты (пробуем по порядку)
PREFERRED_TIMES = ["20:00", "21:00"]

# Дни для бронирования: 1=вторник, 3=четверг
TARGET_WEEKDAYS = [1, 3]

# ─────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SELECT_DATE, SELECT_TIME, SELECT_COURT, CONFIRM = range(4)
bookings_store: list = []

TIME_SLOTS = [
    ["07:00", "08:00", "09:00"],
    ["10:00", "11:00", "12:00"],
    ["13:00", "14:00", "15:00"],
    ["16:00", "17:00", "18:00"],
    ["19:00", "20:00", "21:00"],
]
COURTS = ["Крытый корт", "Корт №1", "Корт №2", "Корт №3", "Любой корт"]

# Предпочитаемый корт для авто-бронирования
PREFERRED_COURT = "Крытый"


# ══════════════════════════════════════════════
# Вычисление следующего вт/чт
# ══════════════════════════════════════════════

def get_next_target_date() -> datetime:
    """Возвращает ближайший вторник или четверг начиная с сегодня."""
    now = datetime.now()
    for i in range(1, 8):
        candidate = now + timedelta(days=i)
        if candidate.weekday() in TARGET_WEEKDAYS:
            return candidate
    return now + timedelta(days=1)


# ══════════════════════════════════════════════
# Playwright — автоматическое бронирование
# ══════════════════════════════════════════════

async def playwright_book(date_str: str, time_str: str, name: str, phone: str) -> dict:
    """
    Открывает alteg.io и заполняет форму бронирования.
    date_str: ДД.ММ.ГГГГ, time_str: ЧЧ:ММ
    """
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            context = await browser.new_context(
                viewport={"width": 390, "height": 844},
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15"
            )
            page = await context.new_page()

            logger.info(f"📅 Открываю страницу бронирования {date_str} {time_str}...")
            await page.goto(BOOKING_URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)

            # Шаг 1 — Выбрать дату и время
            try:
                await page.click("text=Выбрать дату и время", timeout=8000)
                await page.wait_for_timeout(1500)
                logger.info("✅ Открыл выбор даты")
            except Exception as e:
                logger.warning(f"Кнопка даты: {e}")

            # Шаг 2 — Выбрать день в календаре
            try:
                date_obj = datetime.strptime(date_str, "%d.%m.%Y")
                day_num = str(date_obj.day)
                day_btn = page.locator(
                    f"[class*='calendar'] [class*='day']:not([class*='disabled']):not([class*='past']):has-text('{day_num}')"
                ).first
                await day_btn.click(timeout=8000)
                await page.wait_for_timeout(1500)
                logger.info(f"✅ Выбрал день {day_num}")
            except Exception as e:
                logger.warning(f"Выбор дня: {e}")

            # Шаг 3 — Выбрать время
            try:
                time_btn = page.locator(
                    f"[class*='time']:has-text('{time_str}'), button:has-text('{time_str}')"
                ).first
                is_visible = await time_btn.is_visible()
                if not is_visible:
                    await browser.close()
                    return {"success": False, "error": f"Время {time_str} недоступно"}
                await time_btn.click(timeout=8000)
                await page.wait_for_timeout(1500)
                logger.info(f"✅ Выбрал время {time_str}")
            except Exception as e:
                await browser.close()
                return {"success": False, "error": f"Время {time_str} недоступно: {e}"}

            # Шаг 4 — Выбрать корт (крытый)
            try:
                await page.click("text=Выбрать корт", timeout=5000)
                await page.wait_for_timeout(1000)
                # Ищем крытый корт по тексту
                court_btn = page.locator(
                    "[class*='staff']:has-text('Крытый'), [class*='court']:has-text('Крытый'), li:has-text('Крытый')"
                ).first
                await court_btn.click(timeout=5000)
                await page.wait_for_timeout(1000)
                logger.info("✅ Выбрал крытый корт")
            except Exception as e:
                logger.warning(f"Выбор корта: {e}")

            # Шаг 4б — Выбрать услугу если нужно
            try:
                await page.click("text=Выбрать услуги", timeout=3000)
                await page.wait_for_timeout(1000)
                first_service = page.locator("[class*='service']:not([class*='disabled'])").first
                await first_service.click(timeout=5000)
                await page.wait_for_timeout(1000)
                logger.info("✅ Выбрал услугу")
            except Exception:
                pass

            # Шаг 5 — Имя
            try:
                name_input = page.locator(
                    "input[name='name'], input[placeholder*='имя'], input[placeholder*='Имя'], input[placeholder*='ФИО']"
                ).first
                await name_input.fill(name, timeout=5000)
                await page.wait_for_timeout(500)
                logger.info("✅ Ввёл имя")
            except Exception as e:
                logger.warning(f"Имя: {e}")

            # Шаг 6 — Телефон
            try:
                phone_input = page.locator(
                    "input[name='phone'], input[type='tel'], input[placeholder*='телефон'], input[placeholder*='Телефон']"
                ).first
                await phone_input.fill(phone, timeout=5000)
                await page.wait_for_timeout(500)
                logger.info("✅ Ввёл телефон")
            except Exception as e:
                logger.warning(f"Телефон: {e}")

            # Шаг 7 — Подтвердить
            try:
                confirm_btn = page.locator(
                    "button:has-text('Записаться'), button:has-text('Подтвердить'), button:has-text('Забронировать'), button[type='submit']"
                ).first
                await confirm_btn.click(timeout=8000)
                await page.wait_for_timeout(4000)
                logger.info("✅ Нажал подтвердить")
            except Exception as e:
                logger.warning(f"Кнопка подтверждения: {e}")

            # Проверяем результат
            page_text = await page.content()
            success_keywords = ["запись создана", "успешно", "подтверждена", "вы записаны", "success"]
            is_success = any(kw.lower() in page_text.lower() for kw in success_keywords)
            screenshot = await page.screenshot(type="png")

            await browser.close()

            return {
                "success": is_success,
                "screenshot": screenshot,
                "error": None if is_success else "Не удалось подтвердить — страница могла измениться"
            }

    except Exception as e:
        logger.error(f"Playwright error: {e}")
        return {"success": False, "error": str(e), "screenshot": None}


# ══════════════════════════════════════════════
# Авто-бронирование в 07:00
# ══════════════════════════════════════════════

async def auto_book(app: Application):
    """Запускается каждый день в 07:00. Бронирует ближайший вт/чт."""
    now = datetime.now()
    logger.info(f"🕖 Авто-бронирование запущено в {now.strftime('%d.%m.%Y %H:%M')}")

    target_date = get_next_target_date()
    date_str = target_date.strftime("%d.%m.%Y")
    weekday_name = ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"][target_date.weekday()]

    if ADMIN_CHAT_ID:
        await app.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"🤖 Начинаю автобронирование...\n📅 {weekday_name} {date_str}"
        )

    booked = False
    for time_str in PREFERRED_TIMES:
        logger.info(f"Пробую слот {time_str}...")
        result = await playwright_book(date_str, time_str, YOUR_NAME, YOUR_PHONE)

        if result["success"]:
            booked = True
            booking_dt = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
            bookings_store.append({
                "chat_id": ADMIN_CHAT_ID,
                "datetime": booking_dt,
                "court": "Корт",
                "date": date_str,
                "time": time_str,
                "reminded": False,
            })
            if ADMIN_CHAT_ID:
                await app.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=(
                        f"✅ *Запись создана автоматически!*\n\n"
                        f"📅 {weekday_name} {date_str}\n"
                        f"⏰ {time_str}\n\n"
                        f"🔔 Напомню за 2 часа."
                    ),
                    parse_mode="Markdown"
                )
                if result.get("screenshot"):
                    await app.bot.send_photo(
                        chat_id=ADMIN_CHAT_ID,
                        photo=result["screenshot"],
                        caption="📸 Скриншот подтверждения"
                    )
            break
        else:
            logger.warning(f"Слот {time_str} недоступен: {result.get('error')}")
            if ADMIN_CHAT_ID:
                await app.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"⚠️ Слот {time_str} недоступен, пробую {PREFERRED_TIMES[PREFERRED_TIMES.index(time_str)+1] if time_str != PREFERRED_TIMES[-1] else 'больше вариантов нет'}..."
                )

    if not booked and ADMIN_CHAT_ID:
        await app.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=(
                f"❌ *Не удалось забронировать*\n\n"
                f"📅 {weekday_name} {date_str}\n"
                f"Слоты 20:00 и 21:00 недоступны.\n\n"
                f"Забронируйте вручную:\n{BOOKING_URL}"
            ),
            parse_mode="Markdown"
        )


# ══════════════════════════════════════════════
# Ручное бронирование
# ══════════════════════════════════════════════

def _weekday(dt: datetime) -> str:
    return ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"][dt.weekday()]


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎾 *Daulet Tennis Academy*\n\n"
        "Привет! Я автоматически бронирую корт каждый день в 07:00.\n\n"
        "📌 Команды:\n"
        "/status — статус авто-бронирования\n"
        "/book — забронировать вручную\n"
        "/mybookings — мои записи\n"
        "/cancel — отменить",
        parse_mode="Markdown"
    )


async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    target = get_next_target_date()
    weekday_name = _weekday(target)
    now = datetime.now()
    await update.message.reply_text(
        f"🤖 *Авто-бронирование активно*\n\n"
        f"⏰ Каждый день в *07:00*\n"
        f"📅 Следующая цель: *{weekday_name} {target.strftime('%d.%m.%Y')}*\n"
        f"🎯 Слоты: 20:00 → 21:00\n\n"
        f"🕐 Сейчас: {now.strftime('%d.%m.%Y %H:%M')}",
        parse_mode="Markdown"
    )


async def book_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    days_map = {}
    keyboard = []
    row = []
    for i in range(7):
        day = datetime.now() + timedelta(days=i)
        label = day.strftime("%d.%m")
        if i == 0: label += " (сегодня)"
        elif i == 1: label += " (завтра)"
        else: label += f" ({_weekday(day)})"
        days_map[label] = day.strftime("%d.%m.%Y")
        row.append(label)
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    ctx.user_data["days_map"] = days_map
    await update.message.reply_text(
        "📅 Выберите дату или введите вручную (*ДД.ММ.ГГГГ*):",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return SELECT_DATE


async def select_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    days_map = ctx.user_data.get("days_map", {})
    if text in days_map:
        date_str = days_map[text]
    else:
        try:
            date_obj = datetime.strptime(text, "%d.%m.%Y")
            if date_obj.date() < datetime.today().date():
                await update.message.reply_text("❌ Дата в прошлом. Введите будущую дату:")
                return SELECT_DATE
            date_str = text
        except ValueError:
            await update.message.reply_text("❌ Неверный формат. Введите как *ДД.ММ.ГГГГ*:", parse_mode="Markdown")
            return SELECT_DATE

    ctx.user_data["date"] = date_str
    await update.message.reply_text(
        f"⏰ Выберите время на *{date_str}*:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(TIME_SLOTS, one_time_keyboard=True, resize_keyboard=True)
    )
    return SELECT_TIME


async def select_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        datetime.strptime(text, "%H:%M")
    except ValueError:
        await update.message.reply_text("❌ Неверный формат. Введите как *ЧЧ:ММ*:", parse_mode="Markdown")
        return SELECT_TIME
    ctx.user_data["time"] = text
    keyboard = [[c] for c in COURTS]
    await update.message.reply_text(
        "🏟 Выберите корт:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return SELECT_COURT


async def select_court(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["court"] = update.message.text.strip()
    keyboard = [["✅ Подтвердить", "❌ Отменить"]]
    await update.message.reply_text(
        f"📋 *Подтвердите бронирование:*\n\n"
        f"🏟 {ctx.user_data['court']}\n"
        f"📅 {ctx.user_data['date']}\n"
        f"⏰ {ctx.user_data['time']}",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return CONFIRM


async def confirm_booking(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Отменить":
        await update.message.reply_text("Отменено.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    date = ctx.user_data["date"]
    time = ctx.user_data["time"]
    court = ctx.user_data["court"]

    await update.message.reply_text("⏳ Бронирую, подождите...", reply_markup=ReplyKeyboardRemove())
    result = await playwright_book(date, time, YOUR_NAME, YOUR_PHONE)

    if result["success"]:
        try:
            booking_dt = datetime.strptime(f"{date} {time}", "%d.%m.%Y %H:%M")
            bookings_store.append({
                "chat_id": update.effective_chat.id,
                "datetime": booking_dt,
                "court": court,
                "date": date,
                "time": time,
                "reminded": False,
            })
        except Exception as e:
            logger.error(e)

        await update.message.reply_text(
            f"✅ *Запись создана!*\n\n🏟 {court}\n📅 {date} в {time}\n\n🔔 Напомню за 2 часа.",
            parse_mode="Markdown"
        )
        if result.get("screenshot"):
            await update.message.reply_photo(photo=result["screenshot"], caption="📸 Скриншот подтверждения")
    else:
        await update.message.reply_text(
            f"❌ Не удалось создать запись.\n{result.get('error', '')}\n\nВручную: {BOOKING_URL}"
        )
    return ConversationHandler.END


async def my_bookings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    chat_id = update.effective_chat.id
    user_bookings = [b for b in bookings_store if b["chat_id"] == chat_id and b["datetime"] > now]
    if not user_bookings:
        await update.message.reply_text("У вас нет предстоящих записей.")
        return
    text = "📋 *Предстоящие записи:*\n\n"
    for i, b in enumerate(user_bookings, 1):
        text += f"{i}. 🏟 {b['court']} — {b['date']} в {b['time']}\n"
    await update.message.reply_text(text, parse_mode="Markdown")


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ══════════════════════════════════════════════
# Напоминания за 2 часа
# ══════════════════════════════════════════════

async def send_reminders(app: Application):
    now = datetime.now()
    for b in bookings_store:
        if b["reminded"]:
            continue
        delta = b["datetime"] - now
        if timedelta(hours=1, minutes=55) <= delta <= timedelta(hours=2, minutes=5):
            try:
                await app.bot.send_message(
                    chat_id=b["chat_id"],
                    text=(
                        f"🔔 *Напоминание!*\n\n"
                        f"Через 2 часа тренировка:\n"
                        f"🏟 {b['court']}\n"
                        f"📅 {b['date']} в {b['time']}\n\n"
                        f"Не забудьте! 🎾"
                    ),
                    parse_mode="Markdown"
                )
                b["reminded"] = True
            except Exception as e:
                logger.error(f"Reminder error: {e}")


# ══════════════════════════════════════════════
# Запуск
# ══════════════════════════════════════════════

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("book", book_start)],
        states={
            SELECT_DATE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, select_date)],
            SELECT_TIME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, select_time)],
            SELECT_COURT: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_court)],
            CONFIRM:      [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_booking)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("mybookings", my_bookings))
    app.add_handler(conv)

    scheduler = AsyncIOScheduler(timezone="Asia/Almaty")
    scheduler.add_job(auto_book, "cron", hour=7, minute=0, args=[app])
    scheduler.add_job(send_reminders, "interval", minutes=5, args=[app])
    scheduler.start()

    logger.info("Бот запущен ✅ Авто-бронирование в 07:00 (Астана)")
    app.run_polling()


if __name__ == "__main__":
    main()
