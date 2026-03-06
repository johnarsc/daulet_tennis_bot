"""
Telegram-бот для Daulet Tennis Academy
- Авто-бронирование в 07:00 на выбранные дни
- Настройки через /settings
- Напоминание за 2 часа
"""

import logging
import os
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from playwright.async_api import async_playwright
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes, ConversationHandler
)

# ─────────────────────────────────────────────
# НАСТРОЙКИ
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID      = int(os.environ.get("ADMIN_CHAT_ID", "0"))
YOUR_NAME          = os.environ.get("YOUR_NAME", "Асия")
YOUR_PHONE         = os.environ.get("YOUR_PHONE", "+77777720466")

BOOKING_URL = "https://n551098.alteg.io/company/521176/personal/menu?o="

WEEKDAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
WEEKDAY_NAMES_FULL = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

# Настройки по умолчанию (сохраняются в памяти)
settings = {
    "target_weekdays": [1, 3],      # вторник, четверг
    "preferred_times": ["20:00", "21:00"],
    "auto_book_enabled": True,
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SELECT_DATE, SELECT_TIME, CONFIRM = range(3)
bookings_store: list = []

TIME_SLOTS = [
    ["07:00", "08:00", "09:00"],
    ["10:00", "11:00", "12:00"],
    ["13:00", "14:00", "15:00"],
    ["16:00", "17:00", "18:00"],
    ["19:00", "20:00", "21:00"],
]


# ══════════════════════════════════════════════
# Playwright
# ══════════════════════════════════════════════

async def playwright_book(date_str: str, time_str: str, name: str, phone: str) -> dict:
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

            await page.goto(BOOKING_URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)

            await page.click("text=Аренда теннисного корта", timeout=10000)
            await page.wait_for_timeout(1500)

            await page.click("text=Аренда крытого корта", timeout=10000)
            await page.wait_for_timeout(1000)

            await page.click("text=Выбрать корт", timeout=10000)
            await page.wait_for_timeout(1500)

            try:
                await page.click("text=Корт 7", timeout=8000)
            except Exception:
                await page.locator("text=Крытый").first.click(timeout=8000)
            await page.wait_for_timeout(1500)

            await page.click("text=Выбрать дату и время", timeout=10000)
            await page.wait_for_timeout(2000)

            date_obj = datetime.strptime(date_str, "%d.%m.%Y")
            day_num = str(date_obj.day)
            await page.locator(f"text={day_num}").first.click(timeout=8000)
            await page.wait_for_timeout(1500)

            time_locator = page.locator(f"text={time_str}").first
            if not await time_locator.is_visible():
                await page.evaluate("window.scrollBy(0, 300)")
                await page.wait_for_timeout(1000)
            if not await time_locator.is_visible():
                await browser.close()
                return {"success": False, "error": f"Время {time_str} недоступно"}

            await time_locator.click(timeout=8000)
            await page.wait_for_timeout(1000)

            await page.click("text=Готово", timeout=8000)
            await page.wait_for_timeout(2000)

            name_input = page.locator("input[placeholder='Имя'], input[placeholder='Имя *']").first
            await name_input.clear()
            await name_input.fill(name, timeout=5000)
            await page.wait_for_timeout(500)

            phone_input = page.locator("input[placeholder='Телефон'], input[type='tel']").first
            await phone_input.clear()
            await phone_input.fill(phone, timeout=5000)
            await page.wait_for_timeout(500)

            try:
                checkbox = page.locator("input[type='checkbox']").first
                if not await checkbox.is_checked():
                    await checkbox.click(timeout=3000)
                    await page.wait_for_timeout(500)
            except Exception:
                pass

            await page.click("text=Записаться", timeout=8000)
            await page.wait_for_timeout(4000)

            screenshot = await page.screenshot(type="png", full_page=True)
            page_text = await page.content()
            is_success = any(kw.lower() in page_text.lower() for kw in
                             ["запись создана", "успешно", "подтверждена", "вы записаны", "спасибо"])

            await browser.close()
            return {"success": is_success, "screenshot": screenshot,
                    "error": None if is_success else "Запись не подтверждена"}

    except Exception as e:
        logger.error(f"Playwright error: {e}")
        return {"success": False, "error": str(e), "screenshot": None}


# ══════════════════════════════════════════════
# Вспомогательные
# ══════════════════════════════════════════════

def get_next_target_date() -> datetime:
    now = datetime.now()
    for i in range(1, 8):
        candidate = now + timedelta(days=i)
        if candidate.weekday() in settings["target_weekdays"]:
            return candidate
    return now + timedelta(days=1)


def _weekday(dt: datetime) -> str:
    return WEEKDAY_NAMES[dt.weekday()]


def settings_text() -> str:
    days = ", ".join([WEEKDAY_NAMES_FULL[d] for d in sorted(settings["target_weekdays"])])
    times = " → ".join(settings["preferred_times"])
    auto = "✅ Включено" if settings["auto_book_enabled"] else "❌ Выключено"
    return (
        f"⚙️ *Текущие настройки:*\n\n"
        f"📅 Дни бронирования: *{days}*\n"
        f"⏰ Слоты времени: *{times}*\n"
        f"🤖 Авто-бронирование: *{auto}*"
    )


# ══════════════════════════════════════════════
# /settings
# ══════════════════════════════════════════════

async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Изменить дни", callback_data="set_days")],
        [InlineKeyboardButton("⏰ Изменить время", callback_data="set_times")],
        [
            InlineKeyboardButton(
                "🤖 Выключить авто" if settings["auto_book_enabled"] else "🤖 Включить авто",
                callback_data="toggle_auto"
            )
        ],
        [InlineKeyboardButton("🔖 Забронировать сейчас", callback_data="book_now")],
    ])
    await update.message.reply_text(settings_text(), parse_mode="Markdown", reply_markup=keyboard)


