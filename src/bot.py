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
    mark_workout_completed, get_user_stats, get_db, rename_reminder,
    get_any_reminder_by_id   # ищем напоминание без фильтра is_active
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
    return bool(re.match(r'^([01]?[0-9]|2[0-3]):([0-5][0-9])$', time_str))


def parse_days(days_str: str):
    mapping = {
        'пн': 0, 'вт': 1, 'ср': 2, 'чт': 3, 'пт': 4, 'сб': 5, 'вс': 6,
        'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6
    }
    items = [d.strip().lower() for d in days_str.split(',') if d.strip()]
    if not items:
        return False, "Не указаны дни недели."
    nums = []
    for d in items:
        if d not in mapping:
            return False, f"Неизвестный день недели: {d}"
        nums.append(mapping[d])
    seen, out = set(), []
    for x in nums:
        if x not in seen:
            out.append(x)
            seen.add(x)
    return True, out


def days_list_to_str(days: list[int]) -> str:
    back = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']
    return ",".join(back[d] for d in days)


# --------------- Commands ----------------
@dp.message(CommandStart())
async def start_command(message: Message):
    get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name
    )
    await message.answer(
        f"💪 Привет, {message.from_user.first_name}!\n\n"
        "Я твой помощник-напоминалка о тренировках 🏋️‍♂️\n\n"
        "Команды:\n"
        "• /add HH:MM текст — разовое на сегодня\n"
        "• /everyday HH:MM текст — каждый день\n"
        "• /days пн,ср,пт HH:MM текст — по дням недели\n"
        "• /rename ID новый_текст — переименовать\n"
        "• /list — список активных\n"
        "• /delete ID — удалить\n"
        "• /done ID — отметить выполненным\n"
        "• /stats — статистика за 7 дней\n\n"
        "Пример: /add 18:00 Тренировка в спортзале 💪"
    )


@dp.message(Command("add"))
async def add_reminder(message: Message):
    """Create one-time reminder for today."""
    try:
        args = message.text.split(' ', 2)
        if len(args) < 3:
            await message.answer("❌ Формат: /add HH:MM текст")
            return

        time_str, text = args[1].strip(), args[2].strip()
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

        reminder = create_reminder(user_id=user.id, reminder_type="once", time=time_str, text=text)
        job_id = schedule_once_reminder(reminder.id, message.from_user.id, time_str, text)

        if job_id:
            with get_db() as dbs:
                db_rem = dbs.get(reminder.__class__, reminder.id)
                db_rem.job_id = job_id
                dbs.commit()
            await message.answer(f"✅ Напоминание создано!\n🕐 {time_str}\n📝 {text}\n🆔 ID: {reminder.id}")
        else:
            await message.answer("❌ Не удалось запланировать напоминание.")
    except Exception as e:
        logger.exception("Error in /add: %s", e)
        await message.answer("❌ Произошла ошибка при создании напоминания.")


@dp.message(Command("everyday"))
async def everyday_reminder(message: Message):
    try:
        args = message.text.split(' ', 2)
        if len(args) < 3:
            await message.answer("❌ Формат: /everyday HH:MM текст")
            return

        time_str, text = args[1].strip(), args[2].strip()
        if not validate_time_format(time_str):
            await message.answer("❌ Время должно быть HH:MM.")
            return

        user = get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name
        )

        reminder = create_reminder(user_id=user.id, reminder_type="everyday", time=time_str, text=text)
        job_id = schedule_everyday_reminder(reminder.id, message.from_user.id, time_str, text)

        if job_id:
            with get_db() as dbs:
                db_rem = dbs.get(reminder.__class__, reminder.id)
                db_rem.job_id = job_id
                dbs.commit()
            await message.answer(f"✅ Ежедневное напоминание создано!\n🕐 {time_str}\n📝 {text}\n🆔 ID: {reminder.id}")
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
            await message.answer("❌ Формат: /days дни HH:MM текст\nНапр.: /days пн,ср,пт 19:00 Силовая тренировка")
            return

        days_str_raw, time_str, text = args[1].strip(), args[2].strip(), args[3].strip()
        ok, parsed = parse_days(days_str_raw)
        if not ok:
            await message.answer(f"❌ {parsed}\nДни: пн, вт, ср, чт, пт, сб, вс")
            return

        if not validate_time_format(time_str):
            await message.answer("❌ Время должно быть HH:MM.")
            return

        days_str_norm = days_list_to_str(parsed)  # храним строкой

        user = get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name
        )

        reminder = create_reminder(
            user_id=user.id, reminder_type="days", time=time_str, text=text, days=days_str_norm
        )
        job_id = schedule_days_reminder(reminder.id, message.from_user.id, time_str, days_str_norm, text)

        if job_id:
            with get_db() as dbs:
                db_rem = dbs.get(reminder.__class__, reminder.id)
                db_rem.job_id = job_id
                dbs.commit()
            await message.answer(
                f"✅ Напоминание по дням создано!\n📅 Дни: {days_str_norm}\n🕐 {time_str}\n📝 {text}\n🆔 ID: {reminder.id}"
            )
        else:
            await message.answer("❌ Не удалось запланировать напоминание по дням.")
    except Exception as e:
        logger.exception("Error in /days: %s", e)
        await message.answer("❌ Ошибка при создании напоминания.")


