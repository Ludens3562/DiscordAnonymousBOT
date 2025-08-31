import discord
import logging
import os
import io
from discord.ext import commands, tasks
from discord import app_commands
import datetime
from database import SessionLocal
from models import BotLog
from sqlalchemy import desc

logger = logging.getLogger(__name__)

class LogViewer(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cleanup_logs.start()

    def cog_unload(self):
        self.cleanup_logs.cancel()

    @tasks.loop(hours=24)
    async def cleanup_logs(self):
        db = SessionLocal()
        try:
            one_year_ago = datetime.datetime.utcnow() - datetime.timedelta(days=365)
            db.query(BotLog).filter(BotLog.created_at < one_year_ago).delete()
            db.commit()
            logger.info("Old bot logs have been deleted.")
        except Exception as e:
            logger.error(f"Error cleaning up bot logs: {e}")
            db.rollback()
        finally:
            db.close()

    @cleanup_logs.before_loop
    async def before_cleanup_logs(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="view_bot_logs", description="BOTのログを表示します。")
    @app_commands.describe(
        level="ログレベル (INFO, WARNING, ERROR, CRITICAL)",
        days="表示する日数 (1-30)",
        limit="表示する件数 (1-1000)"
    )
    async def view_bot_logs(self, interaction: discord.Interaction, level: str = None, days: int = 7, limit: int = 100):
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("このコマンドはBOTのオーナーのみが実行できます。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        db = SessionLocal()
        try:
            query = db.query(BotLog)

            if level:
                query = query.filter(BotLog.level == level.upper())

            if days:
                start_date = datetime.datetime.utcnow() - datetime.timedelta(days=days)
                query = query.filter(BotLog.created_at >= start_date)

            query = query.order_by(desc(BotLog.created_at)).limit(limit)

            logs = query.all()

            if not logs:
                await interaction.followup.send("指定された条件のログは見つかりませんでした。", ephemeral=True)
                return

            log_content = ""
            for log in logs:
                log_content += f"[{log.created_at.strftime('%Y-%m-%d %H:%M:%S')}] [{log.level}] {log.logger_name}: {log.message}\n"

            log_file = io.BytesIO(log_content.encode('utf-8'))
            timestamp = datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')
            await interaction.followup.send(file=discord.File(log_file, filename=f"bot_logs_{timestamp}.txt"), ephemeral=True)

        finally:
            db.close()


async def setup(bot):
    await bot.add_cog(LogViewer(bot))