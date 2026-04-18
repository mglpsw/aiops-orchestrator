"""SQLite database models and async engine setup."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, String, Text, Integer, Boolean, JSON, Float
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


class TaskRecord(Base):
    __tablename__ = "tasks"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    status = Column(String, default="pending", nullable=False, index=True)
    message = Column(Text, nullable=False)
    user_id = Column(String, default="webai-user")
    risk_level = Column(String, nullable=True)
    plan_json = Column(JSON, nullable=True)
    result_json = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    requires_approval = Column(Boolean, default=False)
    approved_by = Column(String, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    context_json = Column(JSON, nullable=True)


class AuditRecord(Base):
    __tablename__ = "audit_log"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    task_id = Column(String, nullable=True, index=True)
    event_type = Column(String, nullable=False, index=True)
    actor = Column(String, default="system")
    details_json = Column(JSON, nullable=True)
    risk_level = Column(String, nullable=True)


class ProviderCallRecord(Base):
    __tablename__ = "provider_calls"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    task_id = Column(String, nullable=True, index=True)
    provider = Column(String, nullable=False, index=True)
    role = Column(String, nullable=False)  # classify, plan, review, execute, summarize
    model = Column(String, nullable=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    latency_ms = Column(Float, nullable=True)
    success = Column(Boolean, default=True)
    error = Column(Text, nullable=True)


class ExecutionRecord(Base):
    __tablename__ = "executions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    task_id = Column(String, nullable=False, index=True)
    step_order = Column(Integer, nullable=False)
    tool = Column(String, nullable=False)
    command = Column(Text, nullable=True)
    cwd = Column(String, nullable=True)
    stdout = Column(Text, nullable=True)
    stderr = Column(Text, nullable=True)
    exit_code = Column(Integer, nullable=True)
    duration_ms = Column(Float, nullable=True)
    dry_run = Column(Boolean, default=False)
    backup_path = Column(String, nullable=True)
    rollback_done = Column(Boolean, default=False)


# --- Engine and Session ---

_engine = None
_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database_url, echo=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def init_db():
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncSession:
    factory = get_session_factory()
    async with factory() as session:
        yield session
