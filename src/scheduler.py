from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from datetime import datetime
import pytz
import logging

from .config import TIMEZONE
from .db import get_active_reminders, set_reminder_inactive

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(
    jobstores={'default': MemoryJobStore()},
    timezone=pytz.timezone(TIMEZONE)
)

# Bot instance to send messages
bot_instance = None


def set_bot_instance(bot):
    """Store aiogram Bot instance for sending messages."""
    global bot_instance
    bot_instance = bot


async def send_reminder(user_telegram_id: int, reminder_id: int, text: str,
                        reminder_type: str | None = None):
    """Send message with 'Done' button. For once-reminders mark them inactive."""
    if not bot_instance:
        logger.error("Bot instance not set")
        return
    try:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Выполнено", callback_data=f"done_{reminder_id}")
        ]])

        await bot_instance.send_message(
            chat_id=user_telegram_id,
            text=f"💪 Время тренировки!\n\n{text}",
            reply_markup=kb
        )

        # ВАЖНО: одноразовые помечаем неактивными (оставляем запись для колбэка)
        if reminder_type == "once":
            set_reminder_inactive(reminder_id)

        logger.info(f"Sent reminder {reminder_id} to user {user_telegram_id}")
    except Exception as e:
        logger.error(f"Failed to send reminder {reminder_id} to user {user_telegram_id}: {e}")


def schedule_once_reminder(reminder_id: int, user_telegram_id: int,
                           time_str: str, text: str) -> str | None:
    """Plan one-time reminder for today (if time not passed)."""
    try:
        hour, minute = map(int, time_str.split(':'))
        tz = pytz.timezone(TIMEZONE)
        now = datetime.now(tz)
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            return None

        job_id = f"once_{reminder_id}_{user_telegram_id}"
        scheduler.add_job(
            send_reminder,
            trigger=DateTrigger(run_date=target),
            args=[user_telegram_id, reminder_id, text, "once"],
            id=job_id,
            replace_existing=True
        )
        logger.info(f"Scheduled once reminder {reminder_id} for {target}")
        return job_id
    except Exception as e:
        logger.error(f"Failed to schedule once reminder {reminder_id}: {e}")
        return None


def schedule_everyday_reminder(reminder_id: int, user_telegram_id: int,
                               time_str: str, text: str) -> str | None:
    """Plan daily reminder."""
    try:
        hour, minute = map(int, time_str.split(':'))
        job_id = f"everyday_{reminder_id}_{user_telegram_id}"
        scheduler.add_job(
            send_reminder,
            trigger=CronTrigger(hour=hour, minute=minute, timezone=TIMEZONE),
            args=[user_telegram_id, reminder_id, text, "everyday"],
            id=job_id,
            replace_existing=True
        )
        logger.info(f"Scheduled everyday reminder {reminder_id} at {time_str}")
        return job_id
    except Exception as e:
        logger.error(f"Failed to schedule everyday reminder {reminder_id}: {e}")
        return None


def schedule_days_reminder(reminder_id: int, user_telegram_id: int,
                           time_str: str, days_any, text: str) -> str | None:
    """
    Plan reminder for chosen weekdays.
    days_any: 'пн,ср,пт' ИЛИ list[int] where 0=пн..6=вс.
    """
    try:
        hour, minute = map(int, time_str.split(':'))

        mapping_str_to_num = {'пн': 0, 'вт': 1, 'ср': 2, 'чт': 3, 'пт': 4, 'сб': 5, 'вс': 6}
        if isinstance(days_any, str):
            days = [d.strip().lower() for d in days_any.split(',') if d.strip()]
            day_numbers = [mapping_str_to_num[d] for d in days if d in mapping_str_to_num]
        else:
            day_numbers = sorted({int(x) for x in days_any})

        if not day_numbers:
            return None

        day_of_week = ",".join(map(str, day_numbers))
        job_id = f"days_{reminder_id}_{user_telegram_id}"

        scheduler.add_job(
            send_reminder,
            trigger=CronTrigger(
                hour=hour, minute=minute, day_of_week=day_of_week, timezone=TIMEZONE
            ),
            args=[user_telegram_id, reminder_id, text, "days"],
            id=job_id,
            replace_existing=True
        )
        logger.info(f"Scheduled days reminder {reminder_id} for {day_of_week} at {time_str}")
        return job_id
    except Exception as e:
        logger.error(f"Failed to schedule days reminder {reminder_id}: {e}")
        return None


def remove_job(job_id: str):
    """Unschedule job if exists."""
    try:
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
            logger.info(f"Removed job {job_id}")
    except Exception as e:
        logger.error(f"Failed to remove job {job_id}: {e}")


def restore_reminders_from_db():
    """Reschedule all active reminders on startup (skip past 'once')."""
    try:
        reminders = get_active_reminders()
        restored = 0
        for r in reminders:
            job_id = None
            if r.reminder_type == "everyday":
                job_id = schedule_everyday_reminder(r.id, r.user.telegram_id, r.time, r.text)
            elif r.reminder_type == "days":
                job_id = schedule_days_reminder(r.id, r.user.telegram_id, r.time, r.days, r.text)

            if job_id:
                from .db import get_db
                with get_db() as dbs:
                    db_obj = dbs.query(r.__class__).filter(r.__class__.id == r.id).first()
                    if db_obj:
                        db_obj.job_id = job_id
                        dbs.commit()
                restored += 1

        logger.info(f"Restored {restored} reminders from database")
    except Exception as e:
        logger.error(f"Failed to restore reminders from database: {e}")


def start_scheduler():
    if not scheduler.running:
        scheduler.start()
        logger.info("Scheduler started")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler stopped")
