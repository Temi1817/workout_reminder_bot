import asyncio
import logging
import re
from datetime import datetime
import pytz

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command, CommandStart
from aiogram.fsm.storage.memory import MemoryStorage

from .config import BOT_TOKEN, TIMEZONE, LOG_LEVEL
from .db import (
    init_db, get_or_create_user, create_reminder,
    get_active_reminders, get_reminder_by_id, delete_reminder,
    mark_workout_completed, get_user_stats
)
from .scheduler import (
    set_bot_instance, start_scheduler, stop_scheduler,
    restore_reminders_from_db, schedule_once_reminder,
    schedule_everyday_reminder, schedule_days_reminder, remove_job
)

# ---------------- Logging ----------------
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper()),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ---------------- Bot/DP -----------------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# --------------- Helpers -----------------
def validate_time_format(time_str: str) -> bool:
    """Validate HH:MM time format (24h)."""
    pattern = r'^([01]?[0-9]|2[0-3]):([0-5][0-9])$'
    return bool(re.match(pattern, time_str))


def parse_days(days_str: str) -> tuple[bool, str | list[int]]:
    """
    Parse days like 'пн,ср,пт' or 'ПН, Ср , Пт'
    Return (ok, list_of_ints) where Monday=0 ... Sunday=6.
    If not ok -> (False, 'error text')
    """
    map_ru = {
        'пн': 0, 'вт': 1, 'ср': 2, 'чт': 3, 'пт': 4, 'сб': 5, 'вс': 6,
        # на всякий случай латиница
        'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6,
    }
    items = [d.strip().lower() for d in days_str.split(',') if d.strip()]
    if not items:
        return False, "Не указаны дни недели."
    out = []
    for d in items:
        if d not in map_ru:
            return False, f"Неизвестный день недели: {d}"
        out.append(map_ru[d])
    # убрать дубликаты, сохранить порядок
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            uniq.append(x)
            seen.add(x)
    return True, uniq


def days_list_to_str(days: list[int]) -> str:
    """Convert [0,2,4] -> 'пн,ср,пт'."""
    back = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']
    return ",".join(back[d] for d in days)


# --------------- Commands ----------------
@dp.message(CommandStart())
async def start_command(message: Message):
    user = get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name
    )
    welcome_text = (
        f"💪 Привет, {message.from_user.first_name}!\n\n"
        "Я твой помощник-напоминалка о тренировках 🏋️‍♂️\n\n"
        "Команды:\n"
        "• /add HH:MM текст — разовое на сегодня\n"
        "• /everyday HH:MM текст — каждый день\n"
        "• /days пн,ср,пт HH:MM текст — по дням недели\n"
        "• /list — список активных\n"
        "• /delete ID — удалить\n"
        "• /done ID — отметить выполненным\n"
        "• /stats — статистика за 7 дней\n\n"
        "Пример: /add 18:00 Тренировка в спортзале 💪"
    )
    await message.answer(welcome_text)


@dp.message(Command("add"))
async def add_reminder(message: Message):
    """Create one-time reminder for today."""
    try:
        # /add HH:MM текст
        args = message.text.split(' ', 2)
        if len(args) < 3:
            await message.answer(
                "❌ Формат: /add HH:MM текст\n"
                "Например: /add 18:00 Тренировка в спортзале"
            )
            return

        time_str = args[1].strip()
        text = args[2].strip()
        if not validate_time_format(time_str):
            await message.answer("❌ Время должно быть в формате HH:MM, напр. 18:00")
            return

        tz = pytz.timezone(TIMEZONE)
        now = datetime.now(tz)
        h, m = map(int, time_str.split(":"))
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now:
            await message.answer("❌ Это время уже прошло сегодня. Укажи более позднее.")
            return

        user = get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name
        )

        reminder = create_reminder(
            user_id=user.id,
            reminder_type="once",
            time=time_str,
            text=text
        )

        job_id = schedule_once_reminder(
            reminder.id,
            message.from_user.id,
            time_str,
            text
        )

        if job_id:
            from .db import get_db
            with get_db() as dbs:
                db_rem = dbs.get(reminder.__class__, reminder.id)
                db_rem.job_id = job_id
                dbs.commit()

            await message.answer(
                f"✅ Напоминание создано!\n"
                f"🕐 {time_str}\n"
                f"📝 {text}\n"
                f"🆔 ID: {reminder.id}"
            )
        else:
            await message.answer("❌ Не удалось запланировать напоминание.")
    except Exception as e:
        logger.exception("Error in /add: %s", e)
        await message.answer("❌ Произошла ошибка при создании напоминания.")