@dp.message(Command("list"))
async def list_reminders(message: Message):
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
            type_text = (
                'Разовое' if r.reminder_type == 'once'
                else ('Ежедневно' if r.reminder_type == 'everyday' else f"По дням: {r.days}")
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
            await message.answer("❌ Напоминание не найдено (возможно, уже удалено).")
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


@dp.message(Command("rename"))
async def rename_reminder_command(message: Message):
    """/rename ID новый_текст"""
    try:
        parts = message.text.split(maxsplit=2)
        if len(parts) < 3:
            await message.answer("❌ Формат: /rename ID новый_текст")
            return

        try:
            reminder_id = int(parts[1])
        except ValueError:
            await message.answer("❌ ID должен быть числом.")
            return

        new_text = parts[2].strip()
        if not new_text:
            await message.answer("❌ Укажи новый текст.")
            return

        user = get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name
        )

        reminder = get_reminder_by_id(reminder_id, user_id=user.id)
        if not reminder:
            await message.answer("❌ Активное напоминание с таким ID не найдено.")
            return

        if not rename_reminder(reminder_id, user.id, new_text):
            await message.answer("❌ Не удалось переименовать напоминание.")
            return

        if reminder.job_id:
            remove_job(reminder.job_id)

        if reminder.reminder_type == "once":
            job_id = schedule_once_reminder(reminder.id, message.from_user.id, reminder.time, new_text)
        elif reminder.reminder_type == "everyday":
            job_id = schedule_everyday_reminder(reminder.id, message.from_user.id, reminder.time, new_text)
        else:
            job_id = schedule_days_reminder(reminder.id, message.from_user.id, reminder.time, reminder.days, new_text)

        if job_id:
            with get_db() as dbs:
                db_rem = dbs.get(reminder.__class__, reminder.id)
                db_rem.job_id = job_id
                dbs.commit()

        await message.answer(f"✏ Напоминание {reminder_id} изменено на: {new_text}")
    except Exception as e:
        logger.exception("Error in /rename: %s", e)
        await message.answer("❌ Ошибка при переименовании.")


@dp.message(Command("done"))
async def mark_done_command(message: Message):
    """Manual /done ID (optional, кнопка обычно удобнее)."""
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

        # пробуем активные, затем любые (на случай once)
        reminder = get_reminder_by_id(reminder_id, user_id=user.id) or \
                   get_any_reminder_by_id(reminder_id, user_id=user.id)
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
    """
    Статистика за 7 дней:
    - по дням: 'ДД.ММ.ГГГГ — X из Y' + прогресс-бар + ✅ для 100%
    - лучший и слабый день
    - текущая серия 100%-дней (streak)
    - среднее выполнение за день
    - активные напоминания сейчас
    """
    try:
        user = get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name
        )

        # Посуточные данные за 7 дней (сегодня, вчера, ...)
        from .db import get_daily_7d_ratio, get_active_reminders
        items = get_daily_7d_ratio(user.id, tz_str=TIMEZONE)

        if not items:
            await message.answer("📊 Пока нет данных за последние 7 дней. Начнём с первой тренировки! 💪")
            return

        total_done = sum(it["done"] for it in items)
        total_planned = sum(it["planned"] for it in items)
        avg_done = total_done / len(items)
        avg_planned = total_planned / len(items) if len(items) else 0
        avg_pct = int(round(100 * total_done / total_planned)) if total_planned > 0 else 0

        # Лучший / слабый день (считаем только дни, где есть план)
        def ratio(it):
            return (it["done"] / it["planned"]) if it["planned"] > 0 else -1
        planned_days = [it for it in items if it["planned"] > 0]
        best_line = max(planned_days, key=ratio) if planned_days else None
        worst_line = min(planned_days, key=ratio) if planned_days else None

        # Серия 100% от сегодняшнего дня назад (дни без плана пропускаем)
        streak = 0
        for it in items:
            if it["planned"] == 0:
                continue
            if it["done"] >= it["planned"]:
                streak += 1
            else:
                break

        # Строки по дням
        lines = [f"📊 Статистика за {len(items)} дней:\n"]
        for it in items:
            planned = it["planned"]
            done = it["done"]
            if planned > 0:
                r = done / planned
                units = max(1, int(round(min(1.0, r) * 10)))  # 1..10
                bar = "▮" * units
            else:
                bar = "—"
            suffix = " ✅" if planned > 0 and done >= planned else ""
            lines.append(f"🗓 {it['date']} — {done} из {planned}   {bar}{suffix}")

        # Итоги
        lines.append("\nИтого за неделю:")
        lines.append(f"✅ Выполнено: {total_done}")
        lines.append(f"🎯 План: {total_planned}")
        if total_planned > 0:
            lines.append(f"📈 Выполнение: {avg_pct}%")

        # Средние за день
        lines.append(f"\n📊 Среднее за день: {avg_done:.1f} из {avg_planned:.1f}")

        # Лучший / сложный день
        if best_line:
            bpct = int(round(100 * best_line["done"] / best_line["planned"])) if best_line["planned"] else 0
            lines.append(f"🌟 Лучший день: {best_line['date']} — {best_line['done']}/{best_line['planned']} ({bpct}%)")
        if worst_line and worst_line is not best_line:
            wpct = int(round(100 * worst_line["done"] / worst_line["planned"])) if worst_line["planned"] else 0
            lines.append(f"⚠️ Сложный день: {worst_line['date']} — {worst_line['done']}/{worst_line['planned']} ({wpct}%)")

        # Серия 100%
        if streak > 0:
            lines.append(f"🔥 Серия 100% дней подряд: {streak}")

        # Активные напоминания сейчас
        active_now = len(get_active_reminders(user_id=user.id))
        lines.append(f"\n🔔 Активных напоминаний сейчас: {active_now}")

        # Мотивашка
        if total_planned > 0:
            if avg_pct >= 95:
                lines.append("\n🏆 Ты машина! Держи этот космический темп!")
            elif avg_pct >= 80:
                lines.append("\n⭐ Отличный прогресс! Чуть-чуть — и будет 100% 😉")
            elif avg_pct >= 50:
                lines.append("\n💪 Неплохо! Пора поднять планку ещё на шаг!")
            else:
                lines.append("\n🚀 Начало положено — сегодня отличный день сделать +1!")

        await message.answer("\n".join(lines))
    except Exception as e:
        logger.exception("Error in /stats: %s", e)
        await message.answer("❌ Ошибка при получении статистики.")

