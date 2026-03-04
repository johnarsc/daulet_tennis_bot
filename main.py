"""
Telegram-бот для Daulet Tennis Academy (упрощённый вариант)
Без API — просто генерирует ссылку на бронирование

Установка: pip install python-telegram-bot==20.7 apscheduler
"""

import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler, CallbackQueryHandler
)

# ─────────────────────────────────────────────
# НАСТРОЙКИ — замените на свои
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = "8403727447:AAGCWtCn9C8zEriatPgleG6XUC61KD_Sy2I"  # от @BotFather
COMPANY_ID = "521176"
BOOKING_URL = f"https://n551098.alteg.io/company/{COMPANY_ID}/personal/menu?o="

# ─────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Шаги диалога
SELECT_DATE, SELECT_TIME, SELECT_COURT = range(3)

# Хранилище записей для напоминаний
bookings_store: list = []

# Доступные корты (настройте под свои)
COURTS = ["Корт №1", "Корт №2", "Корт №3"]

# Популярные временные слоты
TIME_SLOTS = [
    ["07:00", "08:00", "09:00"],
    ["10:00", "11:00", "12:00"],
    ["13:00", "14:00", "15:00"],
    ["16:00", "17:00", "18:00"],
    ["19:00", "20:00", "21:00"],
]


# ══════════════════════════════════════════════
# Команды
# ══════════════════════════════════════════════

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎾 *Daulet Tennis Academy*\n\n"
        "Привет! Я помогу быстро забронировать корт.\n\n"
        "📌 Команды:\n"
        "/book — забронировать корт\n"
        "/mybookings — мои записи\n"
        "/cancel — отменить",
        parse_mode="Markdown"
    )


async def book_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Генерируем ближайшие 7 дней как кнопки
    keyboard = []
    row = []
    for i in range(7):
        day = datetime.now() + timedelta(days=i)
        label = day.strftime("%d.%m") + (" (сегодня)" if i == 0 else " (завтра)" if i == 1 else f" ({_weekday(day)})")
        row.append(label)
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    ctx.user_data["days_map"] = {}
    for i in range(7):
        day = datetime.now() + timedelta(days=i)
        label = day.strftime("%d.%m") + (" (сегодня)" if i == 0 else " (завтра)" if i == 1 else f" ({_weekday(day)})")
        ctx.user_data["days_map"][label] = day.strftime("%d.%m.%Y")

    await update.message.reply_text(
        "📅 Выберите дату или введите вручную (формат: *ДД.ММ.ГГГГ*)",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return SELECT_DATE


def _weekday(dt: datetime) -> str:
    days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    return days[dt.weekday()]


async def select_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    days_map = ctx.user_data.get("days_map", {})

    # Если выбрали кнопку
    if text in days_map:
        date_str = days_map[text]
    else:
        # Ручной ввод
        try:
            date_obj = datetime.strptime(text, "%d.%m.%Y")
            if date_obj.date() < datetime.today().date():
                await update.message.reply_text("❌ Дата в прошлом. Введите будущую дату:")
                return SELECT_DATE
            date_str = text
        except ValueError:
            await update.message.reply_text("❌ Неверный формат. Введите дату как *ДД.ММ.ГГГГ*:", parse_mode="Markdown")
            return SELECT_DATE

    ctx.user_data["date"] = date_str

    await update.message.reply_text(
        f"⏰ Выберите время на *{date_str}*\nили введите вручную (формат: *ЧЧ:ММ*)",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(TIME_SLOTS, one_time_keyboard=True, resize_keyboard=True)
    )
    return SELECT_TIME


async def select_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # Валидация времени
    try:
        datetime.strptime(text, "%H:%M")
    except ValueError:
        await update.message.reply_text("❌ Неверный формат. Введите время как *ЧЧ:ММ*, например `09:00`:", parse_mode="Markdown")
        return SELECT_TIME

    ctx.user_data["time"] = text

    keyboard = [[c] for c in COURTS]
    keyboard.append(["Любой корт"])
    await update.message.reply_text(
        "🏟 Выберите корт:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return SELECT_COURT


async def select_court(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    court = update.message.text.strip()
    date = ctx.user_data["date"]
    time = ctx.user_data["time"]

    # Формируем ссылку на бронирование
    booking_link = BOOKING_URL

    # Сохраняем запись для напоминания
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

    # Кнопка-ссылка
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📲 Открыть и подтвердить запись", url=booking_link)]
    ])

    await update.message.reply_text(
        f"✅ *Данные записи:*\n\n"
        f"🏟 {court}\n"
        f"📅 {date}\n"
        f"⏰ {time}\n\n"
        f"👆 Нажмите кнопку ниже — откроется страница бронирования.\n"
        f"Дата и время уже выбраны, просто подтвердите!\n\n"
        f"🔔 Напоминание придёт за 2 часа.",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

    await update.message.reply_text("Что-то ещё?", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def my_bookings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    now = datetime.now()
    user_bookings = [
        b for b in bookings_store
        if b["chat_id"] == chat_id and b["datetime"] > now
    ]

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
# Напоминания (каждые 5 минут проверяем)
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
                        f"Через 2 часа у вас корт:\n"
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
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("mybookings", my_bookings))
    app.add_handler(conv)

    # Напоминания каждые 5 минут
    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_reminders, "interval", minutes=5, args=[app])
    scheduler.start()

    logger.info("Бот запущен ✅")
    app.run_polling()


if __name__ == "__main__":
    main()
