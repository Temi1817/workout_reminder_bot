from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from datetime import datetime, timedelta
import pytz
import logging

from .config import TIMEZONE
from .db import get_active_reminders, delete_reminder

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(
    jobstores={'default': MemoryJobStore()},
    timezone=pytz.timezone(TIMEZONE)
)

# Bot instance will be set when initializing
bot_instance = None


def set_bot_instance(bot):
    """Set bot instance for sending messages"""
    global bot_instance
    bot_instance = bot


async def send_reminder(user_telegram_id: int, reminder_id: int, text: str, 
                       reminder_type: str = None):
    """Send reminder message to user"""
    if not bot_instance:
        logger.error("Bot instance not set")
        return
    
    try:
        message = f"💪 Время тренировки!\n\n{text}"
        
        # Add completion button for reminders
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="✅ Выполнено",
                callback_data=f"done_{reminder_id}"
            )]
        ])
        
        await bot_instance.send_message(
            chat_id=user_telegram_id,
            text=message,
            reply_markup=keyboard
        )
        
        # If it's a one-time reminder, mark it as inactive
        if reminder_type == "once":
            delete_reminder(reminder_id)
            
        logger.info(f"Sent reminder {reminder_id} to user {user_telegram_id}")
        
    except Exception as e:
        logger.error(f"Failed to send reminder {reminder_id} to user {user_telegram_id}: {e}")


def schedule_once_reminder(reminder_id: int, user_telegram_id: int, 
                          time_str: str, text: str) -> str:
    """Schedule one-time reminder for today"""
    try:
        # Parse time
        hour, minute = map(int, time_str.split(':'))
        
        # Get current time in timezone
        tz = pytz.timezone(TIMEZONE)
        now = datetime.now(tz)
        
        # Create target datetime for today
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        # If time has passed for today, don't schedule
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
                              time_str: str, text: str) -> str:
    """Schedule daily reminder"""
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
                          time_str: str, days_str: str, text: str) -> str:
    """Schedule reminder for specific days of week"""
    try:
        hour, minute = map(int, time_str.split(':'))
        
        # Map Russian day names to cron day_of_week
        day_mapping = {
            'пн': 0, 'вт': 1, 'ср': 2, 'чт': 3, 'пт': 4, 'сб': 5, 'вс': 6
        }
        
        # Parse days
        days = [d.strip().lower() for d in days_str.split(',')]
        day_numbers = []
        
        for day in days:
            if day in day_mapping:
                day_numbers.append(day_mapping[day])
            else:
                logger.warning(f"Unknown day: {day}")
        
        if not day_numbers:
            return None
            
        # Convert to comma-separated string for cron
        day_of_week = ','.join(map(str, day_numbers))
        
        job_id = f"days_{reminder_id}_{user_telegram_id}"
        
        scheduler.add_job(
            send_reminder,
            trigger=CronTrigger(
                hour=hour, 
                minute=minute, 
                day_of_week=day_of_week,
                timezone=TIMEZONE
            ),
            args=[user_telegram_id, reminder_id, text, "days"],
            id=job_id,
            replace_existing=True
        )
        
        logger.info(f"Scheduled days reminder {reminder_id} for {days_str} at {time_str}")
        return job_id
        
    except Exception as e:
        logger.error(f"Failed to schedule days reminder {reminder_id}: {e}")
        return None


def remove_job(job_id: str):
    """Remove scheduled job"""
    try:
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
            logger.info(f"Removed job {job_id}")
    except Exception as e:
        logger.error(f"Failed to remove job {job_id}: {e}")


def restore_reminders_from_db():
    """Restore all active reminders from database on startup"""
    try:
        reminders = get_active_reminders()
        restored_count = 0
        
        for reminder in reminders:
            job_id = None
            
            if reminder.reminder_type == "once":
                # Skip past one-time reminders
                continue
            elif reminder.reminder_type == "everyday":
                job_id = schedule_everyday_reminder(
                    reminder.id,
                    reminder.user.telegram_id,
                    reminder.time,
                    reminder.text
                )
            elif reminder.reminder_type == "days":
                job_id = schedule_days_reminder(
                    reminder.id,
                    reminder.user.telegram_id,
                    reminder.time,
                    reminder.days,
                    reminder.text
                )
            
            if job_id:
                # Update job_id in database
                from .db import get_db
                with get_db() as db:
                    db_reminder = db.query(reminder.__class__).filter(
                        reminder.__class__.id == reminder.id
                    ).first()
                    if db_reminder:
                        db_reminder.job_id = job_id
                        db.commit()
                
                restored_count += 1
                
        logger.info(f"Restored {restored_count} reminders from database")
        
    except Exception as e:
        logger.error(f"Failed to restore reminders from database: {e}")


def start_scheduler():
    """Start the scheduler"""
    if not scheduler.running:
        scheduler.start()
        logger.info("Scheduler started")


def stop_scheduler():
    """Stop the scheduler"""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler stopped")
