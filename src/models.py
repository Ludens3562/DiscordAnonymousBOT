from sqlalchemy import (
    Column,
    BigInteger,
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
    JSON,
    Index,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func


Base = declarative_base()


class AnonymousPost(Base):
    __tablename__ = 'anonymous_posts'

    id = Column(BigInteger, primary_key=True)
    guild_id = Column(String(30), nullable=False)
    user_id_encrypted = Column(Text, nullable=False)
    encryption_salt = Column(Text, nullable=False)
    global_user_signature = Column(Text, nullable=False)
    key_version = Column(Integer, nullable=False)
    search_tag = Column(Text, nullable=False)
    anonymous_id = Column(String(64), nullable=False)
    message_id = Column(String(64), nullable=False)
    channel_id = Column(String(30), nullable=False)
    thread_id = Column(String(30))
    content = Column(Text, nullable=False)
    attachment_urls = Column(JSON, default=[])
    is_converted = Column(Boolean, default=False)
    original_message_id = Column(String(64))
    created_at = Column(DateTime(timezone=True), nullable=False)
    deleted_at = Column(DateTime(timezone=True))
    deleted_by = Column(String(512))

    __table_args__ = (
        Index('idx_anonymous_posts_guild_channel', 'guild_id', 'channel_id'),
        Index('idx_anonymous_posts_anon_id', 'anonymous_id'),
    )


class ConversionHistory(Base):
    __tablename__ = 'conversion_history'

    id = Column(BigInteger, primary_key=True)
    guild_id = Column(String(30), nullable=False)
    user_id_encrypted = Column(String(512), nullable=False)
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
    log_salt_type = Column(String(50))  # e.g., 'ban', 'user_posts'
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
    success = Column(Boolean, nullable=False, default=True)
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
    rate_limit_key = Column(Text, index=True, nullable=False)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)


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
    match_type = Column(String(20), nullable=False, server_default='partial')  # partial, exact, regex
    action = Column(String(20), nullable=False, default='block')  # e.g., block, warn, delete
    added_by = Column(String(30))
    added_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint('guild_id', 'word', 'match_type', name='uq_ng_word_guild_word_type'),
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


class GlobalChatRoom(Base):
    __tablename__ = 'global_chat_rooms'

    id = Column(BigInteger, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text)
    created_by = Column(String(30), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class GlobalChatChannel(Base):
    __tablename__ = 'global_chat_channels'

    channel_id = Column(String(30), primary_key=True)
    guild_id = Column(String(30), nullable=False)
    room_id = Column(BigInteger, nullable=False)
    webhook_url = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index('idx_gcr_guild_room', 'guild_id', 'room_id'),
    )


class GlobalChatBan(Base):
    __tablename__ = 'global_chat_bans'

    id = Column(BigInteger, primary_key=True)
    target_id = Column(String(30), nullable=False)  # guild_id or user_id
    target_type = Column(String(20), nullable=False)  # 'GUILD' or 'USER'
    room_id = Column(BigInteger)  # NULL for global ban
    reason = Column(Text)
    banned_by = Column(String(30), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index('idx_gcb_target', 'target_id', 'target_type'),
    )


class GlobalNgWord(Base):
    __tablename__ = 'global_ng_words'

    id = Column(BigInteger, primary_key=True)
    word = Column(String(255), nullable=False)
    match_type = Column(String(20), nullable=False, server_default='partial')  # partial, exact, regex
    added_by = Column(String(30), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint('word', 'match_type', name='uq_gng_word_type'),
    )


class GlobalChatEvents(Base):
    __tablename__ = 'global_chat_events'

    original_message_id = Column(String(64), primary_key=True)
    user_id_encrypted = Column(Text, nullable=False)
    forwarded_map = Column(JSONB, nullable=False, server_default='{}')
    status = Column(String(20), nullable=False, default='PENDING')  # e.g., PENDING, COMPLETED
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index('idx_gce_forwarded_map', 'forwarded_map', postgresql_using='gin'),
    )


class GlobalSettings(Base):
    __tablename__ = 'global_settings'

    key = Column(String(100), primary_key=True)
    value = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())