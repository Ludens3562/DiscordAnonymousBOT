import math
import base64
import re
import logging
from enum import Enum
from datetime import timedelta
from bloom_filter import BloomFilter

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy.orm import Session
from sqlalchemy import cast, String

from cogs.config import ConfigCog
from database import get_db
from models import AdminCommandLog, AnonymousPost, GuildBannedUser, BotBannedUser, BulkDeleteHistory, GlobalChatBan, GlobalChatEvents, GlobalChatChannel
from utils.crypto import Encryptor

logger = logging.getLogger(__name__)
encryptor = Encryptor()


class UserPostsView(discord.ui.View):
    def __init__(self, bot, guild_id: str, user: discord.User, posts: list[AnonymousPost]):
        super().__init__(timeout=180)
        self.bot = bot
        self.guild_id = guild_id
        self.user = user
        self.posts = posts
        self.current_page = 1
        self.posts_per_page = 10
        self.total_pages = math.ceil(len(self.posts) / self.posts_per_page)

    async def get_page_embed(self) -> discord.Embed:
        start_index = (self.current_page - 1) * self.posts_per_page
        end_index = start_index + self.posts_per_page
        page_posts = self.posts[start_index:end_index]

        embed = discord.Embed(
            title=f"{self.user.name} の匿名投稿",
            color=discord.Color.purple()
        )
        embed.set_footer(text=f"ページ {self.current_page}/{self.total_pages} ({len(self.posts)}件)")

        if not page_posts:
            embed.description = "このページに投稿はありません。"
            return embed

        for post in page_posts:
            channel = self.bot.get_channel(int(post.channel_id))
            channel_name = f"#{channel.name}" if channel else "不明なチャンネル"
            message_link = f"https://discord.com/channels/{self.guild_id}/{post.channel_id}/{post.message_id}"
            
            title = f"投稿日時: {post.created_at.strftime('%Y-%m-%d %H:%M:%S')}"
            if post.deleted_at:
                title = f"(削除済み) {title}"

            value = (
                f"**チャンネル:** {channel_name}\n"
                f"**メッセージ:** [リンク]({message_link})\n"
                f"**匿名ID:** `{post.anonymous_id}`\n"
                f"**誤投稿変換:** {'あり' if post.is_converted else 'なし'}\n"
            )
            if post.attachment_urls:
                value += "**添付ファイル:**\n" + "\n".join(f"- [ファイル]({url})" for url in post.attachment_urls)

            content_display = f"```{post.content}```"
            if post.deleted_at:
                content_display = f"~~{content_display}~~"
            
            value += content_display

            embed.add_field(name=title, value=value, inline=False)
            
        return embed

    @discord.ui.button(label="◀️ 前へ", style=discord.ButtonStyle.grey)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 1:
            self.current_page -= 1
            embed = await self.get_page_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="次へ ▶️", style=discord.ButtonStyle.grey)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.total_pages:
            self.current_page += 1
            embed = await self.get_page_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()


class AdminLogView(discord.ui.View):
    def __init__(self, bot, guild_id: str, logs: list[AdminCommandLog], total_logs: int, title: str):
        super().__init__(timeout=180)
        self.bot = bot
        self.guild_id = guild_id
        self.logs = logs
        self.total_logs = total_logs
        self.title = title
        self.current_page = 1
        self.logs_per_page = 10
        self.total_pages = math.ceil(len(self.logs) / self.logs_per_page)
        self.encryptor = Encryptor()

    async def get_page_embed(self) -> discord.Embed:
        start_index = (self.current_page - 1) * self.logs_per_page
        end_index = start_index + self.logs_per_page
        page_logs = self.logs[start_index:end_index]

        embed = discord.Embed(title=self.title, color=discord.Color.dark_gold())
        embed.set_footer(text=f"ページ {self.current_page}/{self.total_pages} ({self.total_logs}件)")

        if not page_logs:
            embed.description = "このページにログはありません。"
            return embed

        db: Session = next(get_db())
        try:
            config_cog: ConfigCog = self.bot.get_cog("ConfigCog")

            for log in page_logs:
                executor = await self.bot.fetch_user(int(log.executed_by))
                
                value_str = f"**実行者:** {executor.mention} (`{log.executed_by}`)\n"
                
                if log.target_user_id:
                    decrypted_id = None
                    if log.log_salt_type:
                        salt = self.encryptor.get_logging_salt(log.log_salt_type)
                        if salt:
                            decrypted_id = self.encryptor.decrypt_log_user_id(log.target_user_id, salt)
                    if decrypted_id:
                        target_user = await self.bot.fetch_user(int(decrypted_id))
                        value_str += f"**対象者:** {target_user.mention} (`{decrypted_id}`)\n"
                    else:
                        value_str += "**対象者:** `ID復号失敗`\n"

                value_str += f"**実行日時:** {log.created_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
                value_str += f"**成功/失敗:** {'✅ Success' if log.success else '❌ Failure'}"

                embed.add_field(
                    name=f"コマンド: `{log.command_name}`",
                    value=value_str,
                    inline=False
                )
        finally:
            db.close()
            
        return embed

    @discord.ui.button(label="◀️ 前へ", style=discord.ButtonStyle.grey)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 1:
            self.current_page -= 1
            embed = await self.get_page_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="次へ ▶️", style=discord.ButtonStyle.grey)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.total_pages:
            self.current_page += 1
            embed = await self.get_page_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()


