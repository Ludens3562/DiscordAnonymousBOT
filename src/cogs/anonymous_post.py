import logging
from datetime import datetime, timedelta

import discord
import nanoid
import pytz
from discord import app_commands, Webhook
from discord.ext import commands
from sqlalchemy.orm import Session

from cogs.config import ConfigCog
from database import get_db
from models import (
    AdminCommandLog,
    AnonIdMapping,
    AnonymousPost,
    AnonymousThread,
    BotBannedUser,
    GuildBannedUser,
    NgWord,
    RateLimit,
    UserCommandLog,
)
from utils.crypto import Encryptor

logger = logging.getLogger(__name__)

# Encryptorのインスタンス化
encryptor = Encryptor()




class AnonymousPostCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def is_banned(self, db: Session, guild_id: str, user_id: str) -> bool:
        """ユーザーがBANされているかチェックする"""
        guild_ban = db.query(GuildBannedUser).filter_by(guild_id=guild_id, user_id=user_id).first()
        if guild_ban:
            return True
        bot_ban = db.query(BotBannedUser).filter_by(user_id=user_id).first()
        if bot_ban:
            return True
        return False

    def check_rate_limit(self, db: Session, guild_id: str, user_id_signature: str, settings: dict) -> bool:
        """レート制限をチェックする"""
        count = settings.get('rate_limit_count', 3)
        window = settings.get('rate_limit_window', 60)

        if count == 0 or window == 0:
            return False

        limit_time = datetime.now() - timedelta(seconds=window)
        recent_posts = db.query(RateLimit).filter(
            RateLimit.guild_id == guild_id,
            RateLimit.user_id_signature == user_id_signature,
            RateLimit.timestamp > limit_time
        ).count()
        return recent_posts >= count

    def check_ng_words(self, db: Session, guild_id: str, content: str) -> tuple[bool, str | None]:
        """NGワードをチェックする"""
        ng_words = db.query(NgWord).filter(NgWord.guild_id == guild_id).all()
        for ng_word in ng_words:
            if ng_word.word in content:
                return True, ng_word.action
        return False, None

    async def get_webhook(self, channel: discord.TextChannel) -> Webhook:
        """チャンネルのWebhookを取得または作成する"""
        webhooks = await channel.webhooks()
        webhook = discord.utils.find(lambda wh: wh.user == self.bot.user, webhooks)
        if webhook is None:
            webhook = await channel.create_webhook(name=f"{self.bot.user.name} Webhook")
        return webhook

    async def get_or_create_anon_id(self, db: Session, guild_id: str, channel_or_thread_id: str, daily_user_id_signature: str) -> str:
        """匿名IDを取得または作成する。"""
        now_utc = datetime.now(pytz.utc)
        
        config_cog: ConfigCog = self.bot.get_cog("ConfigCog")
        settings = await config_cog.get_guild_settings(db, guild_id)
        id_rotation_days = settings.get('id_rotation_days', 1)
        
        expiration_time = now_utc - timedelta(days=id_rotation_days)

        mapping = db.query(AnonIdMapping).filter(
            AnonIdMapping.guild_id == guild_id,
            AnonIdMapping.channel_or_thread_id == channel_or_thread_id,
            AnonIdMapping.user_id_signature == daily_user_id_signature,
            AnonIdMapping.created_at >= expiration_time
        ).first()

        if mapping:
            return mapping.anon_id
        else:
            new_anon_id = nanoid.generate(size=10)
            new_mapping = AnonIdMapping(
                guild_id=guild_id,
                channel_or_thread_id=channel_or_thread_id,
                user_id_signature=daily_user_id_signature,
                anon_id=new_anon_id,
                created_at=now_utc
            )
            db.add(new_mapping)
            return new_anon_id

    async def _send_log_message(self, guild_id: str, embed: discord.Embed):
        """設定されたログチャンネルにEmbedメッセージを送信する"""
        db = next(get_db())
        try:
            config_cog: ConfigCog = self.bot.get_cog("ConfigCog")
            if not config_cog:
                return
            settings = await config_cog.get_guild_settings(db, guild_id)
            log_channel_id = settings.get('log_channel_id')
            if log_channel_id:
                channel = self.bot.get_channel(int(log_channel_id))
                if channel:
                    await channel.send(embed=embed)
        except Exception as e:
            print(f"Failed to send log message: {e}")
        finally:
            db.close()

    async def _post_message(
        self,
        db: Session,
        guild_id: str,
        user: discord.User,
        channel: discord.TextChannel,
        content: str,
        attachments: list[discord.Attachment],
        is_converted: bool = False,
        original_message_id: str | None = None
    ) -> AnonymousPost:
        """匿名メッセージを投稿する内部共通処理"""
        config_cog: ConfigCog = self.bot.get_cog("ConfigCog")
        settings = await config_cog.get_guild_settings(db, guild_id)
        guild_salt = settings['guild_salt']
        
        jst = pytz.timezone('Asia/Tokyo')
        today = datetime.now(jst).date()

        user_id = str(user.id)
        user_id_encrypted = encryptor.encrypt(user_id, guild_salt)
        
        daily_user_id_signature = encryptor.sign_daily_user_id(user_id, guild_salt, today)
        persistent_user_id_signature = encryptor.sign_persistent_user_id(user_id, guild_salt)
        search_tag = encryptor.sign_search_tag(daily_user_id_signature, user_id, guild_salt)

        # get_or_create_anon_id に渡すシグネチャを使い分ける
        signature_for_anon_id = persistent_user_id_signature if is_converted else daily_user_id_signature

        if self.is_banned(db, guild_id, user_id):
            raise ValueError("Banned user")

        if self.check_rate_limit(db, guild_id, signature_for_anon_id, settings):
            raise ValueError("Rate limit exceeded")

        is_ng, ng_action = self.check_ng_words(db, guild_id, content)
        if is_ng and ng_action == 'block':
            raise ValueError("NG word detected")

        max_length = settings.get('max_message_length', 2000)
        if len(content) > max_length:
            raise ValueError(f"Message too long ({len(content)} > {max_length})")

        channel_or_thread_id = str(channel.id)
        anon_id = await self.get_or_create_anon_id(db, guild_id, channel_or_thread_id, signature_for_anon_id)
        webhook = await self.get_webhook(channel)

        files = [await att.to_file() for att in attachments]

        webhook_message = await webhook.send(
            content=content,
            username=settings.get('anon_id_format', '匿名ユーザー_{id}').format(id=anon_id),
            files=files,
            wait=True
        )

        attachment_urls = [att.url for att in webhook_message.attachments]
        new_post = AnonymousPost(
            guild_id=guild_id,
            user_id_encrypted=user_id_encrypted,
            daily_user_id_signature=daily_user_id_signature,  # 常に日次署名を保存
            search_tag=search_tag,
            anonymous_id=anon_id,
            message_id=str(webhook_message.id),
            channel_id=str(channel.id),
            content=content,
            attachment_urls=attachment_urls,
            is_converted=is_converted,
            original_message_id=original_message_id
        )
        db.add(new_post)
        
        db.add(RateLimit(
            guild_id=guild_id,
            user_id_signature=signature_for_anon_id,  # レート制限のキーも使い分ける
            command_name='post' if not is_converted else 'convert'
        ))
        
        return new_post

    @app_commands.command(name="post", description="匿名でメッセージを投稿します。")
    @app_commands.describe(
        message="投稿するメッセージ",
        attachment1="添付ファイル1",
        attachment2="添付ファイル2",
        attachment3="添付ファイル3",
        attachment4="添付ファイル4",
        attachment5="添付ファイル5",
    )
    async def post(
        self,
        interaction: discord.Interaction,
        message: str,
        attachment1: discord.Attachment = None,
        attachment2: discord.Attachment = None,
        attachment3: discord.Attachment = None,
        attachment4: discord.Attachment = None,
        attachment5: discord.Attachment = None,
    ):
        await interaction.response.defer(ephemeral=True)
        db = next(get_db())
        try:
            attachments = [att for att in [attachment1, attachment2, attachment3, attachment4, attachment5] if att]
            
            new_post = await self._post_message(
                db=db,
                guild_id=str(interaction.guild.id),
                user=interaction.user,
                channel=interaction.channel,
                content=message,
                attachments=attachments
            )

            db.add(UserCommandLog(
                guild_id=str(interaction.guild.id),
                command_name='post',
                executed_by_signature=new_post.daily_user_id_signature,
                params={'channel_id': str(interaction.channel_id), 'message_length': len(message), 'attachments': len(attachments)}
            ))
            db.commit()

            await interaction.delete_original_response()

            log_embed = discord.Embed(title="匿名投稿", color=discord.Color.blue(), timestamp=datetime.now())
            log_embed.add_field(name="匿名ID", value=new_post.anonymous_id, inline=False)
            log_embed.add_field(name="チャンネル", value=interaction.channel.mention, inline=False)
            if new_post.attachment_urls:
                log_embed.add_field(name="添付ファイル", value="\n".join(new_post.attachment_urls), inline=False)
            await self._send_log_message(str(interaction.guild.id), log_embed)

        except ValueError as e:
            error_messages = {
                "Banned user": "あなたはこのサーバーまたはBOTからBANされています。",
                "Rate limit exceeded": "レート制限を超えました。しばらくしてから再試行してください。",
                "NG word detected": "メッセージに不適切な単語が含まれているため、投稿をブロックしました。",
            }
            message = error_messages.get(str(e), "メッセージが長すぎます。")
            await interaction.followup.send(message, ephemeral=True)
        except Exception as e:
            db.rollback()
            logger.error(f"Error in post command: {e}", exc_info=True)
            if not interaction.response.is_done():
                await interaction.followup.send("エラーが発生しました。管理者に連絡してください。", ephemeral=True)
        finally:
            db.close()

    @app_commands.command(name="reply", description="指定したメッセージに匿名で返信します。")
    @app_commands.describe(
        message_id="返信先のメッセージID",
        message="投稿するメッセージ",
        attachment1="添付ファイル1",
        attachment2="添付ファイル2",
        attachment3="添付ファイル3",
    )
    async def reply(
        self,
        interaction: discord.Interaction,
        message_id: str,
        message: str,
        attachment1: discord.Attachment = None,
        attachment2: discord.Attachment = None,
        attachment3: discord.Attachment = None,
    ):
        await interaction.response.defer(ephemeral=True)
        db = next(get_db())
        try:
            target_message = await interaction.channel.fetch_message(int(message_id))
            if not target_message:
                await interaction.followup.send("返信先のメッセージが見つかりません。", ephemeral=True)
                return

            guild_id = str(interaction.guild.id)
            user_id = str(interaction.user.id)

            config_cog: ConfigCog = self.bot.get_cog("ConfigCog")
            settings = await config_cog.get_guild_settings(db, guild_id)
            guild_salt = settings['guild_salt']
            
            jst = pytz.timezone('Asia/Tokyo')
            today = datetime.now(jst).date()

            user_id_encrypted = encryptor.encrypt(user_id, guild_salt)
            daily_user_id_signature = encryptor.sign_daily_user_id(user_id, guild_salt, today)
            search_tag = encryptor.sign_search_tag(daily_user_id_signature, user_id, guild_salt)

            if self.is_banned(db, guild_id, user_id):
                await interaction.followup.send("あなたはこのサーバーまたはBOTからBANされています。", ephemeral=True)
                return

            max_length = settings.get('max_message_length', 2000)
            if len(message) > max_length:
                await interaction.followup.send(f"メッセージが長すぎます。{max_length}文字以下にしてください。", ephemeral=True)
                return

            channel_or_thread_id = str(interaction.channel_id)
            anon_id = await self.get_or_create_anon_id(db, guild_id, channel_or_thread_id, daily_user_id_signature)
            webhook = await self.get_webhook(interaction.channel)

            attachments = [att for att in [attachment1, attachment2, attachment3] if att]
            files = [await att.to_file() for att in attachments]

            reply_to_url = f"https://discord.com/channels/{guild_id}/{interaction.channel.id}/{message_id}"
            
            target_post = db.query(AnonymousPost).filter_by(message_id=message_id).first()
            
            reply_prefix = ""
            if target_post:
                reply_prefix = f">>[{target_post.anonymous_id}]({reply_to_url})\n"
            else:
                reply_prefix = f"> [返信先]({reply_to_url})\n"

            content_with_reply = f"{reply_prefix}{message}"

            send_kwargs = {
                "content": content_with_reply,
                "username": settings.get('anon_id_format', '匿名ユーザー_{id}').format(id=anon_id),
                "files": files,
                "wait": True,
            }
            if hasattr(target_message, 'thread') and target_message.thread:
                send_kwargs["thread"] = target_message.thread

            webhook_message = await webhook.send(**send_kwargs)

            attachment_urls = [att.url for att in webhook_message.attachments]
            new_post = AnonymousPost(
                guild_id=guild_id,
                user_id_encrypted=user_id_encrypted,
                daily_user_id_signature=daily_user_id_signature,
                search_tag=search_tag,
                anonymous_id=anon_id,
                message_id=str(webhook_message.id),
                channel_id=str(interaction.channel_id),
                content=message,
                attachment_urls=attachment_urls
            )
            db.add(new_post)
            db.add(UserCommandLog(
                guild_id=guild_id,
                command_name='reply',
                executed_by_signature=daily_user_id_signature,
                params={'channel_id': str(interaction.channel.id), 'target_message_id': message_id}
            ))
            db.commit()

            await interaction.followup.send("メッセージに返信しました。", ephemeral=True)

        except discord.NotFound:
            await interaction.followup.send("返信先のメッセージが見つかりません。", ephemeral=True)
        except Exception as e:
            db.rollback()
            logger.error(f"Error in reply command: {e}", exc_info=True)
            if not interaction.response.is_done():
                await interaction.followup.send("エラーが発生しました。管理者に連絡してください。", ephemeral=True)
        finally:
            db.close()

    @app_commands.command(name="delete", description="指定した匿名投稿を削除します。")
    @app_commands.describe(message_id="削除するメッセージID")
    async def delete(self, interaction: discord.Interaction, message_id: str):
        await interaction.response.defer(ephemeral=True)
        db = next(get_db())
        try:
            guild_id = str(interaction.guild.id)
            user_id = str(interaction.user.id)

            config_cog: ConfigCog = self.bot.get_cog("ConfigCog")
            settings = await config_cog.get_guild_settings(db, guild_id)
            guild_salt = settings['guild_salt']
            
            post_to_delete = db.query(AnonymousPost).filter(
                AnonymousPost.guild_id == guild_id,
                AnonymousPost.message_id == message_id,
                AnonymousPost.deleted_at.is_(None)
            ).first()

            if not post_to_delete:
                await interaction.followup.send("削除対象の投稿が見つからないか、既に削除されています。", ephemeral=True)
                return

            jst = pytz.timezone('Asia/Tokyo')
            post_date = post_to_delete.created_at.astimezone(jst).date()
            
            daily_user_id_signature_check = encryptor.sign_daily_user_id(user_id, guild_salt, post_date)

            is_author = post_to_delete.daily_user_id_signature == daily_user_id_signature_check
            is_admin = interaction.user.guild_permissions.manage_guild

            if not is_author and not is_admin:
                await interaction.followup.send("この投稿を削除する権限がありません。", ephemeral=True)
                return

            try:
                channel = self.bot.get_channel(int(post_to_delete.channel_id))
                if channel:
                    message_to_delete = await channel.fetch_message(int(post_to_delete.message_id))
                    await message_to_delete.delete()
            except discord.NotFound:
                pass
            except discord.Forbidden:
                await interaction.followup.send("メッセージを削除する権限がBOTにありません。", ephemeral=True)

            post_to_delete.deleted_at = datetime.now()

            if is_admin:
                post_to_delete.deleted_by = user_id
                db.add(AdminCommandLog(
                    guild_id=guild_id,
                    command_name='delete',
                    executed_by=user_id,
                    target_user_id=post_to_delete.user_id_encrypted,
                    params={'message_id': message_id, 'channel_id': post_to_delete.channel_id}
                ))
            else:
                post_to_delete.deleted_by = post_to_delete.user_id_encrypted
                db.add(UserCommandLog(
                    guild_id=guild_id,
                    command_name='delete',
                    executed_by_signature=post_to_delete.daily_user_id_signature,
                    params={'message_id': message_id}
                ))

            db.commit()
            await interaction.followup.send("投稿を削除しました。", ephemeral=True)

            log_embed = discord.Embed(title="匿名投稿削除", color=discord.Color.red(), timestamp=datetime.now())
            log_embed.add_field(name="匿名ID", value=post_to_delete.anonymous_id, inline=False)
            log_embed.add_field(name="実行者", value=interaction.user.mention, inline=False)
            log_embed.add_field(name="対象メッセージID", value=message_id, inline=False)
            await self._send_log_message(guild_id, log_embed)

        except Exception as e:
            db.rollback()
            logger.error(f"Error in delete command: {e}", exc_info=True)
            if not interaction.response.is_done():
                await interaction.followup.send("エラーが発生しました。管理者に連絡してください。", ephemeral=True)
        finally:
            db.close()

    @app_commands.command(name="th", description="匿名でスレッドを作成します。")
    @app_commands.describe(
        board="スレッドを立てるボード名",
        title="スレッドのタイトル",
        content="最初のメッセージ"
    )
    async def thread(self, interaction: discord.Interaction, board: str, title: str, content: str):
        await interaction.response.defer(ephemeral=True)
        db = next(get_db())
        try:
            guild_id = str(interaction.guild.id)
            user_id = str(interaction.user.id)

            config_cog: ConfigCog = self.bot.get_cog("ConfigCog")
            settings = await config_cog.get_guild_settings(db, guild_id)
            guild_salt = settings['guild_salt']
            
            jst = pytz.timezone('Asia/Tokyo')
            today = datetime.now(jst).date()

            user_id_encrypted = encryptor.encrypt(user_id, guild_salt)
            daily_user_id_signature = encryptor.sign_daily_user_id(user_id, guild_salt, today)
            search_tag = encryptor.sign_search_tag(daily_user_id_signature, user_id, guild_salt)

            if self.is_banned(db, guild_id, user_id):
                await interaction.followup.send("あなたはこのサーバーまたはBOTからBANされています。", ephemeral=True)
                return

            if self.check_rate_limit(db, guild_id, daily_user_id_signature, settings):
                await interaction.followup.send("レート制限を超えました。しばらくしてから再試行してください。", ephemeral=True)
                return

            is_ng, ng_action = self.check_ng_words(db, guild_id, title + "\n" + content)
            if is_ng and ng_action == 'block':
                await interaction.followup.send("タイトルまたはメッセージに不適切な単語が含まれているため、スレッドを作成できません。", ephemeral=True)
                return

            if not isinstance(interaction.channel, discord.TextChannel):
                await interaction.followup.send("このコマンドはテキストチャンネルでのみ使用できます。", ephemeral=True)
                return

            thread = await interaction.channel.create_thread(name=title, type=discord.ChannelType.public_thread)
            anon_id = await self.get_or_create_anon_id(db, guild_id, str(thread.id), daily_user_id_signature)
            webhook = await self.get_webhook(thread)

            webhook_message = await webhook.send(
                content=content,
                username=settings.get('anon_id_format', '匿名ユーザー_{id}').format(id=anon_id),
                wait=True
            )

            new_thread_db = AnonymousThread(
                guild_id=guild_id,
                thread_discord_id=str(thread.id),
                board=board,
                title=title,
                created_by_encrypted=user_id_encrypted
            )
            db.add(new_thread_db)

            new_post = AnonymousPost(
                guild_id=guild_id,
                user_id_encrypted=user_id_encrypted,
                daily_user_id_signature=daily_user_id_signature,
                search_tag=search_tag,
                anonymous_id=anon_id,
                message_id=str(webhook_message.id),
                channel_id=str(interaction.channel_id),
                thread_id=str(thread.id),
                content=content,
                attachment_urls=[]
            )
            db.add(new_post)

            db.add(RateLimit(guild_id=guild_id, user_id_signature=daily_user_id_signature, command_name='thread'))
            db.commit()

            await interaction.followup.send(f"スレッド '{title}' を作成しました。", ephemeral=True)

            log_embed = discord.Embed(title="匿名スレッド作成", color=discord.Color.green(), timestamp=datetime.now())
            log_embed.add_field(name="匿名ID", value=anon_id, inline=False)
            log_embed.add_field(name="スレッド", value=thread.mention, inline=False)
            log_embed.add_field(name="タイトル", value=title, inline=False)
            await self._send_log_message(guild_id, log_embed)

        except Exception as e:
            db.rollback()
            logger.error(f"Error in thread command: {e}", exc_info=True)
            if not interaction.response.is_done():
                await interaction.followup.send("スレッド作成中にエラーが発生しました。管理者に連絡してください。", ephemeral=True)
        finally:
            db.close()


async def setup(bot: commands.Bot):
    await bot.add_cog(AnonymousPostCog(bot))
