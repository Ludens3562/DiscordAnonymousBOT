import os
import datetime
import shutil
import logging
import pytz
from logging.handlers import TimedRotatingFileHandler
from .db_log_handler import DatabaseLogHandler

logger = logging.getLogger(__name__)

LOG_DIR = 'log'
ARCHIVE_DIR = os.path.join(LOG_DIR, 'archive')
NLOG_FILE = os.path.join(LOG_DIR, 'NLOG.log')
ELOG_FILE = os.path.join(LOG_DIR, 'ELOG.log')

class JSTFormatter(logging.Formatter):
    """タイムゾーンをJSTに設定したFormatter"""
    def formatTime(self, record, datefmt=None):
        dt = datetime.datetime.fromtimestamp(record.created, pytz.timezone('Asia/Tokyo'))
        if datefmt:
            return dt.strftime(datefmt)
        else:
            return dt.isoformat()

def setup_logging():
    """ロガーの初期設定を行う"""
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)
    if not os.path.exists(ARCHIVE_DIR):
        os.makedirs(ARCHIVE_DIR)

    dt_fmt = '%Y-%m-%d %H:%M:%S'
    formatter = JSTFormatter('[{asctime}] [{levelname:<8}] {name}: {message}', dt_fmt, style='{')

    # 通常ログ (NLOG)
    nlog_handler = TimedRotatingFileHandler(
        filename=NLOG_FILE,
        when='midnight',
        backupCount=7,
        encoding='utf-8'
    )
    nlog_handler.setFormatter(formatter)
    nlog_handler.setLevel(logging.INFO)
    nlog_handler.addFilter(lambda record: record.levelno == logging.INFO)
    
    # アーカイブファイル名のカスタマイズ
    def namer(name):
        return os.path.join(ARCHIVE_DIR, datetime.date.today().strftime('%Y%m%d') + "_" + os.path.basename(name).replace(".log", "") + ".log")
    nlog_handler.namer = namer

    # エラーログ (ELOG)
    elog_handler = TimedRotatingFileHandler(
        filename=ELOG_FILE,
        when='midnight',
        backupCount=7,
        encoding='utf-8'
    )
    elog_handler.setFormatter(formatter)
    elog_handler.setLevel(logging.ERROR)
    elog_handler.namer = namer

    # DBログハンドラ
    db_handler = DatabaseLogHandler()
    db_handler.setLevel(logging.INFO)

    # ルートロガーにハンドラを追加
    logging.basicConfig(level=logging.INFO, handlers=[nlog_handler, elog_handler, db_handler])
