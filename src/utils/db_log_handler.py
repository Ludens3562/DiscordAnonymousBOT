import logging
from database import SessionLocal
from models import BotLog


class DatabaseLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()

    def emit(self, record):
        db = SessionLocal()
        try:
            log_entry = BotLog(
                logger_name=record.name,
                level=record.levelname,
                message=self.format(record)
            )
            db.add(log_entry)
            db.commit()
        except Exception:
            db.rollback()
            # ここでエラーを発生させると無限ループになる可能性があるため、何もしない
        finally:
            db.close()