@dp.message(Command("everyday"))
async def everyday_reminder(message: Message):
    """Create daily reminder."""
    try:
        # /everyday HH:MM текст
        args = message.text.split(' ', 2)
        if len(args) < 3:
            await message.answer(
                "❌ Формат: /everyday HH:MM текст\n"
                "Например: /everyday 07:00 Утренняя пробежка"
            )
            return

        time_str = args[1].strip()
        text = args[2].strip()
        if not validate_time_format(time_str):
            await message.answer("❌ Время должно быть HH:MM.")
            return

        user = get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name
        )

        reminder = create_reminder(
            user_id=user.id,
            reminder_type="everyday",
            time=time_str,
            text=text
        )

        job_id = schedule_everyday_reminder(
            reminder.id,
            message.from_user.id,
            time_str,
            text
        )

        if job_id:
            from .db import get_db
            with get_db() as dbs:
                db_rem = dbs.get(reminder.__class__, reminder.id)
                db_rem.job_id = job_id
                dbs.commit()

            await message.answer(
                f"✅ Ежедневное напоминание создано!\n"
                f"🕐 {time_str}\n"
                f"📝 {text}\n"
                f"🆔 ID: {reminder.id}"
            )
        else:
            await message.answer("❌ Не удалось запланировать ежедневное напоминание.")
    except Exception as e:
        logger.exception("Error in /everyday: %s", e)
        await message.answer("❌ Ошибка при создании напоминания.")


@dp.message(Command("days"))
async def days_reminder(message: Message):
    """Create reminder for specific days of week."""
    try:
        # /days пн,ср,пт HH:MM текст
        args = message.text.split(' ', 3)
        if len(args) < 4:
            await message.answer(
                "❌ Формат: /days дни HH:MM текст\n"
                "Напр.: /days пн,ср,пт 19:00 Силовая тренировка\n"
                "Доступные дни: пн, вт, ср, чт, пт, сб, вс"
            )
            return

        days_str = args[1].strip()
        time_str = args[2].strip()
        text = args[3].strip()

        ok, parsed = parse_days(days_str)
        if not ok:
            await message.answer(f"❌ {parsed}\nДни: пн, вт, ср, чт, пт, сб, вс")
            return
        days_list = parsed  # list[int]

        if not validate_time_format(time_str):
            await message.answer("❌ Время должно быть HH:MM.")
            return

        user = get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name
        )

        reminder = create_reminder(
            user_id=user.id,
            reminder_type="days",
            time=time_str,
            text=text,
            days=days_list
        )

        job_id = schedule_days_reminder(
            reminder.id,
            message.from_user.id,
            time_str,
            days_list,
            text
        )

        if job_id:
            from .db import get_db
            with get_db() as dbs:
                db_rem = dbs.get(reminder.__class__, reminder.id)
                db_rem.job_id = job_id
                dbs.commit()

            await message.answer(
                f"✅ Напоминание по дням создано!\n"
                f"📅 Дни: {days_list_to_str(days_list)}\n"
                f"🕐 {time_str}\n"
                f"📝 {text}\n"
                f"🆔 ID: {reminder.id}"
            )
        else:
            await message.answer("❌ Не удалось запланировать напоминание по дням.")
    except Exception as e:
        logger.exception("Error in /days: %s", e)
        await message.answer("❌ Ошибка при создании напоминания.")


@dp.message(Command("list"))
async def list_reminders(message: Message):
    """Show active reminders."""
    try:
        user = get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name
        )

        reminders = get_active_reminders(user_id=user.id)
        if not reminders:
            await message.answer("📋 У тебя пока нет активных напоминаний.")
            return

        type_emoji = {'once': '🔔', 'everyday': '🔄', 'days': '📅'}
        lines = ["📋 Твои активные напоминания:\n"]
        for r in reminders:
            type_text = 'Разовое' if r.reminder_type == 'once' else (
                'Ежедневно' if r.reminder_type == 'everyday' else f"По дням: {days_list_to_str(r.days)}"
            )
            lines.append(
                f"{type_emoji.get(r.reminder_type, '🔔')} **ID {r.id}**\n"
                f"⏰ {r.time}\n"
                f"📝 {r.text}\n"
                f"📊 {type_text}\n"
                f"📅 Создано: {r.created_at.strftime('%d.%m.%Y %H:%M')}\n"
            )
        await message.answer("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error in /list: %s", e)
        await message.answer("❌ Произошла ошибка при получении списка.")


