import logging
import asyncio
import re
import os
import base64
from datetime import datetime, timedelta

import discord
import nanoid
import pytz
from discord import app_commands, Webhook
from discord.ext import commands
from sqlalchemy.orm import Session
from sqlalchemy import func

from cogs.config import ConfigCog
from database import get_db
from cogs.global_config import GlobalConfigCog
from models import (
    AnonIdMapping, AnonymousPost, AnonymousThread,
    BotBannedUser, GuildBannedUser, NgWord, RateLimit, UserCommandLog,
    GlobalChatChannel, GlobalNgWord, GlobalChatBan, GlobalChatEvents
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

    async def check_rate_limit(self, db: Session, user_id: str, guild_salt: str, settings: dict, channel_id: str) -> tuple[bool, str]:
        """
        ローカルおよびグローバル（該当する場合）のレート制限をチェックする。
        戻り値: (レート制限違反か, メッセージ)
        """
        now = datetime.now(pytz.utc)
        
        # 1. ローカルレート制限のチェック
        local_count_limit = settings.get('rate_limit_count', 3)
        local_window = settings.get('rate_limit_window', 60)
        
        if local_count_limit > 0 and local_window > 0:
            limit_time = now - timedelta(seconds=local_window)
            local_rate_limit_key = encryptor.generate_rate_limit_key(user_id, guild_salt, now.date())
            
            recent_local_posts_count = db.query(func.count(RateLimit.id)).filter(
                RateLimit.rate_limit_key == local_rate_limit_key,
                RateLimit.timestamp > limit_time
            ).scalar()

            if recent_local_posts_count >= local_count_limit:
                # 次の投稿が可能になるまでの時間を計算
                oldest_post_in_window = db.query(RateLimit).filter(
                    RateLimit.rate_limit_key == local_rate_limit_key,
                    RateLimit.timestamp > limit_time
                ).order_by(RateLimit.timestamp.asc()).limit(1).first()
                
                if oldest_post_in_window:
                    wait_time = (oldest_post_in_window.timestamp + timedelta(seconds=local_window)) - now
                    wait_seconds = max(int(wait_time.total_seconds()), 1)
                    return True, f"❌ レート制限に達しました。あと {wait_seconds} 秒お待ちください。"
                else:
                    return True, "❌ レート制限に達しました。しばらくしてから再試行してください。"

        # 2. グローバルレート制限のチェック (グローバルチャットチャンネルの場合のみ)
        is_global_chat = db.query(GlobalChatChannel).filter_by(channel_id=channel_id).first()
        if is_global_chat:
            global_config_cog: GlobalConfigCog = self.bot.get_cog("GlobalConfigCog")
            if not global_config_cog:
                logger.warning("GlobalConfigCog not loaded, skipping global rate limit check.")
                return False, ""
            
            global_settings = await global_config_cog.get_global_settings(db)
            
            global_count_limit = global_settings.get('rate_limit_count', 10)
            global_window = global_settings.get('rate_limit_window', 60)

            if global_count_limit > 0 and global_window > 0:
                limit_time = now - timedelta(seconds=global_window)
                global_chat_salt = global_settings.get("global_chat_salt")
                if not global_chat_salt:
                    logger.error("Global chat salt is not configured.")
                    return False, ""  # Saltがない場合はチェックをスキップ

                global_rate_limit_key = encryptor.generate_global_rate_limit_key(user_id, global_chat_salt, now.date())

                recent_global_posts_count = db.query(func.count(RateLimit.id)).filter(
                    RateLimit.rate_limit_key == global_rate_limit_key,
                    RateLimit.timestamp > limit_time
                ).scalar()

                if recent_global_posts_count >= global_count_limit:
                    oldest_post_in_window = db.query(RateLimit).filter(
                        RateLimit.rate_limit_key == global_rate_limit_key,
                        RateLimit.timestamp > limit_time
                    ).order_by(RateLimit.timestamp.asc()).limit(1).first()
                    
                    if oldest_post_in_window:
                        wait_time = (oldest_post_in_window.timestamp + timedelta(seconds=global_window)) - now
                        wait_seconds = max(int(wait_time.total_seconds()), 1)
                        return True, f"❌ グローバルレート制限に達しました。あと {wait_seconds} 秒お待ちください。"
                    else:
                        return True, "❌ グローバルレート制限に達しました。しばらくしてから再試行してください。"

        return False, ""

    def check_ng_words(self, db: Session, guild_id: str, content: str) -> tuple[bool, str | None]:
        """NGワードをチェックする"""
        ng_words = db.query(NgWord).filter(NgWord.guild_id == guild_id).all()
        for ng_word in ng_words:
            is_match = False
            if ng_word.match_type == 'exact':
                if ng_word.word == content:
                    is_match = True
            elif ng_word.match_type == 'regex':
                try:
                    if re.search(ng_word.word, content):
                        is_match = True
                except re.error:
                    # 正規表現が無効な場合はログに出力してスキップ
                    logger.warning(f"Invalid regex for NG word (ID: {ng_word.id}): {ng_word.word}")
                    continue
            else:  # partial (default)
                if ng_word.word in content:
                    is_match = True
            
            if is_match:
                return True, ng_word.action
        return False, None

    def is_globally_banned(self, db: Session, guild_id: str, user_id: str) -> bool:
        """ユーザーがグローバルBANされているかチェックする"""
        # ユーザーIDでのBANを確認
        user_ban = db.query(GlobalChatBan).filter_by(target_id=user_id, target_type='USER', room_id=None).first()
        if user_ban:
            return True
        # サーバーIDでのBANを確認
        guild_ban = db.query(GlobalChatBan).filter_by(target_id=guild_id, target_type='GUILD', room_id=None).first()
        if guild_ban:
            return True
        return False

    def check_global_ng_words(self, db: Session, content: str) -> bool:
        """グローバルNGワードをチェックする"""
        ng_words = db.query(GlobalNgWord).all()
        for ng_word in ng_words:
            is_match = False
            if ng_word.match_type == 'exact':
                if ng_word.word == content:
                    is_match = True
            elif ng_word.match_type == 'regex':
                try:
                    if re.search(ng_word.word, content):
                        is_match = True
                except re.error:
                    logger.warning(f"Invalid regex for Global NG word (ID: {ng_word.id}): {ng_word.word}")
                    continue
            else:  # partial
                if ng_word.word in content:
                    is_match = True
            
            if is_match:
                return True
        return False

    async def get_webhook(self, channel: discord.TextChannel | discord.Thread) -> Webhook:
        """チャンネルまたはスレッドのWebhookを取得または作成する"""
        target_channel = channel
        if isinstance(channel, discord.Thread):
            target_channel = channel.parent

        webhooks = await target_channel.webhooks()
        webhook = discord.utils.find(lambda wh: wh.user == self.bot.user, webhooks)
        if webhook is None:
            webhook = await target_channel.create_webhook(name=f"{self.bot.user.name} Webhook")
        return webhook

    async def get_or_create_anon_id(self, db: Session, guild_id: str, channel_or_thread_id: str, user_id_signature: str) -> str:
        """匿名IDを取得または作成する。"""
        now_utc = datetime.now(pytz.utc)
        
        config_cog: ConfigCog = self.bot.get_cog("ConfigCog")
        settings = await config_cog.get_guild_settings(db, guild_id)
        id_duration_seconds = settings.get('anon_id_duration', 86400)
        
        expiration_time = now_utc - timedelta(seconds=id_duration_seconds)

        mapping = db.query(AnonIdMapping).filter(
            AnonIdMapping.guild_id == guild_id,
            AnonIdMapping.channel_or_thread_id == channel_or_thread_id,
            AnonIdMapping.user_id_signature == user_id_signature,
            AnonIdMapping.created_at >= expiration_time
        ).first()

        if mapping:
            return mapping.anon_id
        else:
            new_anon_id = nanoid.generate(size=10)
            new_mapping = AnonIdMapping(
                guild_id=guild_id,
                channel_or_thread_id=channel_or_thread_id,
                user_id_signature=user_id_signature,
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
        channel: discord.TextChannel | discord.Thread,
        content: str,
        attachments: list[discord.Attachment],
        is_converted: bool = False,
        original_message_id: str | None = None
    ) -> AnonymousPost:
        """匿名メッセージを投稿する内部共通処理 (新アーキテクチャ)"""
        config_cog: ConfigCog = self.bot.get_cog("ConfigCog")
        settings = await config_cog.get_guild_settings(db, guild_id)
        guild_salt = settings['guild_salt']
        user_id = str(user.id)

        # --- 事前チェック ---
        if self.is_banned(db, guild_id, user_id) or self.is_globally_banned(db, guild_id, user_id):
            raise ValueError("Banned user")

        is_limited, limit_message = await self.check_rate_limit(db, user_id, guild_salt, settings, str(channel.id))
        if is_limited:
            raise ValueError(limit_message)

        is_ng, ng_action = self.check_ng_words(db, guild_id, content)
        if is_ng and ng_action == 'block':
            raise ValueError("NG word detected")
        
        if self.check_global_ng_words(db, content):
            raise ValueError("Global NG word detected")

        max_length = settings.get('max_message_length', 2000)
        if len(content) > max_length:
            raise ValueError(f"Message too long ({len(content)} > {max_length})")

        # --- 暗号化・署名生成 ---
        key_version = encryptor.current_key_version
        # 投稿ごとにユニークなソルトとノンスを生成
        encryption_salt = os.urandom(16)
        
        # nonceを投稿時刻から決定的に生成する
        now_utc = datetime.now(pytz.utc)
        nonce = now_utc.strftime('%Y-%m-%d-%H-%M').encode()

        user_id_encrypted = encryptor.encrypt_user_id(user_id, key_version, encryption_salt)
        search_tag = encryptor.generate_search_tag(user_id, guild_salt, nonce)
        global_user_signature = encryptor.generate_global_user_signature(user_id, key_version, nonce)
        
        # 匿名ID生成用の署名 (これは永続的である必要がある)
        # サーバー秘密鍵とユーザーIDから決定的な署名を生成
        user_id_signature_for_anon_id = encryptor.generate_search_tag(user_id, guild_salt, b'anon-id-purpose')

        # --- 匿名ID取得、Webhookで投稿 ---
        channel_or_thread_id = str(channel.id)
        if isinstance(channel, discord.Thread) and isinstance(channel.parent, discord.ForumChannel):
            channel_or_thread_id = str(channel.parent_id)
        
        anon_id = await self.get_or_create_anon_id(db, guild_id, channel_or_thread_id, user_id_signature_for_anon_id)
        webhook = await self.get_webhook(channel)

        files = [await att.to_file() for att in attachments]
        send_kwargs = {
            "content": content,
            "username": settings.get('anon_id_format', '匿名ユーザー_{id}').format(id=anon_id),
            "files": files,
            "wait": True,
        }
        if isinstance(channel, discord.Thread):
            send_kwargs["thread"] = channel
        
        webhook_message = await webhook.send(**send_kwargs)

        # --- DB保存 ---
        attachment_urls = [att.url for att in webhook_message.attachments]
        new_post = AnonymousPost(
            guild_id=guild_id,
            user_id_encrypted=user_id_encrypted,
            encryption_salt=base64.b64encode(encryption_salt).decode(),
            global_user_signature=global_user_signature,
            key_version=key_version,
            search_tag=search_tag,
            anonymous_id=anon_id,
            message_id=str(webhook_message.id),
            channel_id=str(channel.id),
            thread_id=str(channel.id) if isinstance(channel, discord.Thread) else None,
            content=content,
            attachment_urls=attachment_urls,
            is_converted=is_converted,
            original_message_id=original_message_id,
            created_at=now_utc
        )
        db.add(new_post)
        
        # レート制限データの記録
        now = datetime.now(pytz.utc)
        local_rate_limit_key = encryptor.generate_rate_limit_key(user_id, guild_salt, now.date())
        db.add(RateLimit(rate_limit_key=local_rate_limit_key, timestamp=now))
        
        # --- グローバルチャット処理 ---
        source_gcl_channel = db.query(GlobalChatChannel).filter_by(channel_id=str(channel.id)).first()
        if source_gcl_channel:
            # グローバル用のレート制限レコードを追加
            global_config_cog: GlobalConfigCog = self.bot.get_cog("GlobalConfigCog")
            global_settings = await global_config_cog.get_global_settings(db)
            global_chat_salt = global_settings.get("global_chat_salt")
            
            if global_chat_salt:
                global_rate_limit_key = encryptor.generate_global_rate_limit_key(user_id, global_chat_salt, now.date())
                db.add(RateLimit(rate_limit_key=global_rate_limit_key, timestamp=now))
            else:
                logger.error("Global chat salt is not configured. Skipping global rate limit recording.")

            # イベントレコードを作成
            gce = GlobalChatEvents(
                original_message_id=str(webhook_message.id),
                user_id_encrypted=user_id_encrypted,
                status='PENDING'
            )
            db.add(gce)
            # 即時コミットは行わず、呼び出し元でコミットする
            # db.commit()
            
            # 非同期タスクでメッセージ転送を実行
            self.bot.loop.create_task(
                self.forward_global_chat_message(
                    original_channel_id=str(channel.id),
                    original_message_id=str(webhook_message.id),
                    room_id=source_gcl_channel.room_id,
                    content=content,
                    username=send_kwargs["username"],
                    attachments=attachments
                )
            )
        
        return new_post

    async def forward_global_chat_message(self, original_channel_id: str, original_message_id: str, room_id: int, content: str, username: str, attachments: list[discord.Attachment]):
        """非同期でグローバルチャットメッセージを転送し、結果をDBに記録する"""
        db: Session = next(get_db())
        try:
            forward_channels = db.query(GlobalChatChannel).filter(
                GlobalChatChannel.room_id == room_id,
                GlobalChatChannel.channel_id != original_channel_id  # 自分自身には送らない
            ).all()

            forwarded_map = {}
            for target_gcl_channel in forward_channels:
                try:
                    # BANチェックはここでは行わない（投稿時に行っているため）
                    target_webhook = Webhook.from_url(target_gcl_channel.webhook_url, session=self.bot.session)
                    files_for_forward = [await att.to_file() for att in attachments]
                    
                    forwarded_message = await target_webhook.send(
                        content=content,
                        username=username,
                        files=files_for_forward,
                        wait=True
                    )
                    forwarded_map[target_gcl_channel.guild_id] = str(forwarded_message.id)
                except Exception as e:
                    logger.error(f"Failed to forward message to channel {target_gcl_channel.channel_id}: {e}")

            # イベントレコードを更新
            event = db.query(GlobalChatEvents).filter_by(original_message_id=original_message_id).first()
            if event:
                event.forwarded_map = forwarded_map
                event.status = 'COMPLETED'
                db.commit()

        except Exception as e:
            logger.error(f"Error during global chat forwarding task for message {original_message_id}: {e}")
            db.rollback()
        finally:
            db.close()

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
        if isinstance(interaction.channel, discord.ForumChannel):
            await interaction.response.send_message("❌ フォーラムチャンネル自体には投稿できません。`/forum_post` を使用するか、既存の投稿内で返信してください。", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        db = next(get_db())
        try:
            config_cog: ConfigCog = self.bot.get_cog("ConfigCog")
            settings = await config_cog.get_guild_settings(db, str(interaction.guild.id))
            attachments = [att for att in [attachment1, attachment2, attachment3, attachment4, attachment5] if att]
            
            new_post = await self._post_message(
                db=db,
                guild_id=str(interaction.guild.id),
                user=interaction.user,
                channel=interaction.channel,
                content=message,
                attachments=attachments
            )

            user_id_signature_for_anon_id = encryptor.generate_search_tag(str(interaction.user.id), settings['guild_salt'], b'anon-id-purpose')
            db.add(UserCommandLog(
                guild_id=str(interaction.guild.id),
                command_name='post',
                executed_by_signature=user_id_signature_for_anon_id,
                params={'channel_id': str(interaction.channel_id), 'message_length': len(message), 'attachments': len(attachments)}
            ))
            db.commit()

            await interaction.delete_original_response()

            log_embed = discord.Embed(title="匿名投稿", color=discord.Color.blue(), timestamp=discord.utils.utcnow())
            log_embed.add_field(name="匿名ID", value=new_post.anonymous_id, inline=False)
            log_embed.add_field(name="チャンネル", value=interaction.channel.mention, inline=False)
            if new_post.attachment_urls:
                log_embed.add_field(name="添付ファイル", value="\n".join(new_post.attachment_urls), inline=False)
            await self._send_log_message(str(interaction.guild.id), log_embed)

        except ValueError as e:
            error_messages = {
                "Banned user": "❌ あなたは匿名チャットからBANされています。",
                "NG word detected": "❌ メッセージに不適切な単語が含まれているため、投稿をブロックしました。",
                "Global NG word detected": "❌ メッセージに不適切な単語が含まれているため、投稿をブロックしました。",
            }
            message = error_messages.get(str(e), str(e))
            await interaction.followup.send(message, ephemeral=True)
        except Exception as e:
            db.rollback()
            logger.error(f"Error in post command: {e}", exc_info=True)
            if not interaction.response.is_done():
                await interaction.followup.send("❌ エラーが発生しました。管理者に連絡してください。", ephemeral=True)
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
        db = next(get_db())
        try:
            # 返信先のメッセージを取得
            try:
                await interaction.channel.fetch_message(int(message_id))
            except (discord.NotFound, ValueError):
                await interaction.followup.send("❌ 返信先のメッセージが見つかりません。", ephemeral=True)
                return

            # 引用テキストを作成
            reply_to_url = f"https://discord.com/channels/{interaction.guild.id}/{interaction.channel.id}/{message_id}"
            target_post = db.query(AnonymousPost).filter_by(message_id=message_id).first()
            reply_prefix = f">>[{target_post.anonymous_id}]({reply_to_url})\n" if target_post else f"> [返信先]({reply_to_url})\n"
            content_with_reply = f"{reply_prefix}{message}"

            attachments = [att for att in [attachment1, attachment2, attachment3] if att]
            
            # 投稿処理を共通関数に委譲
            await self._post_message(
                db=db,
                guild_id=str(interaction.guild.id),
                user=interaction.user,
                channel=interaction.channel,
                content=content_with_reply,
                attachments=attachments
            )

            # ログ記録
            config_cog: ConfigCog = self.bot.get_cog("ConfigCog")
            settings = await config_cog.get_guild_settings(db, str(interaction.guild.id))
            user_id_signature_for_anon_id = encryptor.generate_search_tag(str(interaction.user.id), settings['guild_salt'], b'anon-id-purpose')
            db.add(UserCommandLog(
                guild_id=str(interaction.guild.id),
                command_name='reply',
                executed_by_signature=user_id_signature_for_anon_id,
                params={'channel_id': str(interaction.channel.id), 'target_message_id': message_id}
            ))
            db.commit()

            await interaction.followup.send("✅ メッセージに返信しました。", ephemeral=True)

        except ValueError as e:
            error_messages = {
                "Banned user": "❌ あなたは匿名チャットからBANされています。",
                "NG word detected": "❌ メッセージに不適切な単語が含まれているため、投稿をブロックしました。",
                "Global NG word detected": "❌ メッセージに不適切な単語が含まれているため、投稿をブロックしました。",
            }
            message = error_messages.get(str(e), str(e))
            await interaction.followup.send(message, ephemeral=True)
        except Exception as e:
            db.rollback()
            logger.error(f"Error in reply command: {e}", exc_info=True)
            if not interaction.response.is_done():
                await interaction.followup.send("❌ エラーが発生しました。管理者に連絡してください。", ephemeral=True)
        finally:
            db.close()
            db.close()
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

            if not isinstance(interaction.channel, discord.TextChannel):
                await interaction.followup.send("❌ このコマンドはテキストチャンネルでのみ使用できます。", ephemeral=True)
                return
            
            # スレッドを作成
            thread = await interaction.channel.create_thread(name=title, type=discord.ChannelType.public_thread)

            # 投稿処理を共通関数に委譲
            new_post = await self._post_message(
                db=db,
                guild_id=guild_id,
                user=interaction.user,
                channel=thread,
                content=content,
                attachments=[]
            )

            # スレッド情報をDBに保存
            new_thread_db = AnonymousThread(
                guild_id=guild_id,
                thread_discord_id=str(thread.id),
                board=board,
                title=title,
                created_by_encrypted=new_post.user_id_encrypted
            )
            db.add(new_thread_db)

            # ログ記録
            config_cog: ConfigCog = self.bot.get_cog("ConfigCog")
            settings = await config_cog.get_guild_settings(db, guild_id)
            user_id_signature_for_anon_id = encryptor.generate_search_tag(user_id, settings['guild_salt'], b'anon-id-purpose')
            db.add(UserCommandLog(
                guild_id=guild_id,
                command_name='thread',
                executed_by_signature=user_id_signature_for_anon_id,
                params={'channel_id': str(interaction.channel.id), 'title': title}
            ))
            db.commit()

            await interaction.followup.send(f"✅ スレッド '{title}' を作成しました。", ephemeral=True)

            log_embed = discord.Embed(title="匿名スレッド作成", color=discord.Color.green(), timestamp=discord.utils.utcnow())
            log_embed.add_field(name="匿名ID", value=new_post.anonymous_id, inline=False)
            log_embed.add_field(name="スレッド", value=thread.mention, inline=False)
            log_embed.add_field(name="タイトル", value=title, inline=False)
            await self._send_log_message(guild_id, log_embed)

        except ValueError as e:
            error_messages = {
                "Banned user": "❌ あなたは匿名チャットからBANされています。",
                "NG word detected": "❌ タイトルまたはメッセージに不適切な単語が含まれているため、スレッドを作成できません。",
                "Global NG word detected": "❌ タイトルまたはメッセージに不適切な単語が含まれているため、スレッドを作成できません。",
            }
            message = error_messages.get(str(e), str(e))
            await interaction.followup.send(message, ephemeral=True)
        except Exception as e:
            db.rollback()
            logger.error(f"Error in thread command: {e}", exc_info=True)
            if not interaction.response.is_done():
                await interaction.followup.send("❌ スレッド作成中にエラーが発生しました。管理者に連絡してください。", ephemeral=True)
        finally:
            db.close()

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        """
        コマンド以外でメッセージが削除された場合も、それが匿名投稿であればDBに記録する
        """
        if not message.guild:
            return
            
        # Webhookによって投稿されたメッセージはbot.userがauthorになるため、これだけでは不十分
        # message.webhook_id があるかどうかで判断するのがより確実
        if not message.webhook_id:
            return

        db: Session = next(get_db())
        try:
            post = db.query(AnonymousPost).filter(
                AnonymousPost.guild_id == str(message.guild.id),
                AnonymousPost.message_id == str(message.id),
                AnonymousPost.deleted_at.is_(None)
            ).first()

            if post:
                # 監査ログから削除実行者を取得
                deleter = None
                # 監査ログが取得できるまで少し待つ
                await asyncio.sleep(2)
                async for entry in message.guild.audit_logs(limit=5, action=discord.AuditLogAction.message_delete):
                    # 削除されたメッセージのチャンネルと実行者のターゲットが一致するかで判断
                    if entry.extra.channel.id == message.channel.id and entry.target.id == self.bot.user.id:
                        deleter = entry.user
                        deleter = entry.user
                        break
                
                post.deleted_at = discord.utils.utcnow()
                if deleter:
                    post.deleted_by = str(deleter.id)
                else:
                    # 監査ログで追えない場合は、投稿者自身が削除したとみなし、暗号化IDを保存
                    post.deleted_by = post.user_id_encrypted

                db.commit()

                # --- グローバルチャットの削除連携 ---
                event = db.query(GlobalChatEvents).filter_by(original_message_id=str(message.id)).first()
                if event and event.forwarded_map:
                    source_gcl_channel = db.query(GlobalChatChannel).filter_by(channel_id=post.channel_id).first()
                    if source_gcl_channel:
                        room_id = source_gcl_channel.room_id
                        for guild_id_str, msg_id_str in event.forwarded_map.items():
                            try:
                                target_gcl_channel = db.query(GlobalChatChannel).filter_by(guild_id=guild_id_str, room_id=room_id).first()
                                if not target_gcl_channel:
                                    continue
                                target_channel = self.bot.get_channel(int(target_gcl_channel.channel_id))
                                if target_channel:
                                    message_to_delete = await target_channel.fetch_message(int(msg_id_str))
                                    await message_to_delete.delete()
                            except discord.NotFound:
                                pass
                            except discord.Forbidden:
                                logger.warning(f"Failed to delete forwarded message {msg_id_str} in guild {guild_id_str}: Missing Permissions")
                            except Exception as e:
                                logger.error(f"Error deleting forwarded message {msg_id_str}: {e}")

                log_embed = discord.Embed(title="匿名投稿削除 (外部)", color=0x7289da, timestamp=discord.utils.utcnow())
                log_embed.add_field(name="匿名ID", value=post.anonymous_id, inline=False)
                log_embed.add_field(name="対象メッセージID", value=message.id, inline=False)
                log_embed.add_field(name="チャンネル", value=message.channel.mention, inline=False)
                if deleter:
                    log_embed.add_field(name="削除実行者", value=deleter.mention, inline=False)
                else:
                    log_embed.add_field(name="削除実行者", value="不明 (投稿者本人による削除の可能性)", inline=False)
                
                await self._send_log_message(str(message.guild.id), log_embed)

        except Exception as e:
            logger.error(f"Error in on_message_delete event: {e}", exc_info=True)
            db.rollback()
        finally:
            db.close()

    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread):
        """
        フォーラム投稿（スレッド）が削除された場合、DBに記録する
        """
        # フォーラムチャンネル内のスレッドでなければ無視
        if not isinstance(thread.parent, discord.ForumChannel):
            return

        db: Session = next(get_db())
        try:
            # 削除されたスレッドIDに紐づく投稿を探す
            post = db.query(AnonymousPost).filter(
                AnonymousPost.guild_id == str(thread.guild.id),
                AnonymousPost.thread_id == str(thread.id),
                AnonymousPost.deleted_at.is_(None)
            ).first()

            if post:
                # 監査ログから削除実行者を取得
                deleter = None
                # 監査ログが記録されるまで少し待つ
                await asyncio.sleep(2)
                async for entry in thread.guild.audit_logs(limit=5, action=discord.AuditLogAction.thread_delete):
                    if entry.target.id == thread.id:
                        deleter = entry.user
                        break
                
                post.deleted_at = discord.utils.utcnow()
                if deleter:
                    post.deleted_by = str(deleter.id)
                else:
                    # 監査ログで追えない場合は、投稿者本人が削除したとみなし、暗号化IDを保存
                    post.deleted_by = post.user_id_encrypted

                db.commit()

                log_embed = discord.Embed(title="匿名フォーラム投稿削除 (外部)", color=0x7289da, timestamp=discord.utils.utcnow())
                log_embed.add_field(name="匿名ID", value=post.anonymous_id, inline=False)
                log_embed.add_field(name="対象スレッド", value=thread.name, inline=False)
                log_embed.add_field(name="フォーラム", value=thread.parent.mention, inline=False)
                if deleter:
                    log_embed.add_field(name="削除実行者", value=deleter.mention, inline=False)
                else:
                    log_embed.add_field(name="削除実行者", value="不明 (投稿者本人による削除の可能性)", inline=False)
                
                await self._send_log_message(str(thread.guild.id), log_embed)

        except Exception as e:
            logger.error(f"Error in on_thread_delete event: {e}", exc_info=True)
            db.rollback()
        finally:
            db.close()

    @app_commands.command(name="forum_post", description="指定したフォーラムに匿名で新しい投稿を作成します。")
    @app_commands.describe(
        forum="投稿先のフォーラムチャンネル",
        title="投稿のタイトル",
        content="最初のメッセージ内容"
    )
    async def forum_post(self, interaction: discord.Interaction, forum: discord.ForumChannel, title: str, content: str):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        try:
            guild_id = str(interaction.guild.id)
            user_id = str(interaction.user.id)

            # 匿名IDの生成 (IDのスコープはフォーラムチャンネル自体)
            config_cog: ConfigCog = self.bot.get_cog("ConfigCog")
            settings = await config_cog.get_guild_settings(db, guild_id)
            guild_salt = settings['guild_salt']
            user_id_signature_for_anon_id = encryptor.generate_search_tag(user_id, guild_salt, b'anon-id-purpose')
            anon_id = await self.get_or_create_anon_id(db, guild_id, str(forum.id), user_id_signature_for_anon_id)
            
            # Webhookを取得して、匿名ユーザーとして投稿
            webhook = await self.get_webhook(forum)
            thread_with_message = await webhook.send(
                content=content,
                username=settings.get('anon_id_format', '匿名ユーザー_{id}').format(id=anon_id),
                thread_name=title,
                wait=True,
            )
            
            # _post_message を直接呼び出す代わりに、必要な情報を手動で構築して保存する
            # なぜなら、forum_postでは特殊なスレッド作成処理とWebhook送信が先に行われるため
            
            # --- 事前チェック ---
            if self.is_banned(db, guild_id, user_id) or self.is_globally_banned(db, guild_id, user_id):
                raise ValueError("Banned user")
            is_limited, limit_message = await self.check_rate_limit(db, user_id, guild_salt, settings, str(forum.id))
            if is_limited:
                raise ValueError(limit_message)
            is_ng, ng_action = self.check_ng_words(db, guild_id, title + "\n" + content)
            if is_ng and ng_action == 'block':
                raise ValueError("NG word detected")
            if self.check_global_ng_words(db, title + "\n" + content):
                raise ValueError("Global NG word detected")

            # --- 暗号化・署名生成 ---
            key_version = encryptor.current_key_version
            encryption_salt = os.urandom(16)
            now_utc = datetime.now(pytz.utc)
            nonce = now_utc.strftime('%Y-%m-%d-%H-%M').encode()
            user_id_encrypted = encryptor.encrypt_user_id(user_id, key_version, encryption_salt)
            search_tag = encryptor.generate_search_tag(user_id, guild_salt, nonce)
            global_user_signature = encryptor.generate_global_user_signature(user_id, key_version, nonce)

            # --- DB保存 ---
            new_post = AnonymousPost(
                guild_id=guild_id,
                user_id_encrypted=user_id_encrypted,
                encryption_salt=base64.b64encode(encryption_salt).decode(),
                global_user_signature=global_user_signature,
                key_version=key_version,
                search_tag=search_tag,
                anonymous_id=anon_id,
                message_id=str(thread_with_message.id),
                channel_id=str(forum.id),
                thread_id=str(thread_with_message.channel.id),
                content=content,
                created_at=now_utc,
            )
            db.add(new_post)
            
            # レート制限データの記録
            now = datetime.now(pytz.utc)
            local_rate_limit_key = encryptor.generate_rate_limit_key(user_id, guild_salt, now.date())
            db.add(RateLimit(rate_limit_key=local_rate_limit_key, timestamp=now))
            
            # グローバルチャットチャンネルか確認し、グローバル用も記録
            is_global_chat = db.query(GlobalChatChannel).filter_by(channel_id=str(forum.id)).first()
            if is_global_chat:
                global_config_cog: GlobalConfigCog = self.bot.get_cog("GlobalConfigCog")
                global_settings = await global_config_cog.get_global_settings(db)
                global_chat_salt = global_settings.get("global_chat_salt")

                if global_chat_salt:
                    global_rate_limit_key = encryptor.generate_global_rate_limit_key(user_id, global_chat_salt, now.date())
                    db.add(RateLimit(rate_limit_key=global_rate_limit_key, timestamp=now))
                else:
                    logger.error("Global chat salt is not configured. Skipping global rate limit recording.")
            
            db.add(UserCommandLog(
                guild_id=guild_id,
                command_name='forum_post',
                executed_by_signature=user_id_signature_for_anon_id,
                params={'channel_id': str(forum.id), 'title': title}
            ))
            db.commit()

            await interaction.followup.send(f"✅ フォーラムに投稿 '{title}' を作成しました。", ephemeral=True)

        except ValueError as e:
            error_messages = {
                "Banned user": "❌ あなたは匿名チャットからBANされています。",
                "NG word detected": "❌ タイトルまたはメッセージに不適切な単語が含まれているため、投稿できません。",
                "Global NG word detected": "❌ タイトルまたはメッセージに不適切な単語が含まれているため、投稿できません。",
            }
            message = error_messages.get(str(e), str(e))
            await interaction.followup.send(message, ephemeral=True)
            # エラーが発生した場合、作成されてしまったスレッドを削除する
            if 'thread_with_message' in locals() and thread_with_message:
                try:
                    await thread_with_message.channel.delete()
                except Exception as del_e:
                    logger.error(f"Failed to delete thread after error in forum_post: {del_e}")
        except Exception as e:
            db.rollback()
            logger.error(f"Error in forum_post command: {e}", exc_info=True)
            await interaction.followup.send("❌ 投稿中にエラーが発生しました。", ephemeral=True)
        finally:
            db.close()

    @app_commands.command(name="myid", description="このチャンネルで今日使用している匿名IDを表示します。")
    async def myid(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        try:
            guild_id = str(interaction.guild.id)
            user_id = str(interaction.user.id)

            config_cog: ConfigCog = self.bot.get_cog("ConfigCog")
            settings = await config_cog.get_guild_settings(db, guild_id)
            guild_salt = settings['guild_salt']
            
            user_id_signature_for_anon_id = encryptor.generate_search_tag(user_id, guild_salt, b'anon-id-purpose')
            
            channel_or_thread_id = str(interaction.channel_id)
            # フォーラム内のスレッドの場合、親のフォーラムチャンネルIDをキーにする
            if isinstance(interaction.channel, discord.Thread) and isinstance(interaction.channel.parent, discord.ForumChannel):
                channel_or_thread_id = str(interaction.channel.parent_id)
            
            anon_id = await self.get_or_create_anon_id(db, guild_id, channel_or_thread_id, user_id_signature_for_anon_id)

            await interaction.followup.send(f"ℹ️ このチャンネルでの今日のあなたの匿名IDは `{anon_id}` です。", ephemeral=True)
            db.commit()

        except Exception as e:
            db.rollback()
            logger.error(f"Error in myid command: {e}", exc_info=True)
            await interaction.followup.send("❌ IDの取得中にエラーが発生しました。", ephemeral=True)
        finally:
            db.close()


async def setup(bot: commands.Bot):
    await bot.add_cog(AnonymousPostCog(bot))
