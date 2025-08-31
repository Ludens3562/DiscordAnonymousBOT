import discord
from discord.ext import commands
from sqlalchemy.orm import Session
import logging

from models import ConversionHistory
from database import get_db
from cogs.config import DEFAULT_SETTINGS, ConfigCog
from cogs.anonymous_post import AnonymousPostCog
from utils.crypto import Encryptor

logger = logging.getLogger(__name__)

encryptor = Encryptor()


class ConversionView(discord.ui.View):
    def __init__(self, author: discord.User, cog_instance: "ConversionCog", original_message: discord.Message, timeout: float):
        super().__init__(timeout=timeout)
        self.author = author
        self.cog_instance = cog_instance
        self.original_message = original_message
        self.confirmation_message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("このボタンはメッセージの投稿者のみが使用できます。", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if self.confirmation_message:
            try:
                await self.confirmation_message.delete()
            except discord.NotFound:
                pass  # Already deleted
        await self.cog_instance.record_conversion_history(
            self.original_message, None, "timeout"
        )

    @discord.ui.button(label="変換する", style=discord.ButtonStyle.primary, emoji="🔄")
    async def convert(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            await self.cog_instance.convert_message(interaction, self.original_message)
            if self.confirmation_message:
                await self.confirmation_message.delete()
        except ValueError as e:
            error_messages = {
                "Banned user": "❌ あなたは匿名チャットからBANされています。",
                "Rate limit exceeded": "❌ レート制限に達しました。しばらくしてから再試行してください。",
                "NG word detected": "❌ メッセージに不適切な単語が含まれているため、変換をブロックしました。",
            }
            message = error_messages.get(str(e), "❌ メッセージが長すぎるか、その他の理由で変換できませんでした。")
            await interaction.followup.send(message, ephemeral=True)
        except Exception as e:
            logger.error(f"Error during conversion: {e}", exc_info=True)
            await interaction.followup.send("変換中に予期せぬエラーが発生しました。", ephemeral=True)
        self.stop()

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if self.confirmation_message:
            await self.confirmation_message.delete()
        await self.cog_instance.record_conversion_history(
            self.original_message, None, "cancelled"
        )
        self.stop()


class ConversionCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.anonymous_post_cog: AnonymousPostCog = self.bot.get_cog("AnonymousPostCog")

    async def get_guild_settings(self, db: Session, guild_id: str) -> dict:
        config_cog: "ConfigCog" = self.bot.get_cog("ConfigCog")
        if not config_cog:
            return DEFAULT_SETTINGS
        return await config_cog.get_guild_settings(db, guild_id)

    async def record_conversion_history(self, original_message: discord.Message, converted_message_id: int | None, status: str):
        db: Session = next(get_db())
        try:
            config_cog: "ConfigCog" = self.bot.get_cog("ConfigCog")
            settings = await config_cog.get_guild_settings(db, str(original_message.guild.id))
            guild_salt = settings.get('guild_salt', '')
            
            user_id = str(original_message.author.id)
            user_id_signature = encryptor.sign_persistent_user_id(user_id, guild_salt)

            history_entry = ConversionHistory(
                guild_id=str(original_message.guild.id),
                user_id_signature=user_id_signature,
                original_message_id=str(original_message.id),
                converted_message_id=str(converted_message_id) if converted_message_id else None,
                channel_id=str(original_message.channel.id),
                thread_id=str(original_message.channel.id) if isinstance(original_message.channel, discord.Thread) else None,
                status=status,
            )
            db.add(history_entry)
            db.commit()
        except Exception as e:
            logger.error(f"Failed to record conversion history: {e}")
            db.rollback()
        finally:
            db.close()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        
        # テキストチャンネルか、フォーラムチャンネル内のスレッドでのみ動作
        is_text_channel = isinstance(message.channel, discord.TextChannel)
        is_thread_in_forum = isinstance(message.channel, discord.Thread) and isinstance(message.channel.parent, discord.ForumChannel)
        
        if not is_text_channel and not is_thread_in_forum:
            return

        # Ensure AnonymousPostCog is ready
        if not self.anonymous_post_cog:
            self.anonymous_post_cog = self.bot.get_cog("AnonymousPostCog")
            if not self.anonymous_post_cog:
                logger.warning("AnonymousPostCog not found, conversion feature will be disabled.")
                return

        db: Session = next(get_db())
        try:
            settings = await self.get_guild_settings(db, str(message.guild.id))
            conversion_enabled = settings.get("conversion_enabled", False)
            if not conversion_enabled:
                return

            conversion_channels = settings.get("conversion_channels", [])
            timeout = settings.get("conversion_timeout", 30.0)

            target_channel_id = str(message.channel.id)
            # スレッドの場合、親チャンネル（フォーラム）が変換対象かチェック
            if isinstance(message.channel, discord.Thread):
                target_channel_id = str(message.channel.parent_id)

            if target_channel_id in conversion_channels:
                view = ConversionView(message.author, self, message, timeout)
                confirmation_message = await message.reply(
                    "このメッセージを匿名投稿に変換しますか？",
                    view=view,
                    delete_after=timeout
                )
                view.confirmation_message = confirmation_message
        finally:
            db.close()

    async def convert_message(self, interaction: discord.Interaction, original_message: discord.Message):
        db: Session = next(get_db())
        try:
            new_post = await self.anonymous_post_cog._post_message(
                db=db,
                guild_id=str(original_message.guild.id),
                user=original_message.author,
                channel=original_message.channel,
                content=original_message.content,
                attachments=original_message.attachments,
                is_converted=True,
                original_message_id=str(original_message.id)
            )
            db.commit()

            # 履歴を記録
            await self.record_conversion_history(original_message, int(new_post.message_id), "converted")

            # 元のメッセージを削除
            try:
                await original_message.delete()
            except discord.NotFound:
                pass  # Already deleted

        except ValueError:
            # ValueErrorはそのまま呼び出し元に伝播させる
            raise
        except Exception as e:
            logger.error(f"Failed to convert message: {e}", exc_info=True)
            db.rollback()
            raise
        finally:
            db.close()


async def setup(bot: commands.Bot):
    await bot.add_cog(ConversionCog(bot))