async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "set_days":
        # Показываем кнопки для каждого дня недели
        buttons = []
        for i, name in enumerate(WEEKDAY_NAMES_FULL):
            mark = "✅" if i in settings["target_weekdays"] else "☐"
            buttons.append([InlineKeyboardButton(f"{mark} {name}", callback_data=f"day_{i}")])
        buttons.append([InlineKeyboardButton("💾 Сохранить", callback_data="save_days")])
        await query.edit_message_text("📅 Выберите дни для авто-бронирования:", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("day_"):
        day_num = int(data.split("_")[1])
        if day_num in settings["target_weekdays"]:
            settings["target_weekdays"].remove(day_num)
        else:
            settings["target_weekdays"].append(day_num)
        # Обновляем кнопки
        buttons = []
        for i, name in enumerate(WEEKDAY_NAMES_FULL):
            mark = "✅" if i in settings["target_weekdays"] else "☐"
            buttons.append([InlineKeyboardButton(f"{mark} {name}", callback_data=f"day_{i}")])
        buttons.append([InlineKeyboardButton("💾 Сохранить", callback_data="save_days")])
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))

    elif data == "save_days":
        if not settings["target_weekdays"]:
            await query.answer("⚠️ Выберите хотя бы один день!", show_alert=True)
            return
        days = ", ".join([WEEKDAY_NAMES_FULL[d] for d in sorted(settings["target_weekdays"])])
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 Изменить дни", callback_data="set_days")],
            [InlineKeyboardButton("⏰ Изменить время", callback_data="set_times")],
            [InlineKeyboardButton(
                "🤖 Выключить авто" if settings["auto_book_enabled"] else "🤖 Включить авто",
                callback_data="toggle_auto"
            )],
            [InlineKeyboardButton("🔖 Забронировать сейчас", callback_data="book_now")],
        ])
        await query.edit_message_text(
            settings_text() + f"\n\n✅ Дни сохранены: *{days}*",
            parse_mode="Markdown",
            reply_markup=keyboard
        )

    elif data == "set_times":
        all_times = ["07:00","08:00","09:00","10:00","11:00","12:00",
                     "13:00","14:00","15:00","16:00","17:00","18:00","19:00","20:00","21:00","22:00"]
        buttons = []
        row = []
        for t in all_times:
            mark = "✅" if t in settings["preferred_times"] else "☐"
            row.append(InlineKeyboardButton(f"{mark} {t}", callback_data=f"time_{t}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("💾 Сохранить", callback_data="save_times")])
        await query.edit_message_text(
            "⏰ Выберите слоты времени (по приоритету — первый выбранный пробуется первым):",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data.startswith("time_"):
        t = data.split("_", 1)[1]
        if t in settings["preferred_times"]:
            settings["preferred_times"].remove(t)
        else:
            settings["preferred_times"].append(t)
        # Обновляем кнопки
        all_times = ["07:00","08:00","09:00","10:00","11:00","12:00",
                     "13:00","14:00","15:00","16:00","17:00","18:00","19:00","20:00","21:00","22:00"]
        buttons = []
        row = []
        for tm in all_times:
            mark = "✅" if tm in settings["preferred_times"] else "☐"
            row.append(InlineKeyboardButton(f"{mark} {tm}", callback_data=f"time_{tm}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("💾 Сохранить", callback_data="save_times")])
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))

    elif data == "save_times":
        if not settings["preferred_times"]:
            await query.answer("⚠️ Выберите хотя бы один слот!", show_alert=True)
            return
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 Изменить дни", callback_data="set_days")],
            [InlineKeyboardButton("⏰ Изменить время", callback_data="set_times")],
            [InlineKeyboardButton(
                "🤖 Выключить авто" if settings["auto_book_enabled"] else "🤖 Включить авто",
                callback_data="toggle_auto"
            )],
            [InlineKeyboardButton("🔖 Забронировать сейчас", callback_data="book_now")],
        ])
        await query.edit_message_text(settings_text() + "\n\n✅ Время сохранено!", parse_mode="Markdown", reply_markup=keyboard)

    elif data == "toggle_auto":
        settings["auto_book_enabled"] = not settings["auto_book_enabled"]
        status = "включено ✅" if settings["auto_book_enabled"] else "выключено ❌"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 Изменить дни", callback_data="set_days")],
            [InlineKeyboardButton("⏰ Изменить время", callback_data="set_times")],
            [InlineKeyboardButton(
                "🤖 Выключить авто" if settings["auto_book_enabled"] else "🤖 Включить авто",
                callback_data="toggle_auto"
            )],
            [InlineKeyboardButton("🔖 Забронировать сейчас", callback_data="book_now")],
        ])
        await query.edit_message_text(
            settings_text() + f"\n\n🤖 Авто-бронирование {status}",
            parse_mode="Markdown", reply_markup=keyboard
        )

    elif data == "book_now":
        await query.edit_message_text("⏳ Запускаю бронирование прямо сейчас...")
        await auto_book(ctx.application)


