import os
import discord
import logging
from discord.ext import commands
from utils.log_utils import setup_logging
from dotenv import load_dotenv

# .envファイルから環境変数を読み込む
load_dotenv()

# ロガーの設定
setup_logging()
logger = logging.getLogger(__name__)

# Botの初期化
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

async def load_cogs():
    """cogsフォルダ内のCogを読み込む"""
    for filename in os.listdir('./cogs'):
        if filename.endswith('.py'):
            try:
                await bot.load_extension(f'cogs.{filename[:-3]}')
                logger.info(f'Loaded cog: {filename[:-3]}')
            except Exception as e:
                logger.error(f'Failed to load cog {filename[:-3]}: {e}')

@bot.event
async def on_ready():
    """Botが起動したときに呼び出されるイベント"""
    logger.info(f'{bot.user.name} has connected to Discord!')
    await load_cogs()
    logger.info('Bot is ready to receive commands.')

    # グローバルコマンドの同期
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} global command(s)")
    except Exception as e:
        logger.error(f"Failed to sync global commands: {e}")

    # 特定ギルドへのコマンド同期
    guild_id = os.getenv('GUILD_ID')
    if guild_id:
        try:
            guild = discord.Object(id=int(guild_id))
            synced = await bot.tree.sync(guild=guild)
            logger.info(f"Synced {len(synced)} command(s) to guild {guild_id}")
        except Exception as e:
            logger.error(f"Failed to sync commands to guild {guild_id}: {e}")

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