@dp.message(Command("weeks"))
async def weeks_command(message: Message):
    """
    Показывает недельные итоги (с нумерацией):
    1) 12.08–18.08 — 55/56 (98%)
    2) 19.08–25.08 — 50/56 (89%)
    ...
    По умолчанию выводит до 12 недель. Можно: /weeks 20
    """
    try:
        user = get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name
        )

        parts = message.text.split()
        limit = 12
        if len(parts) == 2:
            try:
                limit = max(1, min(52, int(parts[1])))
            except ValueError:
                pass

        from .db import finalize_past_weeks, get_week_summaries
        # На всякий случай перед показом пересчитаем незакрытые прошлые недели
        finalize_past_weeks(user.id, tz_str=TIMEZONE)

        weeks = get_week_summaries(user.id, tz_str=TIMEZONE)
        if not weeks:
            await message.answer("🗂 Пока нет недельных итогов. Дай хотя бы одной неделе завершиться 😉")
            return

        # Нумерация с 1 от самой ранней недели
        if len(weeks) > limit:
            weeks = weeks[-limit:]

        lines = ["🗂 Недельные итоги:\n"]
        for i, w in enumerate(weeks, start=1):
            lines.append(f"{i}) {w['range']} — {w['done']}/{w['planned']} ({w['pct']}%)")

        await message.answer("\n".join(lines))
    except Exception as e:
        logger.exception("Error in /weeks: %s", e)
        await message.answer("❌ Ошибка при получении недельных итогов.")


@dp.callback_query(F.data.startswith("done_"))
async def handle_done_callback(callback: CallbackQuery):
    """Inline button '✅ Выполнено'."""
    try:
        reminder_id = int(callback.data.split("_")[1])

        user = get_or_create_user(
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
            last_name=callback.from_user.last_name
        )

        # 1) сначала активные, 2) если once уже деактивирован — ищем без фильтра
        reminder = get_reminder_by_id(reminder_id, user_id=user.id) or \
                   get_any_reminder_by_id(reminder_id, user_id=user.id)

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

@dp.message(Command("weeks"))
async def weeks_command(message: Message):
    """
    Показывает недельные итоги:
    1) 12.08–18.08 — 55/56 (98%)
    2) 19.08–25.08 — 50/56 (89%)
    ...
    """
    try:
        user = get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name
        )

        from .db import finalize_past_weeks, get_week_summaries
        finalize_past_weeks(user.id, tz_str=TIMEZONE)
        weeks = get_week_summaries(user.id, tz_str=TIMEZONE)

        if not weeks:
            await message.answer("🗂 Пока нет недельных итогов. Дай хотя бы одной неделе завершиться 😉")
            return

        lines = ["🗂 Недельные итоги:\n"]
        for i, w in enumerate(weeks, start=1):
            lines.append(f"{i}) {w['range']} — {w['done']}/{w['planned']} ({w['pct']}%)")

        await message.answer("\n".join(lines))
    except Exception as e:
        logger.exception("Error in /weeks: %s", e)
        await message.answer("❌ Ошибка при получении недельных итогов.")


@dp.message()
async def handle_unknown_command(message: Message):
    await message.answer(
        "🤔 Не понимаю эту команду.\n\n"
        "Команды:\n"
        "• /add HH:MM текст — разовое\n"
        "• /everyday HH:MM текст — ежедневное\n"
        "• /days пн,ср,пт HH:MM текст — по дням\n"
        "• /rename ID новый_текст — переименовать\n"
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