@dp.message(Command("delete"))
async def delete_reminder_command(message: Message):
    """Delete reminder by ID."""
    try:
        args = message.text.split()
        if len(args) != 2:
            await message.answer("❌ Формат: /delete ID")
            return
        try:
            reminder_id = int(args[1])
        except ValueError:
            await message.answer("❌ ID должен быть числом.")
            return

        user = get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name
        )

        reminder = get_reminder_by_id(reminder_id, user_id=user.id)
        if not reminder:
            await message.answer("❌ Напоминание не найдено.")
            return

        if reminder.job_id:
            remove_job(reminder.job_id)

        if delete_reminder(reminder_id, user_id=user.id):
            await message.answer(f"✅ Напоминание удалено! ID: {reminder_id}\n📝 {reminder.text}")
        else:
            await message.answer("❌ Не удалось удалить напоминание.")
    except Exception as e:
        logger.exception("Error in /delete: %s", e)
        await message.answer("❌ Ошибка при удалении.")


@dp.message(Command("done"))
async def mark_done_command(message: Message):
    """Mark workout as completed for a reminder ID."""
    try:
        args = message.text.split()
        if len(args) != 2:
            await message.answer("❌ Формат: /done ID")
            return
        try:
            reminder_id = int(args[1])
        except ValueError:
            await message.answer("❌ ID должен быть числом.")
            return

        user = get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name
        )

        reminder = get_reminder_by_id(reminder_id, user_id=user.id)
        if not reminder:
            await message.answer("❌ Напоминание не найдено.")
            return

        mark_workout_completed(reminder_id, user.id, reminder.text)
        await message.answer(f"🎉 Отлично! Тренировка выполнена!\n💪 {reminder.text}\n⭐ Так держать!")
    except Exception as e:
        logger.exception("Error in /done: %s", e)
        await message.answer("❌ Ошибка при отметке выполнения.")


@dp.message(Command("stats"))
async def stats_command(message: Message):
    """Show user statistics (last 7 days)."""
    try:
        user = get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name
        )

        stats = get_user_stats(user.id, days=7)
        text = (
            f"📊 Статистика за {stats['days']} дней:\n\n"
            f"✅ Выполнено тренировок: {stats['completed_workouts']}\n"
            f"🔔 Активных напоминаний: {stats['total_reminders']}\n\n"
        )

        done = stats['completed_workouts']
        if done == 0:
            text += "💪 Время начать тренироваться! Ты можешь это сделать!"
        elif done < 3:
            text += "🔥 Неплохое начало! Продолжай в том же духе!"
        elif done < 7:
            text += "⭐ Отлично! Ты на правильном пути к цели!"
        else:
            text += "🏆 Невероятно! Ты настоящий чемпион!"

        await message.answer(text)
    except Exception as e:
        logger.exception("Error in /stats: %s", e)
        await message.answer("❌ Ошибка при получении статистики.")


@dp.callback_query(F.data.startswith("done_"))
async def handle_done_callback(callback: CallbackQuery):
    """Button callback to mark workout done."""
    try:
        reminder_id = int(callback.data.split("_")[1])

        user = get_or_create_user(
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
            last_name=callback.from_user.last_name
        )

        reminder = get_reminder_by_id(reminder_id, user_id=user.id)
        if not reminder:
            await callback.answer("❌ Напоминание не найдено!", show_alert=True)
            return

        mark_workout_completed(reminder_id, user.id, reminder.text)
        await callback.message.edit_text(
            f"✅ Тренировка выполнена!\n\n{reminder.text}\n\n🎉 Отлично! Так держать! 💪"
        )
        await callback.answer("🎉 Отмечено!")
    except Exception as e:
        logger.exception("Error in done callback: %s", e)
        await callback.answer("❌ Ошибка.", show_alert=True)


@dp.message()
async def handle_unknown_command(message: Message):
    await message.answer(
        "🤔 Не понимаю эту команду.\n\n"
        "Команды:\n"
        "• /add HH:MM текст — разовое\n"
        "• /everyday HH:MM текст — ежедневное\n"
        "• /days пн,ср,пт HH:MM текст — по дням\n"
        "• /list — список\n"
        "• /delete ID — удалить\n"
        "• /done ID — выполнено\n"
        "• /stats — статистика\n\n"
        "Нужна помощь? /start 😊"
    )


# --------------- App entry ----------------
async def main():
    try:
        init_db()
        set_bot_instance(bot)
        start_scheduler()
        restore_reminders_from_db()
        logger.info("Bot starting...")
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
    finally:
        stop_scheduler()
        logger.info("Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