# ══════════════════════════════════════════════
# Авто-бронирование
# ══════════════════════════════════════════════

async def auto_book(app: Application):
    if not settings["auto_book_enabled"]:
        logger.info("Авто-бронирование выключено")
        return

    if not settings["target_weekdays"]:
        logger.info("Нет дней для бронирования")
        return

    target = get_next_target_date()
    date_str = target.strftime("%d.%m.%Y")
    weekday_name = WEEKDAY_NAMES_FULL[target.weekday()]

    if ADMIN_CHAT_ID:
        await app.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"🤖 Начинаю автобронирование...\n📅 {weekday_name} {date_str}"
        )

    booked = False
    for time_str in settings["preferred_times"]:
        result = await playwright_book(date_str, time_str, YOUR_NAME, YOUR_PHONE)
        if result["success"]:
            booked = True
            booking_dt = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
            bookings_store.append({
                "chat_id": ADMIN_CHAT_ID,
                "datetime": booking_dt,
                "date": date_str,
                "time": time_str,
                "reminded": False,
            })
            if ADMIN_CHAT_ID:
                await app.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"✅ *Запись создана!*\n\n📅 {weekday_name} {date_str}\n⏰ {time_str}\n\n🔔 Напомню за 2 часа.",
                    parse_mode="Markdown"
                )
                if result.get("screenshot"):
                    await app.bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=result["screenshot"], caption="📸 Подтверждение")
            break
        else:
            if result.get("screenshot") and ADMIN_CHAT_ID:
                await app.bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=result["screenshot"], caption=f"⚠️ Слот {time_str} недоступен")

    if not booked and ADMIN_CHAT_ID:
        times_str = " и ".join(settings["preferred_times"])
        await app.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"❌ *Не удалось забронировать*\n\n📅 {weekday_name} {date_str}\nСлоты {times_str} недоступны.\n\nВручную: {BOOKING_URL}",
            parse_mode="Markdown"
        )


