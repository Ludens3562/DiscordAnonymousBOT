import os
import discord
import logging
import aiohttp
from discord.ext import commands, tasks
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

from utils.log_utils import setup_logging
from dotenv import load_dotenv
from database import get_db
from models import RateLimit

# .envファイルから環境変数を読み込む
load_dotenv()

# ロガーの設定
setup_logging()
logger = logging.getLogger(__name__)

class MyBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session: aiohttp.ClientSession | None = None

    async def load_all_cogs(self):
        """cogsフォルダ内のCogを読み込む"""
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py'):
                try:
                    await self.load_extension(f'cogs.{filename[:-3]}')
                    logger.info(f'Loaded cog: {filename[:-3]}')
                except Exception as e:
                    logger.error(f'Failed to load cog {filename[:-3]}: {e}')

    async def setup_hook(self) -> None:
        """Botの非同期初期化処理"""
        self.session = aiohttp.ClientSession()
        await self.load_all_cogs()
        self.cleanup_rate_limits.start()
        
        # グローバルコマンドの同期
        try:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} global command(s)")
        except Exception as e:
            logger.error(f"Failed to sync global commands: {e}")

        # 特定ギルドへのコマンド同期
        guild_id = os.getenv('GUILD_ID')
        if guild_id:
            try:
                guild = discord.Object(id=int(guild_id))
                synced = await self.tree.sync(guild=guild)
                logger.info(f"Synced {len(synced)} command(s) to guild {guild_id}")
            except Exception as e:
                logger.error(f"Failed to sync commands to guild {guild_id}: {e}")

    async def close(self):
        """Bot終了時の処理"""
        await super().close()
        if self.session:
            await self.session.close()

    @tasks.loop(hours=24)
    async def cleanup_rate_limits(self):
        """古いレート制限データを削除する"""
        db: Session = next(get_db())
        try:
            # 2日以上前のデータを削除
            two_days_ago = datetime.utcnow() - timedelta(days=2)
            deleted_count = db.query(RateLimit).filter(RateLimit.timestamp < two_days_ago).delete()
            db.commit()
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} old rate limit entries.")
        except Exception as e:
            logger.error(f"Error during rate limit cleanup: {e}", exc_info=True)
            db.rollback()
        finally:
            db.close()

    @cleanup_rate_limits.before_loop
    async def before_cleanup(self):
        await self.wait_until_ready()

# Botの初期化
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
bot = MyBot(command_prefix='/', intents=intents)

@bot.event
async def on_ready():
    """Botが起動したときに呼び出されるイベント"""
    logger.info(f'{bot.user.name} has connected to Discord!')
    logger.info('Bot is ready.')

@bot.event
async def on_command_error(ctx, error):
    """コマンドエラー時のイベント"""
    if isinstance(error, commands.CommandNotFound):
        return  # CommandNotFoundエラーは無視する
    # その他のエラーはログに出力
    logger.error(f"Ignoring exception in command {ctx.command}:", exc_info=error)


# Botの実行
if __name__ == "__main__":
    token = os.getenv('DISCORD_BOT_TOKEN')
    if token and token != "YOUR_DISCORD_BOT_TOKEN":
        bot.run(token)
    else:
        logger.error("Error: DISCORD_BOT_TOKEN not found or not set in .env file.")
        logger.error("Please set the DISCORD_BOT_TOKEN in your .env file.")