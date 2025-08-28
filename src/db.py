from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
from typing import Generator
import logging

from .models import Base, User, Reminder, CompletedWorkout
from .config import DATABASE_URL

logger = logging.getLogger(__name__)

# DB
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialized")


@contextmanager
def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_or_create_user(telegram_id: int, username: str = None,
                       first_name: str = None, last_name: str = None) -> User:
    with get_db() as db:
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if not user:
            user = User(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            logger.info(f"Created new user: {telegram_id}")
        else:
            if user.username != username or user.first_name != first_name or user.last_name != last_name:
                user.username = username
                user.first_name = first_name
                user.last_name = last_name
                db.commit()
        return user


def create_reminder(user_id: int, reminder_type: str, time: str,
                    text: str, days: str = None, job_id: str = None) -> Reminder:
    with get_db() as db:
        reminder = Reminder(
            user_id=user_id,
            reminder_type=reminder_type,
            time=time,
            days=days,
            text=text,
            job_id=job_id
        )
        db.add(reminder)
        db.commit()
        db.refresh(reminder)
        logger.info(f"Created reminder {reminder.id} for user {user_id}")
        return reminder


def get_active_reminders(user_id: int = None):
    with get_db() as db:
        q = db.query(Reminder).filter(Reminder.is_active == True)
        if user_id:
            q = q.filter(Reminder.user_id == user_id)
        return q.all()


def get_reminder_by_id(reminder_id: int, user_id: int = None):
    """Возвращает ТОЛЬКО активное напоминание (чтобы нельзя было удалять повторно)."""
    with get_db() as db:
        q = db.query(Reminder).filter(
            Reminder.id == reminder_id,
            Reminder.is_active == True
        )
        if user_id:
            q = q.filter(Reminder.user_id == user_id)
        return q.first()


def delete_reminder(reminder_id: int, user_id: int = None) -> bool:
    """Жёсткое удаление записи из БД."""
    with get_db() as db:
        q = db.query(Reminder).filter(Reminder.id == reminder_id)
        if user_id:
            q = q.filter(Reminder.user_id == user_id)
        r = q.first()
        if not r:
            return False
        db.delete(r)
        db.commit()
        logger.info(f"Hard-deleted reminder {reminder_id}")
        return True


def rename_reminder(reminder_id: int, user_id: int, new_text: str) -> bool:
    """Переименовать активное напоминание."""
    with get_db() as db:
        r = db.query(Reminder).filter(
            Reminder.id == reminder_id,
            Reminder.user_id == user_id,
            Reminder.is_active == True
        ).first()
        if not r:
            return False
        r.text = new_text
        db.commit()
        return True


def mark_workout_completed(reminder_id: int, user_id: int, text: str = None):
    with get_db() as db:
        completed = CompletedWorkout(
            user_id=user_id,
            reminder_id=reminder_id,
            text=text
        )
        db.add(completed)
        db.commit()
        logger.info(f"Marked workout completed for user {user_id}, reminder {reminder_id}")


def get_user_stats(user_id: int, days: int = 7):
    from datetime import datetime, timedelta
    with get_db() as db:
        start_date = datetime.utcnow() - timedelta(days=days)
        completed_workouts = db.query(CompletedWorkout).filter(
            CompletedWorkout.user_id == user_id,
            CompletedWorkout.completed_at >= start_date
        ).count()
        total_reminders = db.query(Reminder).filter(
            Reminder.user_id == user_id,
            Reminder.is_active == True
        ).count()
        return {
            'completed_workouts': completed_workouts,
            'total_reminders': total_reminders,
            'days': days
        }

# --- ВНИЗУ db.py рядом с другими функциями ---

def set_reminder_inactive(reminder_id: int) -> None:
    """Пометить напоминание неактивным (для одноразовых после отправки)."""
    with get_db() as db:
        r = db.query(Reminder).filter(Reminder.id == reminder_id).first()
        if r and r.is_active:
            r.is_active = False
            db.commit()

def get_any_reminder_by_id(reminder_id: int, user_id: int = None):
    """Ищет напоминание без фильтра is_active (нужно для колбэка после once)."""
    with get_db() as db:
        q = db.query(Reminder).filter(Reminder.id == reminder_id)
        if user_id:
            q = q.filter(Reminder.user_id == user_id)
        return q.first()