# ══════════════════════════════════════════════
# Команды
# ══════════════════════════════════════════════

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎾 *Daulet Tennis Academy*\n\n"
        "Привет! Я автоматически бронирую корт каждый день в 07:00.\n\n"
        "📌 Команды:\n"
        "/settings — настройки авто-бронирования\n"
        "/status — статус и следующая цель\n"
        "/book — забронировать вручную\n"
        "/mybookings — мои записи\n"
        "/cancel — отменить",
        parse_mode="Markdown"
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    target = get_next_target_date()
    now = datetime.now()
    auto = "✅ Включено" if settings["auto_book_enabled"] else "❌ Выключено"
    days = ", ".join([WEEKDAY_NAMES_FULL[d] for d in sorted(settings["target_weekdays"])]) or "не выбраны"
    times = " → ".join(settings["preferred_times"]) or "не выбраны"
    await update.message.reply_text(
        f"🤖 *Статус бота*\n\n"
        f"Авто-бронирование: *{auto}*\n"
        f"⏰ Запуск: каждый день в *07:00*\n"
        f"📅 Дни: *{days}*\n"
        f"🎯 Слоты: *{times}*\n"
        f"📅 Следующая цель: *{WEEKDAY_NAMES_FULL[target.weekday()]} {target.strftime('%d.%m.%Y')}*\n\n"
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
        await update.message.reply_text("❌ Введите как *ЧЧ:ММ*:", parse_mode="Markdown")
        return SELECT_TIME
    ctx.user_data["time"] = text
    keyboard = [["✅ Подтвердить", "❌ Отменить"]]
    await update.message.reply_text(
        f"📋 *Подтвердите:*\n\n🏟 Крытый корт (Корт 7)\n📅 {ctx.user_data['date']}\n⏰ {text}",
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
    await update.message.reply_text("⏳ Бронирую, подождите...", reply_markup=ReplyKeyboardRemove())
    result = await playwright_book(date, time, YOUR_NAME, YOUR_PHONE)

    if result["success"]:
        try:
            booking_dt = datetime.strptime(f"{date} {time}", "%d.%m.%Y %H:%M")
            bookings_store.append({"chat_id": update.effective_chat.id, "datetime": booking_dt,
                                   "date": date, "time": time, "reminded": False})
        except Exception as e:
            logger.error(e)
        await update.message.reply_text(
            f"✅ *Запись создана!*\n\n🏟 Крытый корт\n📅 {date} в {time}\n\n🔔 Напомню за 2 часа.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(f"❌ Не удалось.\n{result.get('error', '')}\n\nВручную: {BOOKING_URL}")

    if result.get("screenshot"):
        await update.message.reply_photo(photo=result["screenshot"], caption="📸 Скриншот")
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
        text += f"{i}. 📅 {b['date']} в {b['time']}\n"
    await update.message.reply_text(text, parse_mode="Markdown")


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


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
                    text=f"🔔 *Напоминание!*\n\nЧерез 2 часа тренировка:\n🏟 Крытый корт\n📅 {b['date']} в {b['time']}\n\nНе забудьте! 🎾",
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
            SELECT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_date)],
            SELECT_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_time)],
            CONFIRM:     [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_booking)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("mybookings", my_bookings))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(conv)

    scheduler = AsyncIOScheduler(timezone="Asia/Almaty")
    scheduler.add_job(auto_book, "cron", hour=7, minute=0, args=[app])
    scheduler.add_job(send_reminders, "interval", minutes=5, args=[app])
    scheduler.start()

    logger.info("Бот запущен ✅")
    app.run_polling()


if __name__ == "__main__":
    main()
