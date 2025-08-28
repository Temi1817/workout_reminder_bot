from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False)
    username = Column(String(255))
    first_name = Column(String(255))
    last_name = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    reminders = relationship("Reminder", back_populates="user")
    completed_workouts = relationship("CompletedWorkout", back_populates="user")


class Reminder(Base):
    __tablename__ = "reminders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reminder_type = Column(String(50), nullable=False)  # once, everyday, days
    time = Column(String(5), nullable=False)            # HH:MM
    days = Column(String(20))                           # 'пн,ср,пт' для type='days'
    text = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    job_id = Column(String(100))                        # APScheduler job ID

    user = relationship("User", back_populates="reminders")


class CompletedWorkout(Base):
    __tablename__ = "completed_workouts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reminder_id = Column(Integer, ForeignKey("reminders.id"))
    completed_at = Column(DateTime, default=datetime.utcnow)
    text = Column(Text)

    user = relationship("User", back_populates="completed_workouts")
    reminder = relationship("Reminder")