class Scope(Enum):
    current_channel = "current_channel"
    all_channels = "all_channels"
    # channels = "channels" # TODO: 複数チャンネル指定は後で実装


class ConditionType(Enum):
    messages = "messages"
    hours = "hours"
    user = "user"
    contains = "contains"
    pattern = "pattern"
    anonymous_id = "anonymous_id"
    converted_only = "converted_only"
    direct_only = "direct_only"
    # reactions_less = "reactions_less" # TODO: 後で実装


class AdminCommands(Enum):
    ban = "ban"
    unban = "unban"
    trace = "trace"
    user_posts = "user_posts"
    bulk_delete = "bulk_delete"
    admin_logs = "admin_logs"


class DeletedStatus(Enum):
    all = "すべて"
    deleted_only = "削除済みのみ"
    exclude_deleted = "削除済みを除く"


class ModerationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ban", description="ユーザーをこのサーバーの匿名投稿からBANします。")
    @app_commands.describe(user="BAN対象のユーザー", global_ban="BOT全体からBANするかどうか (デフォルト: False)")
    @app_commands.default_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, user: discord.User, global_ban: bool = False):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        success = False
        
        encrypted_user_id = encryptor.encrypt_user_id(str(user.id), encryptor.current_key_version, b'logging_salt_for_ban')

        try:
            guild_id = str(interaction.guild.id)
            user_id = str(user.id)
            
            banned_by_id = str(interaction.user.id)

            if global_ban:
                if not await self.bot.is_owner(interaction.user):
                    await interaction.followup.send("❌ グローバルBANはBOTのオーナーのみが実行できます。", ephemeral=True)
                    return
                existing_ban = db.query(BotBannedUser).filter_by(user_id=user_id).first()
                if existing_ban:
                    await interaction.followup.send(f"❌ {user.mention} は既にグローバルBANされています。", ephemeral=True)
                    return
                new_ban = BotBannedUser(user_id=user_id, banned_by=banned_by_id)
                db.add(new_ban)

                # グローバルチャットからもBAN
                gchat_ban = db.query(GlobalChatBan).filter_by(target_id=user_id, target_type='USER', room_id=None).first()
                if not gchat_ban:
                    new_gchat_ban = GlobalChatBan(
                        target_id=user_id,
                        target_type='USER',
                        reason="Global BAN from /ban command",
                        banned_by=banned_by_id
                    )
                    db.add(new_gchat_ban)

                db.commit()
                await interaction.followup.send(f"✅ {user.mention} をグローバルBANしました。", ephemeral=True)
            else:
                existing_ban = db.query(GuildBannedUser).filter_by(guild_id=guild_id, user_id=user_id).first()
                if existing_ban:
                    await interaction.followup.send(f"❌ {user.mention} は既にこのサーバーでBANされています。", ephemeral=True)
                    return
                new_ban = GuildBannedUser(guild_id=guild_id, user_id=user_id, banned_by=banned_by_id)
                db.add(new_ban)
                db.commit()
                await interaction.followup.send(f"✅ {user.mention} をこのサーバーの匿名投稿からBANしました。", ephemeral=True)
            
            success = True

        except Exception as e:
            db.rollback()
            logger.error(f"An error occurred in 'ban' command: {e}", exc_info=True)
            await interaction.followup.send(f"❌ エラーが発生しました: {e}", ephemeral=True)
        finally:
            log = AdminCommandLog(
                guild_id=str(interaction.guild.id),
                command_name='ban',
                executed_by=str(interaction.user.id),
                target_user_id=encrypted_user_id,
                log_salt_type='ban',
                params={'user_id': str(user.id), 'global_ban': global_ban},
                success=success
            )
            db.add(log)
            db.commit()
            db.close()

    @app_commands.command(name="unban", description="ユーザーの匿名投稿BANを解除します。")
    @app_commands.describe(user="BAN解除対象のユーザー", global_unban="グローバルBANを解除するかどうか (デフォルト: False)")
    @app_commands.default_permissions(ban_members=True)
    async def unban(self, interaction: discord.Interaction, user: discord.User, global_unban: bool = False):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        success = False

        encrypted_user_id = encryptor.encrypt_user_id(str(user.id), encryptor.current_key_version, b'logging_salt_for_unban')

        try:
            guild_id = str(interaction.guild.id)
            user_id = str(user.id)
            
            if global_unban:
                if not await self.bot.is_owner(interaction.user):
                    await interaction.followup.send("❌ グローバルBANの解除はBOTのオーナーのみが実行できます。", ephemeral=True)
                    return
                ban_to_remove = db.query(BotBannedUser).filter_by(user_id=user_id).first()
                if not ban_to_remove:
                    await interaction.followup.send(f"❌ {user.mention} はグローバルBANされていません。", ephemeral=True)
                    return
                db.delete(ban_to_remove)

                # グローバルチャットのBANも解除
                gchat_ban_to_remove = db.query(GlobalChatBan).filter_by(target_id=user_id, target_type='USER', room_id=None).first()
                if gchat_ban_to_remove:
                    db.delete(gchat_ban_to_remove)

                db.commit()
                await interaction.followup.send(f"✅ {user.mention} のグローバルBANを解除しました。", ephemeral=True)
            else:
                ban_to_remove = db.query(GuildBannedUser).filter_by(guild_id=guild_id, user_id=user_id).first()
                if not ban_to_remove:
                    await interaction.followup.send(f"❌ {user.mention} はこのサーバーでBANされていません。", ephemeral=True)
                    return
                db.delete(ban_to_remove)

                db.commit()
                await interaction.followup.send(f"✅ {user.mention} のこのサーバーでのBANを解除しました。", ephemeral=True)

            success = True

        except Exception as e:
            db.rollback()
            logger.error(f"An error occurred in 'unban' command: {e}", exc_info=True)
            await interaction.followup.send(f"❌ エラーが発生しました: {e}", ephemeral=True)
        finally:
            log = AdminCommandLog(
                guild_id=str(interaction.guild.id),
                command_name='unban',
                executed_by=str(interaction.user.id),
                target_user_id=encrypted_user_id,
                log_salt_type='unban',
                params={'user_id': str(user.id), 'global_unban': global_unban},
                success=success
            )
            db.add(log)
            db.commit()
            db.close()

    @app_commands.command(name="trace", description="メッセージIDから投稿者を特定します。")
    @app_commands.describe(message_id="特定したい匿名投稿のメッセージID")
    @app_commands.default_permissions(view_audit_log=True)
    async def trace(self, interaction: discord.Interaction, message_id: str):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        success = False
        post = None
        try:
            guild_id = str(interaction.guild.id)

            post = db.query(AnonymousPost).filter_by(guild_id=guild_id, message_id=message_id).first()
            if not post:
                await interaction.followup.send("❌ 指定されたメッセージIDの投稿が見つかりません。", ephemeral=True)
                return
            
            salt_bytes = base64.b64decode(post.encryption_salt)
            decrypted_user_id = encryptor.decrypt_user_id_with_salt(post.user_id_encrypted, salt_bytes)

            if decrypted_user_id:
                user = await self.bot.fetch_user(int(decrypted_user_id))
                member = interaction.guild.get_member(user.id)

                embed = discord.Embed(title="投稿者特定結果", color=discord.Color.orange())
                embed.set_author(name=f"{user.name} ({user.id})", icon_url=user.display_avatar.url)
                
                embed.add_field(name="メッセージID", value=f"[{message_id}](https://discord.com/channels/{guild_id}/{post.channel_id}/{message_id})", inline=False)
                embed.add_field(name="投稿者", value=f"{user.mention} (`{user.id}`)", inline=False)
                
                embed.add_field(name="匿名ID", value=f"`{post.anonymous_id}`", inline=True)
                embed.add_field(name="投稿日時", value=post.created_at.strftime('%Y-%m-%d %H:%M:%S'), inline=True)
                embed.add_field(name="誤投稿変換", value='あり' if post.is_converted else 'なし', inline=True)

                now = discord.utils.utcnow()
                created_at_days = (now - user.created_at).days
                embed.add_field(name="アカウント作成日時", value=f"{user.created_at.strftime('%Y-%m-%d %H:%M:%S')} ({created_at_days}日前)", inline=False)

                if member and member.joined_at:
                    # ここも修正
                    joined_at_days = (now - member.joined_at).days
                    embed.add_field(name="サーバー参加日時", value=f"{member.joined_at.strftime('%Y-%m-%d %H:%M:%S')} ({joined_at_days}日前)", inline=False)

                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.followup.send("❌ ユーザーIDの復号に失敗しました。キーが変更されたか、データが破損している可能性があります。", ephemeral=True)
            
            success = True

        except Exception as e:
            db.rollback()
            logger.error(f"An error occurred in 'trace' command: {e}", exc_info=True)
            await interaction.followup.send(f"❌ エラーが発生しました: {e}", ephemeral=True)
        finally:
            encrypted_user_id_for_log = None
            if decrypted_user_id:
                log_salt = encryptor.get_logging_salt('admin_logs')
                if log_salt:
                    encrypted_user_id_for_log = encryptor.encrypt_user_id(decrypted_user_id, encryptor.current_key_version, log_salt)

            log = AdminCommandLog(
                guild_id=str(interaction.guild.id),
                command_name='trace',
                executed_by=str(interaction.user.id),
                target_user_id=encrypted_user_id_for_log,
                log_salt_type='admin_logs',
                params={'message_id': message_id},
                success=success
            )
            db.add(log)
            db.commit()
            db.close()

    @app_commands.command(name="user_posts", description="指定したユーザーの匿名投稿を検索します (ブルームフィルタ使用)。")
    @app_commands.describe(
        user="検索対象のユーザー",
        days="検索する日数（1-90、デフォルト30）",
        deleted_status="削除済みメッセージの扱い"
    )
    @app_commands.default_permissions(view_audit_log=True)
    async def user_posts(self, interaction: discord.Interaction, user: discord.User, days: int = 30, deleted_status: DeletedStatus = DeletedStatus.exclude_deleted):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        success = False
        encrypted_user_id = None
        try:
            if not 1 <= days <= 90:
                await interaction.followup.send("❌ 日数は1から90の間で指定してください。", ephemeral=True)
                return

            guild_id = str(interaction.guild.id)
            user_id = str(user.id)

            config_cog: ConfigCog = self.bot.get_cog("ConfigCog")
            settings = await config_cog.get_guild_settings(db, guild_id)
            guild_salt = settings['guild_salt']
            
            # ユーザーIDを暗号化してログに残す
            # 投稿ごとのソルトがないため、ここでは固定のソルトで暗号化
            encrypted_user_id = encryptor.encrypt_user_id(user_id, encryptor.current_key_version, b'logging_salt_for_user_posts')

            start_date = discord.utils.utcnow() - timedelta(days=days)
            
            # 1. DBから期間内の全投稿を取得
            query = db.query(AnonymousPost).filter(
                AnonymousPost.guild_id == guild_id,
                AnonymousPost.created_at >= start_date
            )
            if deleted_status == DeletedStatus.deleted_only:
                query = query.filter(AnonymousPost.deleted_at.isnot(None))
            elif deleted_status == DeletedStatus.exclude_deleted:
                query = query.filter(AnonymousPost.deleted_at.is_(None))
            
            all_posts_in_period = query.order_by(AnonymousPost.created_at.asc()).all()

            if not all_posts_in_period:
                await interaction.followup.send(f"ℹ️ 過去{days}日間に検索対象の投稿はありませんでした。", ephemeral=True)
                success = True
                return

            # 2. メモリ上でsearch_tagを再計算して検証
            user_posts_found = []
            for post in all_posts_in_period:
                nonce = post.created_at.strftime('%Y-%m-%d-%H-%M').encode()
                recalculated_tag = encryptor.generate_search_tag(user_id, guild_salt, nonce)
                if recalculated_tag == post.search_tag:
                    user_posts_found.append(post)

            # 5. 厳密な検証 (今回はブルームフィルタの性質上、省略可能だが念のため)
            # 実際には、ノンスが不明なためクライアントサイドでの完全な再生成は困難。
            # ブルームフィルタの偽陽性率が十分に低ければ、このステップは省略できる。

            if not user_posts_found:
                await interaction.followup.send(f"ℹ️ {user.mention} による過去{days}日間の匿名投稿は見つかりませんでした。(候補0件)", ephemeral=True)
                success = True
                return

            view = UserPostsView(self.bot, guild_id, user, user_posts_found)
            embed = await view.get_page_embed()

            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            success = True

        except Exception as e:
            db.rollback()
            logger.error(f"An error occurred in 'user_posts' command: {e}", exc_info=True)
            await interaction.followup.send(f"❌ エラーが発生しました: {e}", ephemeral=True)
        finally:
            log = AdminCommandLog(
                guild_id=str(interaction.guild.id),
                command_name='user_posts',
                executed_by=str(interaction.user.id),
                target_user_id=encrypted_user_id,
                log_salt_type='user_posts',
                params={'days': days, 'deleted_status': deleted_status.value},
                success=success
            )
            db.add(log)
            db.commit()
            db.close()

    @app_commands.command(name="bulk_delete", description="条件を指定して匿名投稿をまとめて削除します。")
    @app_commands.describe(
        scope="削除対象の範囲",
        condition_type="削除する投稿の条件タイプ",
        condition_value="条件の値",
        dry_run="実行前に件数とプレビューのみ表示するか (デフォルト: True)"
    )
    @app_commands.default_permissions(manage_guild=True)
    async def bulk_delete(self, interaction: discord.Interaction, scope: Scope, condition_type: ConditionType, condition_value: str, dry_run: bool = True):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        success = False
        target_user_id_encrypted = None
        
        try:
            guild_id = str(interaction.guild.id)
            
            query = db.query(AnonymousPost).filter(
                AnonymousPost.guild_id == guild_id,
                AnonymousPost.deleted_at.is_(None)
            )

            if scope == Scope.current_channel:
                query = query.filter(AnonymousPost.channel_id == str(interaction.channel_id))
            
            posts_to_delete = []
            if condition_type == ConditionType.user:
                try:
                    target_user = await commands.UserConverter().convert(interaction, condition_value)
                except commands.UserNotFound:
                    await interaction.followup.send("❌ 指定されたユーザーが見つかりません。", ephemeral=True)
                    return
                user_id = str(target_user.id)
                
                config_cog: ConfigCog = self.bot.get_cog("ConfigCog")
                settings = await config_cog.get_guild_settings(db, guild_id)
                guild_salt = settings['guild_salt']
                target_user_id_encrypted = encryptor.encrypt_user_id(user_id, encryptor.current_key_version, b'logging_salt_for_bulk_delete')
                
                all_posts_in_scope = query.all()
                for post in all_posts_in_scope:
                    nonce = post.created_at.strftime('%Y-%m-%d-%H-%M').encode()
                    recalculated_tag = encryptor.generate_search_tag(user_id, guild_salt, nonce)
                    if recalculated_tag == post.search_tag:
                        posts_to_delete.append(post)
            else:
                if condition_type == ConditionType.messages:
                    limit = int(condition_value)
                    query = query.order_by(AnonymousPost.created_at.desc()).limit(limit)
                elif condition_type == ConditionType.hours:
                    hours = int(condition_value)
                    since = discord.utils.utcnow() - timedelta(hours=hours)
                    query = query.filter(AnonymousPost.created_at >= since)
                elif condition_type == ConditionType.contains:
                    query = query.filter(AnonymousPost.content.contains(condition_value))
                elif condition_type == ConditionType.pattern:
                    all_posts_in_scope = query.all()
                    try:
                        pattern = re.compile(condition_value)
                        posts_to_delete = [p for p in all_posts_in_scope if pattern.search(p.content)]
                    except re.error as e:
                        await interaction.followup.send(f"❌ 正規表現エラー: {e}", ephemeral=True)
                        return
                elif condition_type == ConditionType.anonymous_id:
                    query = query.filter(AnonymousPost.anonymous_id == condition_value)
                elif condition_type == ConditionType.converted_only:
                    query = query.filter(AnonymousPost.is_converted.is_(True))
                elif condition_type == ConditionType.direct_only:
                    query = query.filter(AnonymousPost.original_message_id.is_(None))
                
                if condition_type != ConditionType.pattern:
                    posts_to_delete = query.all()

            if not posts_to_delete:
                await interaction.followup.send("ℹ️ 削除対象の投稿は見つかりませんでした。", ephemeral=True)
                success = True
                return

            if dry_run:
                embed = discord.Embed(title="一括削除プレビュー (Dry Run)", color=discord.Color.yellow())
                embed.description = f"**{len(posts_to_delete)}** 件の投稿が削除対象です。"
                for post in posts_to_delete[:5]:
                    content_preview = (post.content[:70] + '...') if len(post.content) > 70 else post.content
                    channel = self.bot.get_channel(int(post.channel_id))
                    channel_name = channel.name if channel else "不明"
                    embed.add_field(name=f"#{channel_name} の投稿", value=content_preview, inline=False)
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                post_ids_to_delete = [p.id for p in posts_to_delete]
                
                db.query(AnonymousPost).filter(AnonymousPost.id.in_(post_ids_to_delete)).update({
                    'deleted_at': discord.utils.utcnow(),
                    'deleted_by': str(interaction.user.id)
                }, synchronize_session=False)

                history = BulkDeleteHistory(
                    guild_id=guild_id,
                    executed_by=str(interaction.user.id),
                    target_type='anonymous_post',
                    scope=scope.value,
                    conditions={'type': condition_type.value, 'value': condition_value},
                    deleted_count=len(post_ids_to_delete),
                    dry_run=False
                )
                db.add(history)
                db.commit()
                
                await interaction.followup.send(f"✅ {len(post_ids_to_delete)} 件の投稿を論理削除しました。", ephemeral=True)
            
            success = True

        except Exception as e:
            db.rollback()
            logger.error(f"An error occurred in 'bulk_delete' command: {e}", exc_info=True)
            await interaction.followup.send(f"❌ エラーが発生しました: {e}", ephemeral=True)
        finally:
            log = AdminCommandLog(
                guild_id=str(interaction.guild.id),
                command_name='bulk_delete',
                executed_by=str(interaction.user.id),
                target_user_id=target_user_id_encrypted,
                log_salt_type='bulk_delete' if condition_type == ConditionType.user else None,
                params={'scope': scope.value, 'condition_type': condition_type.value, 'condition_value': condition_value, 'dry_run': dry_run},
                success=success
            )
            db.add(log)
            db.commit()
            db.close()

    @app_commands.command(name="admin_logs", description="管理コマンドの実行ログを検索します。")
    @app_commands.describe(
        command_name="検索するコマンド名",
        target_user="コマンドの対象となったユーザー",
        user="実行したユーザー",
        days="検索する日数（1-90、デフォルト30）"
    )
    @app_commands.default_permissions(manage_guild=True)
    async def admin_logs(self, interaction: discord.Interaction, command_name: AdminCommands = None, target_user: discord.User = None, user: discord.User = None, days: int = 30):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        try:
            if not 1 <= days <= 90:
                await interaction.followup.send("❌ 日数は1から90の間で指定してください。", ephemeral=True)
                return

            guild_id = str(interaction.guild.id)
            
            query = db.query(AdminCommandLog).filter(AdminCommandLog.guild_id == guild_id)

            start_date = discord.utils.utcnow() - timedelta(days=days)
            query = query.filter(AdminCommandLog.created_at >= start_date)

            if command_name:
                query = query.filter(AdminCommandLog.command_name == command_name.value)
            
            if user:
                query = query.filter(AdminCommandLog.executed_by == str(user.id))

            if target_user:
                encrypted_target_id = encryptor.encrypt_user_id(str(target_user.id), encryptor.current_key_version, b'logging_salt_for_admin_logs')
                query = query.filter(AdminCommandLog.target_user_id == encrypted_target_id)

            total_logs = query.count()
            logs = query.order_by(AdminCommandLog.created_at.asc()).all()

            if not logs:
                await interaction.followup.send("ℹ️ 指定された条件のログは見つかりませんでした。", ephemeral=True)
                return

            title = f"管理コマンド実行ログ (過去{days}日間)"
            view = AdminLogView(self.bot, guild_id, logs, total_logs, title)
            embed = await view.get_page_embed()
            
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)

        except Exception as e:
            logger.error(f"An error occurred in 'admin_logs' command: {e}", exc_info=True)
            await interaction.followup.send(f"❌ エラーが発生しました: {e}", ephemeral=True)
        finally:
            db.close()

    @app_commands.command(name="global_trace", description="[BOTオーナー専用] メッセージIDから投稿者をグローバル検索します。")
    @app_commands.describe(message_id="特定したい匿名投稿のメッセージID")
    @commands.is_owner()
    async def global_trace(self, interaction: discord.Interaction, message_id: str):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        try:
            # 1. まず、message_idがoriginal_message_idである可能性を考慮して検索
            event = db.query(GlobalChatEvents).filter_by(original_message_id=message_id).first()
            if event:
                post = db.query(AnonymousPost).filter_by(message_id=event.original_message_id).first()
            else:
                # 2. 次に、転送されたメッセージIDとしてforwarded_mapを検索
                event = db.query(GlobalChatEvents).filter(cast(GlobalChatEvents.forwarded_map, String).like(f'%"{message_id}"%')).first()
                if event:
                    post = db.query(AnonymousPost).filter_by(message_id=event.original_message_id).first()
                else:
                    # 3. 最後に、グローバルチャットではない通常の投稿として検索
                    post = db.query(AnonymousPost).filter_by(message_id=message_id).first()
            
            if not post:
                await interaction.followup.send("❌ 指定されたメッセージIDの投稿が見つかりません。", ephemeral=True)
                return

            salt_bytes = base64.b64decode(post.encryption_salt)
            decrypted_user_id = encryptor.decrypt_user_id_with_salt(post.user_id_encrypted, salt_bytes)

            if decrypted_user_id:
                user = await self.bot.fetch_user(int(decrypted_user_id))
                
                embed = discord.Embed(title="グローバル投稿者特定結果", color=0xffa500)  # Orange
                embed.set_author(name=f"{user.name} ({user.id})", icon_url=user.display_avatar.url)
                
                original_guild = self.bot.get_guild(int(post.guild_id))
                original_channel = self.bot.get_channel(int(post.channel_id))

                embed.add_field(name="メッセージID", value=f"[{message_id}](https://discord.com/channels/{post.guild_id}/{post.channel_id}/{message_id})", inline=False)
                embed.add_field(name="投稿者", value=f"{user.mention} (`{user.id}`)", inline=False)
                embed.add_field(name="投稿サーバー", value=f"{original_guild.name if original_guild else '不明なサーバー'} (`{post.guild_id}`)", inline=False)
                embed.add_field(name="投稿チャンネル", value=f"{original_channel.name if original_channel else '不明なチャンネル'} (`{post.channel_id}`)", inline=False)
                
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.followup.send("❌ ユーザーIDの復号に失敗しました。", ephemeral=True)

        except Exception as e:
            logger.error(f"Error in global_trace command: {e}", exc_info=True)
            await interaction.followup.send(f"❌ エラーが発生しました: {e}", ephemeral=True)
        finally:
            db.close()

    @app_commands.command(name="global_user_posts", description="[BOTオーナー専用] ユーザーの投稿をグローバル検索します。")
    @app_commands.describe(user="検索対象のユーザー", days="検索日数 (デフォルト: 30)")
    @commands.is_owner()
    async def global_user_posts(self, interaction: discord.Interaction, user: discord.User, days: int = 30):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        try:
            if not 1 <= days <= 90:
                await interaction.followup.send("❌ 日数は1から90の間で指定してください。", ephemeral=True)
                return

            user_id = str(user.id)
            start_date = discord.utils.utcnow() - timedelta(days=days)

            all_posts_in_period = db.query(AnonymousPost).filter(AnonymousPost.created_at >= start_date).all()

            if not all_posts_in_period:
                await interaction.followup.send(f"ℹ️ 過去{days}日間に検索対象の投稿はありませんでした。", ephemeral=True)
                return

            # メモリ上でglobal_user_signatureを再計算して検証
            candidate_posts = []
            for post in all_posts_in_period:
                nonce = post.created_at.strftime('%Y-%m-%d-%H-%M').encode()
                # 投稿に使われたキーバージョンで署名を再計算
                recalculated_sig = encryptor.generate_global_user_signature(user_id, post.key_version, nonce)
                if recalculated_sig == post.global_user_signature:
                    candidate_posts.append(post)

            if not candidate_posts:
                await interaction.followup.send(f"ℹ️ {user.mention} による過去{days}日間の匿名投稿は見つかりませんでした。", ephemeral=True)
                return

            # 厳密な検証は省略（ブルームフィルタの偽陽性を許容）
            
            view = UserPostsView(self.bot, "N/A", user, candidate_posts)
            embed = await view.get_page_embed()
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in global_user_posts command: {e}", exc_info=True)
            await interaction.followup.send(f"❌ エラーが発生しました: {e}", ephemeral=True)
        finally:
            db.close()

    @app_commands.command(name="delete", description="指定した匿名投稿を削除します。")
    @app_commands.describe(message_id="削除するメッセージID")
    async def delete(self, interaction: discord.Interaction, message_id: str):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        try:
            user_id = str(interaction.user.id)
            is_owner = await self.bot.is_owner(interaction.user)

            # まず、どのサーバーの投稿かを問わずメッセージIDで投稿を検索
            post_to_delete = db.query(AnonymousPost).filter_by(message_id=message_id, deleted_at=None).first()

            if not post_to_delete:
                await interaction.followup.send("❌ 削除対象の投稿が見つからないか、既に削除されています。", ephemeral=True)
                return

            # 権限チェック
            salt_bytes = base64.b64decode(post_to_delete.encryption_salt)
            decrypted_user_id = encryptor.decrypt_user_id_with_salt(post_to_delete.user_id_encrypted, salt_bytes)
            is_author = (decrypted_user_id == user_id)
            is_local_admin = interaction.guild and interaction.user.guild_permissions.manage_messages and post_to_delete.guild_id == str(interaction.guild.id)

            if not is_author and not is_local_admin and not is_owner:
                await interaction.followup.send("❌ この投稿を削除する権限がありません。", ephemeral=True)
                return

            # Discord上のメッセージを削除
            try:
                target_guild = self.bot.get_guild(int(post_to_delete.guild_id))
                if target_guild:
                    target_channel = target_guild.get_channel_or_thread(int(post_to_delete.thread_id or post_to_delete.channel_id))
                    if target_channel:
                        message = await target_channel.fetch_message(int(post_to_delete.message_id))
                        await message.delete()
            except discord.NotFound:
                pass  # 既に削除済み
            except discord.Forbidden:
                logger.warning(f"Failed to delete message {post_to_delete.message_id} in guild {post_to_delete.guild_id}: Missing Permissions")
            except Exception as e:
                logger.error(f"Error deleting message from Discord: {e}")

            # DBを論理削除
            post_to_delete.deleted_at = discord.utils.utcnow()
            post_to_delete.deleted_by = user_id
            
            # グローバルチャット連携の削除
            event = db.query(GlobalChatEvents).filter_by(original_message_id=message_id).first()
            if event and event.forwarded_map:
                for guild_id_str, msg_id_str in event.forwarded_map.items():
                    try:
                        target_guild = self.bot.get_guild(int(guild_id_str))
                        if not target_guild:
                            continue
                        
                        # forwarded_mapには転送先サーバーIDしかないので、チャンネルIDをDBから引く
                        gcl_channel = db.query(GlobalChatChannel).filter_by(guild_id=guild_id_str, room_id=event.room_id).first()
                        if not gcl_channel:
                            continue

                        target_channel = target_guild.get_channel(int(gcl_channel.channel_id))
                        if target_channel:
                            message_to_delete = await target_channel.fetch_message(int(msg_id_str))
                            await message_to_delete.delete()
                    except discord.NotFound:
                        pass
                    except discord.Forbidden:
                        logger.warning(f"Failed to delete forwarded message {msg_id_str} in guild {guild_id_str}: Missing Permissions")
                    except Exception as e:
                        logger.error(f"Error deleting forwarded message {msg_id_str}: {e}")

            db.commit()
            await interaction.followup.send("✅ 投稿を削除しました。", ephemeral=True)

            # ログ
            # log_embed = discord.Embed(title="匿名投稿削除", color=discord.Color.red(), timestamp=discord.utils.utcnow())
            # log_embed.add_field(name="匿名ID", value=post_to_delete.anonymous_id, inline=False)
            # log_embed.add_field(name="実行者", value=interaction.user.mention, inline=False)
            # log_embed.add_field(name="対象メッセージID", value=message_id, inline=False)
            # await self._send_log_message(post_to_delete.guild_id, log_embed)

        except Exception as e:
            db.rollback()
            logger.error(f"Error in delete command: {e}", exc_info=True)
            await interaction.followup.send("❌ エラーが発生しました。", ephemeral=True)
        finally:
            db.close()


async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCog(bot))