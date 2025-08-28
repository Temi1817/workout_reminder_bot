from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
from typing import Generator
import logging

from .models import Base, User, Reminder, CompletedWorkout
from .config import DATABASE_URL

logger = logging.getLogger(__name__)

# Create engine
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Initialize database tables"""
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialized")


@contextmanager
def get_db() -> Generator[Session, None, None]:
    """Context manager for database sessions"""
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
    """Get existing user or create new one"""
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
            # Update user info if changed
            if user.username != username or user.first_name != first_name or user.last_name != last_name:
                user.username = username
                user.first_name = first_name
                user.last_name = last_name
                db.commit()
        
        return user


def create_reminder(user_id: int, reminder_type: str, time: str, 
                   text: str, days: str = None, job_id: str = None) -> Reminder:
    """Create new reminder"""
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
    """Get active reminders for user or all users"""
    with get_db() as db:
        query = db.query(Reminder).filter(Reminder.is_active == True)
        if user_id:
            query = query.filter(Reminder.user_id == user_id)
        return query.all()


def get_reminder_by_id(reminder_id: int, user_id: int = None):
    """Get reminder by ID, optionally filtered by user"""
    with get_db() as db:
        query = db.query(Reminder).filter(Reminder.id == reminder_id)
        if user_id:
            query = query.filter(Reminder.user_id == user_id)
        return query.first()


def delete_reminder(reminder_id: int, user_id: int = None) -> bool:
    """Delete reminder"""
    with get_db() as db:
        query = db.query(Reminder).filter(Reminder.id == reminder_id)
        if user_id:
            query = query.filter(Reminder.user_id == user_id)
        
        reminder = query.first()
        if reminder:
            reminder.is_active = False
            db.commit()
            logger.info(f"Deleted reminder {reminder_id}")
            return True
        return False


def mark_workout_completed(reminder_id: int, user_id: int, text: str = None):
    """Mark workout as completed"""
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
    """Get user workout statistics for last N days"""
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
