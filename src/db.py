from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
from typing import Generator
import logging

from .models import Base, User, Reminder, CompletedWorkout, WeeklySummary
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

def _planned_for_day(user_id: int, day_local_date, tz_str: str = "Asia/Almaty") -> int:
    """План на конкретный день: active 'everyday' + active 'days' совпадающего weekday."""
    ru_days = ['пн','вт','ср','чт','пт','сб','вс']
    ru = ru_days[day_local_date.weekday()]
    with get_db() as db:
        rems = db.query(Reminder).filter(
            Reminder.user_id == user_id,
            Reminder.is_active == True
        ).all()
    y = 0
    for r in rems:
        if r.reminder_type == "everyday":
            y += 1
        elif r.reminder_type == "days" and r.days:
            parts = [p.strip().lower() for p in r.days.split(",") if p.strip()]
            if ru in parts:
                y += 1
    return y


def finalize_past_weeks(user_id: int, tz_str: str = "Asia/Almaty") -> int:
    """
    Сводит недели (пн–вс) ТОЛЬКО с момента первой активности пользователя.
    Не сохраняет пустые недели 0/0.
    Возвращает, сколько итогов создано.
    """
    from datetime import datetime, timedelta
    import pytz

    tz = pytz.timezone(tz_str)
    today = datetime.now(tz).date()
    this_monday = today - timedelta(days=today.weekday())  # понедельник текущей недели

    # ---- 1) Находим дату старта пользователя ----
    with get_db() as db:
        # дата создания юзера (UTC)
        user = db.query(User).filter(User.id == user_id).first()

        # самое раннее напоминание (UTC)
        first_rem = db.query(Reminder)\
            .filter(Reminder.user_id == user_id)\
            .order_by(Reminder.created_at.asc())\
            .first()

        # самое раннее выполнение (UTC)
        first_done = db.query(CompletedWorkout)\
            .filter(CompletedWorkout.user_id == user_id)\
            .order_by(CompletedWorkout.completed_at.asc())\
            .first()

    candidates = []
    if user and user.created_at:
        # created_at в UTC -> локальная дата
        candidates.append(user.created_at.replace(tzinfo=pytz.utc).astimezone(tz).date())
    if first_rem and first_rem.created_at:
        candidates.append(first_rem.created_at.replace(tzinfo=pytz.utc).astimezone(tz).date())
    if first_done and first_done.completed_at:
        candidates.append(first_done.completed_at.replace(tzinfo=pytz.utc).astimezone(tz).date())

    if not candidates:
        # никакой активности ещё не было — нечего сводить
        return 0

    start_date = min(candidates)
    start_monday = start_date - timedelta(days=start_date.weekday())  # округляем влево до понедельника

    # ---- 2) Узнаём какие недели уже сохранены ----
    with get_db() as db:
        existing = db.query(WeeklySummary).filter(WeeklySummary.user_id == user_id).all()

    have = set()
    for w in existing:
        ws_local = w.week_start.replace(tzinfo=pytz.utc).astimezone(tz).date()
        have.add(ws_local)  # локальная дата понедельника

    # ---- 3) Считаем и сохраняем новые недели ----
    created = 0
    cur = start_monday
    while cur < this_monday:
        if cur not in have:
            week_days = [cur + timedelta(days=i) for i in range(7)]
            week_end_date = week_days[-1]

            # неделя завершена, если её воскресенье < this_monday
            if week_end_date < this_monday:
                # границы недели в локали -> UTC
                week_start_local = datetime(cur.year, cur.month, cur.day, 0, 0, 0, tzinfo=tz)
                week_end_local   = datetime(week_end_date.year, week_end_date.month, week_end_date.day, 23, 59, 59, tzinfo=tz)
                week_start_utc = week_start_local.astimezone(pytz.utc)
                week_end_utc   = week_end_local.astimezone(pytz.utc)

                # DONE внутри этой недели
                with get_db() as db:
                    done_rows = db.query(CompletedWorkout).filter(
                        CompletedWorkout.user_id == user_id,
                        CompletedWorkout.completed_at >= week_start_utc,
                        CompletedWorkout.completed_at <= week_end_utc
                    ).all()

                # считаем done по дням (в локальной TZ)
                dm = {}
                for r in done_rows:
                    dt_local = r.completed_at.replace(tzinfo=pytz.utc).astimezone(tz)
                    dd = dt_local.date()
                    dm[dd] = dm.get(dd, 0) + 1

                planned_total, done_total = 0, 0
                for d in week_days:
                    planned_total += _planned_for_day(user_id, d, tz_str)
                    done_total += dm.get(d, 0)

                # Не сохраняем пустую неделю 0/0
                if planned_total == 0 and done_total == 0:
                    cur += timedelta(weeks=1)
                    continue

                # сохраняем
                with get_db() as db:
                    ws = WeeklySummary(
                        user_id=user_id,
                        week_start=week_start_utc,
                        week_end=week_end_utc,
                        done_total=done_total,
                        planned_total=planned_total
                    )
                    db.add(ws)
                    db.commit()
                created += 1

        cur += timedelta(weeks=1)

    return created

def get_week_summaries(user_id: int, tz_str: str = "Asia/Almaty"):
    """
    Вернёт все недельные итоги пользователя.
    Формат:
    {"range": "12.08–18.08", "done": 55, "planned": 56, "pct": 98}
    """
    import pytz
    tz = pytz.timezone(tz_str)
    with get_db() as db:
        rows = db.query(WeeklySummary)\
                 .filter(WeeklySummary.user_id == user_id)\
                 .order_by(WeeklySummary.week_start.asc())\
                 .all()
    out = []
    for w in rows:
        ws = w.week_start.replace(tzinfo=pytz.utc).astimezone(tz).date()
        we = w.week_end.replace(tzinfo=pytz.utc).astimezone(tz).date()
        pct = int(round(100 * w.done_total / w.planned_total)) if w.planned_total > 0 else 0
        out.append({
            "range": f"{ws.strftime('%d.%m')}–{we.strftime('%d.%m')}",
            "done": w.done_total,
            "planned": w.planned_total,
            "pct": pct
        })
    return out




def get_daily_7d_ratio(user_id: int, tz_str: str = "Asia/Almaty"):
    """
    Возвращает список на 7 дней (сегодня и 6 прошлых) в порядке: сегодня, вчера, ...
      [{"date":"ДД.ММ.ГГГГ","done":X,"planned":Y}, ...]
    """
    from datetime import datetime, timedelta
    import pytz

    tz = pytz.timezone(tz_str)
    now_local = datetime.now(tz)
    today0 = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    # отметки DONE за последние 7 дней
    start_local = today0 - timedelta(days=6)
    start_utc = start_local.astimezone(pytz.utc)

    with get_db() as db:
        rows = db.query(CompletedWorkout).filter(
            CompletedWorkout.user_id == user_id,
            CompletedWorkout.completed_at >= start_utc
        ).all()

    done_map = {}
    for r in rows:
        dt_local = r.completed_at.replace(tzinfo=pytz.utc).astimezone(tz)
        d = dt_local.date()
        done_map[d] = done_map.get(d, 0) + 1

    out = []
    for i in range(7):
        d = (today0 - timedelta(days=i)).date()
        out.append({
            "date": d.strftime("%d.%m.%Y"),
            "done": done_map.get(d, 0),
            "planned": _planned_for_day(user_id, d, tz_str)
        })
    return out
