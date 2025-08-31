from sqlalchemy import (
    Column,
    BigInteger,
    String,
    Text,
    Boolean,
    DateTime,
    JSON,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func


Base = declarative_base()


class AnonymousPost(Base):
    __tablename__ = 'anonymous_posts'

    id = Column(BigInteger, primary_key=True)
    guild_id = Column(String(30), nullable=False)
    user_id_encrypted = Column(String(512), nullable=False)
    daily_user_id_signature = Column(String(128), nullable=False)
    search_tag = Column(String(128), nullable=False)
    anonymous_id = Column(String(64), nullable=False)
    message_id = Column(String(64), nullable=False)
    channel_id = Column(String(30), nullable=False)
    thread_id = Column(String(30))
    content = Column(Text, nullable=False)
    attachment_urls = Column(JSON, default=[])
    is_converted = Column(Boolean, default=False)
    original_message_id = Column(String(64))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    deleted_at = Column(DateTime(timezone=True))
    deleted_by = Column(String(30))

    __table_args__ = (
        Index('idx_anonymous_posts_guild_channel', 'guild_id', 'channel_id'),
        Index('idx_anonymous_posts_anon_id', 'anonymous_id'),
    )


class ConversionHistory(Base):
    __tablename__ = 'conversion_history'

    id = Column(BigInteger, primary_key=True)
    guild_id = Column(String(30), nullable=False)
    user_id_signature = Column(String(128), nullable=False)
    original_message_id = Column(String(64), nullable=False)
    converted_message_id = Column(String(64))
    channel_id = Column(String(30), nullable=False)
    thread_id = Column(String(30))
    status = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index('idx_conversion_history_guild', 'guild_id'),
    )


class AdminCommandLog(Base):
    __tablename__ = 'admin_command_logs'

    id = Column(BigInteger, primary_key=True)
    guild_id = Column(String(30), nullable=False)
    command_name = Column(String(100), nullable=False)
    executed_by = Column(String(64), nullable=False)
    target_user_id = Column(String(512))
    channel_id = Column(String(64))
    params = Column(JSON)
    success = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index('idx_admin_logs_guild_time', 'guild_id', 'created_at', postgresql_using='btree', postgresql_ops={'created_at': 'DESC'}),
        Index('idx_admin_logs_executed_by', 'executed_by'),
    )


class UserCommandLog(Base):
    __tablename__ = 'user_command_logs'

    id = Column(BigInteger, primary_key=True)
    guild_id = Column(String(30), nullable=False)
    command_name = Column(String(100), nullable=False)
    executed_by_signature = Column(String(128), nullable=False)
    params = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index('idx_user_logs_guild_time', 'guild_id', 'created_at', postgresql_using='btree', postgresql_ops={'created_at': 'DESC'}),
    )


class BulkDeleteHistory(Base):
    __tablename__ = 'bulk_delete_history'

    id = Column(BigInteger, primary_key=True)
    guild_id = Column(String(30))
    executed_by = Column(String(30), nullable=False)
    target_user_signature = Column(String(128))
    target_type = Column(String(20), nullable=False)
    scope = Column(String(50), nullable=False)
    conditions = Column(JSON, nullable=False)
    deleted_count = Column(BigInteger, nullable=False, default=0)
    dry_run = Column(Boolean, default=False)
    execution_time = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index('idx_bulk_delete_guild_time', 'guild_id', 'execution_time', postgresql_using='btree', postgresql_ops={'execution_time': 'DESC'}),
    )


class AnonIdMapping(Base):
    __tablename__ = 'anon_id_mappings'

    id = Column(BigInteger, primary_key=True)
    guild_id = Column(String(30), nullable=False)
    channel_or_thread_id = Column(String(30), nullable=False)
    user_id_signature = Column(String(128), nullable=False)
    anon_id = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint('guild_id', 'channel_or_thread_id', 'user_id_signature', name='uq_anon_mapping_scope_user'),
    )


class AnonymousThread(Base):
    __tablename__ = 'anonymous_threads'

    id = Column(BigInteger, primary_key=True)
    guild_id = Column(String(30), nullable=False)
    thread_discord_id = Column(String(64), nullable=False)
    board = Column(String(100), nullable=False)
    title = Column(String(200), nullable=False)
    created_by_encrypted = Column(String(512), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index('idx_threads_guild_board', 'guild_id', 'board'),
    )


class GuildBannedUser(Base):
    __tablename__ = 'guild_banned_users'

    guild_id = Column(String(30), primary_key=True)
    user_id = Column(String(30), primary_key=True)
    banned_by = Column(String(30))
    banned_at = Column(DateTime(timezone=True), server_default=func.now())


class BotBannedUser(Base):
    __tablename__ = 'bot_banned_users'

    user_id = Column(String(30), primary_key=True)
    banned_by = Column(String(30))
    banned_at = Column(DateTime(timezone=True), server_default=func.now())


class GuildSettings(Base):
    __tablename__ = 'guild_settings'

    guild_id = Column(String(30), primary_key=True)
    settings = Column(JSON, nullable=False)  # JSONB in PostgreSQL
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RateLimit(Base):
    __tablename__ = 'rate_limits'

    id = Column(BigInteger, primary_key=True)
    guild_id = Column(String(30), nullable=False)
    user_id_signature = Column(String(128), nullable=False)
    command_name = Column(String(100), nullable=False)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index('idx_rate_limits_guild_user_command', 'guild_id', 'user_id_signature', 'command_name'),
    )


class BatchDeleteJob(Base):
    __tablename__ = 'batch_delete_jobs'

    id = Column(BigInteger, primary_key=True)
    guild_id = Column(String(30), nullable=False)
    status = Column(String(20), nullable=False, default='pending')  # e.g., pending, running, completed, failed
    conditions = Column(JSON, nullable=False)
    created_by = Column(String(30), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    error_message = Column(Text)


class ConfigHistory(Base):
    __tablename__ = 'config_history'

    id = Column(BigInteger, primary_key=True)
    guild_id = Column(String(30), nullable=False)
    key = Column(String(100), nullable=False)
    old_value = Column(JSON)
    new_value = Column(JSON)
    changed_by = Column(String(30), nullable=False)
    changed_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index('idx_config_history_guild_key', 'guild_id', 'key'),
    )


class NgWord(Base):
    __tablename__ = 'ng_words'

    id = Column(BigInteger, primary_key=True)
    guild_id = Column(String(30), nullable=False)
    word = Column(String(255), nullable=False)
    action = Column(String(20), nullable=False, default='block')  # e.g., block, warn, delete
    added_by = Column(String(30))
    added_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint('guild_id', 'word', name='uq_ng_word_guild_word'),
    )




class BotLog(Base):
    __tablename__ = 'bot_logs'

    id = Column(BigInteger, primary_key=True)
    logger_name = Column(String(255))
    level = Column(String(50))
    message = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index('idx_bot_logs_level', 'level'),
        Index('idx_bot_logs_created_at', 'created_at'),
    )