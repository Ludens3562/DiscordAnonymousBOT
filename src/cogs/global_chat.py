import logging
import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy.orm import Session

from database import get_db
from models import (
    GlobalChatRoom,
    GlobalChatChannel,
    GlobalChatBan,
    GlobalNgWord,
)

logger = logging.getLogger(__name__)


async def is_owner_check(interaction: discord.Interaction) -> bool:
    """BOTのオーナーであるかを確認するチェック関数"""
    return await interaction.client.is_owner(interaction.user)


class GlobalChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --- サーバー管理者向けコマンド ---
    gcr = app_commands.Group(
        name="gcr",
        description="グローバルチャットルームへの参加・退出などを管理します。",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    async def room_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        db: Session = next(get_db())
        try:
            rooms = db.query(GlobalChatRoom).filter(GlobalChatRoom.name.ilike(f"%{current}%")).limit(25).all()
            return [app_commands.Choice(name=room.name, value=room.name) for room in rooms]
        finally:
            db.close()

    @gcr.command(name="join", description="チャンネルをグローバルチャットルームに参加させます。")
    @app_commands.describe(room="参加するルーム名")
    @app_commands.autocomplete(room=room_autocomplete)
    async def join(self, interaction: discord.Interaction, room: str):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        try:
            target_room = db.query(GlobalChatRoom).filter_by(name=room).first()
            if not target_room:
                await interaction.followup.send(f"❌ ルーム `{room}` は存在しません。", ephemeral=True)
                return

            channel_id = str(interaction.channel_id)
            existing_channel = db.query(GlobalChatChannel).filter_by(channel_id=channel_id).first()
            if existing_channel:
                await interaction.followup.send("❌ このチャンネルは既に他のルームに参加しています。", ephemeral=True)
                return

            if not isinstance(interaction.channel, discord.TextChannel):
                await interaction.followup.send("❌ テキストチャンネルでのみ参加できます。", ephemeral=True)
                return

            # Webhookの作成
            webhooks = await interaction.channel.webhooks()
            webhook = discord.utils.find(lambda wh: wh.user == self.bot.user, webhooks)
            if webhook is None:
                webhook = await interaction.channel.create_webhook(name=f"{self.bot.user.name} Webhook")

            new_gcl_channel = GlobalChatChannel(
                channel_id=channel_id,
                guild_id=str(interaction.guild_id),
                room_id=target_room.id,
                webhook_url=webhook.url
            )
            db.add(new_gcl_channel)
            db.commit()

            await interaction.followup.send(f"✅ このチャンネルをルーム `{room}` に参加させました。", ephemeral=True)

        except discord.errors.Forbidden:
            db.rollback()
            logger.warning(f"Missing 'Manage Webhooks' permission in channel {interaction.channel_id} for /gcr join.")
            await interaction.followup.send("❌ Webhookを管理する権限がBOTにありません。このチャンネルで「ウェブフックの管理」権限をBOTに付与してください。", ephemeral=True)
        except Exception as e:
            db.rollback()
            logger.error(f"Error in /gcr join: {e}", exc_info=True)
            await interaction.followup.send("❌ 不明なエラーが発生しました。", ephemeral=True)
        finally:
            db.close()

    @gcr.command(name="leave", description="チャンネルをグローバルチャットルームから退出させます。")
    async def leave(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        try:
            channel_id = str(interaction.channel_id)
            gcl_channel = db.query(GlobalChatChannel).filter_by(channel_id=channel_id).first()

            if not gcl_channel:
                await interaction.followup.send("❌ このチャンネルはどのルームにも参加していません。", ephemeral=True)
                return

            db.delete(gcl_channel)
            db.commit()

            await interaction.followup.send("✅ このチャンネルをルームから退出させました。", ephemeral=True)

        except Exception as e:
            db.rollback()
            logger.error(f"Error in /gcr leave: {e}", exc_info=True)
            await interaction.followup.send("❌ エラーが発生しました。", ephemeral=True)
        finally:
            db.close()

    @gcr.command(name="list", description="サーバーが参加しているグローバルチャットルームの一覧を表示します。")
    async def list_rooms(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        try:
            guild_id = str(interaction.guild.id)
            
            # サーバー内の参加チャンネルを取得
            joined_channels = db.query(GlobalChatChannel).filter_by(guild_id=guild_id).all()

            if not joined_channels:
                await interaction.followup.send("ℹ️ このサーバーはどのグローバルチャットルームにも参加していません。", ephemeral=True)
                return

            # ルーム情報を取得
            room_ids = [jc.room_id for jc in joined_channels]
            rooms = db.query(GlobalChatRoom).filter(GlobalChatRoom.id.in_(room_ids)).all()
            room_map = {room.id: room for room in rooms}

            embed = discord.Embed(title=f"{interaction.guild.name} のグローバルチャット参加状況", color=discord.Color.blue())
            
            for room_id, room in room_map.items():
                channels_in_room = [f"<#{jc.channel_id}>" for jc in joined_channels if jc.room_id == room_id]
                embed.add_field(
                    name=f"ルーム: {room.name}",
                    value="参加チャンネル:\n" + "\n".join(channels_in_room),
                    inline=False
                )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in /gcr list: {e}", exc_info=True)
            await interaction.followup.send("❌ エラーが発生しました。", ephemeral=True)
        finally:
            db.close()

    # --- BOTオーナー向けコマンド ---
    owner = app_commands.Group(
        name="owner",
        description="BOTオーナー向けの管理コマンドです。",
        guild_only=True,  # TODO: オーナー専用サーバーでのみ表示するなどの制御を追加
    )

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Cog内のコマンドでエラーが発生した際のグローバルエラーハンドラ"""
        # ownerグループのコマンドで発生したCheckFailureを処理
        if isinstance(error, app_commands.CheckFailure) and interaction.command and hasattr(interaction.command, 'parent') and interaction.command.parent == self.owner:
            message = "❌ このコマンドはBOTのオーナーのみが実行できます。"
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        else:
            # 既に各コマンドで個別にエラーハンドリングされているため、ここではログ出力のみ
            logger.error(f"An unhandled error occurred in GlobalChatCog command '{interaction.command.name if interaction.command else 'unknown'}': {error}", exc_info=True)

    @owner.command(name="gcr_create", description="新しいグローバルチャットルームを作成します。")
    @app_commands.describe(name="ルーム名", description="ルームの説明")
    @app_commands.check(is_owner_check)
    async def gcr_create(self, interaction: discord.Interaction, name: str, description: str = None):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        try:
            existing_room = db.query(GlobalChatRoom).filter_by(name=name).first()
            if existing_room:
                await interaction.followup.send(f"❌ ルーム名 `{name}` は既に使用されています。", ephemeral=True)
                return

            new_room = GlobalChatRoom(
                name=name,
                description=description,
                created_by=str(interaction.user.id)
            )
            db.add(new_room)
            db.commit()

            await interaction.followup.send(f"✅ 新しいグローバルチャットルーム `{name}` を作成しました。", ephemeral=True)

        except Exception as e:
            db.rollback()
            logger.error(f"Error in /owner gcr_create: {e}", exc_info=True)
            await interaction.followup.send("❌ エラーが発生しました。", ephemeral=True)
        finally:
            db.close()

    @owner.command(name="gcr_delete", description="グローバルチャットルームを削除します。")
    @app_commands.describe(room="削除するルーム名")
    @app_commands.autocomplete(room=room_autocomplete)
    @app_commands.check(is_owner_check)
    async def gcr_delete(self, interaction: discord.Interaction, room: str):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        try:
            target_room = db.query(GlobalChatRoom).filter_by(name=room).first()
            if not target_room:
                await interaction.followup.send(f"❌ ルーム `{room}` は存在しません。", ephemeral=True)
                return

            # 関連するチャンネルをすべて退出させる
            db.query(GlobalChatChannel).filter_by(room_id=target_room.id).delete()
            
            db.delete(target_room)
            db.commit()

            await interaction.followup.send(f"✅ ルーム `{room}` を削除しました。", ephemeral=True)

        except Exception as e:
            db.rollback()
            logger.error(f"Error in /owner gcr_delete: {e}", exc_info=True)
            await interaction.followup.send("❌ エラーが発生しました。", ephemeral=True)
        finally:
            db.close()

    @owner.command(name="gcr_list", description="全てのグローバルチャットルームの一覧を表示します。")
    @app_commands.check(is_owner_check)
    async def gcr_list_all(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        try:
            rooms = db.query(GlobalChatRoom).order_by(GlobalChatRoom.created_at).all()
            if not rooms:
                await interaction.followup.send("ℹ️ グローバルチャットルームはまだ作成されていません。", ephemeral=True)
                return

            embed = discord.Embed(title="全グローバルチャットルーム一覧", color=discord.Color.purple())
            for room in rooms:
                embed.add_field(
                    name=f"{room.name} (ID: {room.id})",
                    value=room.description or "説明なし",
                    inline=False
                )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"Error in /owner gcr_list: {e}", exc_info=True)
            await interaction.followup.send("❌ エラーが発生しました。", ephemeral=True)
        finally:
            db.close()
        
    @owner.command(name="gcr_kick", description="サーバーをルームから強制退出させます。")
    @app_commands.describe(server_id="対象サーバーID", room="対象ルーム名", reason="理由")
    @app_commands.autocomplete(room=room_autocomplete)
    @app_commands.check(is_owner_check)
    async def gcr_kick(self, interaction: discord.Interaction, server_id: str, room: str, reason: str = None):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        try:
            target_room = db.query(GlobalChatRoom).filter_by(name=room).first()
            if not target_room:
                await interaction.followup.send(f"❌ ルーム `{room}` は存在しません。", ephemeral=True)
                return

            # サーバーがそのルームに参加しているチャンネルをすべて削除
            deleted_count = db.query(GlobalChatChannel).filter_by(
                guild_id=server_id,
                room_id=target_room.id
            ).delete()
            
            db.commit()

            if deleted_count > 0:
                await interaction.followup.send(f"✅ サーバーID `{server_id}` をルーム `{room}` から強制退出させました。", ephemeral=True)
                # TODO: 対象サーバーの管理者に通知
            else:
                await interaction.followup.send(f"ℹ️ サーバーID `{server_id}` はルーム `{room}` に参加していません。", ephemeral=True)

        except Exception as e:
            db.rollback()
            logger.error(f"Error in /owner gcr_kick: {e}", exc_info=True)
            await interaction.followup.send("❌ エラーが発生しました。", ephemeral=True)
        finally:
            db.close()

    @owner.command(name="gban_server", description="サーバーをグローバルチャットからBANします。")
    @app_commands.describe(server_id="対象サーバーID", room="対象ルーム名（任意）", reason="理由")
    @app_commands.autocomplete(room=room_autocomplete)
    @app_commands.check(is_owner_check)
    async def gban_server(self, interaction: discord.Interaction, server_id: str, room: str = None, reason: str = None):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        try:
            target_room_id = None
            if room:
                target_room = db.query(GlobalChatRoom).filter_by(name=room).first()
                if not target_room:
                    await interaction.followup.send(f"❌ ルーム `{room}` は存在しません。", ephemeral=True)
                    return
                target_room_id = target_room.id

            # 既存のBAN情報を確認
            existing_ban = db.query(GlobalChatBan).filter_by(
                target_id=server_id,
                target_type='GUILD',
                room_id=target_room_id
            ).first()
            if existing_ban:
                await interaction.followup.send(f"ℹ️ サーバー `{server_id}` は既にこのスコープでBANされています。", ephemeral=True)
                return

            new_ban = GlobalChatBan(
                target_id=server_id,
                target_type='GUILD',
                room_id=target_room_id,
                reason=reason,
                banned_by=str(interaction.user.id)
            )
            db.add(new_ban)
            
            # BANされたサーバーを関連するルームから退出させる
            if target_room_id:
                db.query(GlobalChatChannel).filter_by(guild_id=server_id, room_id=target_room_id).delete()
            else:  # グローバルBANの場合はすべてのルームから退出
                db.query(GlobalChatChannel).filter_by(guild_id=server_id).delete()

            db.commit()
            
            scope = f"ルーム `{room}`" if room else "全てのグローバルチャット"
            await interaction.followup.send(f"✅ サーバー `{server_id}` を {scope} からBANしました。", ephemeral=True)

        except Exception as e:
            db.rollback()
            logger.error(f"Error in /owner gban_server: {e}", exc_info=True)
            await interaction.followup.send("❌ エラーが発生しました。", ephemeral=True)
        finally:
            db.close()

    @owner.command(name="gngword_add", description="グローバルNGワードを追加します。")
    @app_commands.describe(word="NGワード", match_type="一致種別")
    @app_commands.choices(match_type=[
        app_commands.Choice(name="部分一致", value="partial"),
        app_commands.Choice(name="完全一致", value="exact"),
        app_commands.Choice(name="正規表現", value="regex"),
    ])
    @app_commands.check(is_owner_check)
    async def gngword_add(self, interaction: discord.Interaction, word: str, match_type: str):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        try:
            existing_word = db.query(GlobalNgWord).filter_by(word=word, match_type=match_type).first()
            if existing_word:
                await interaction.followup.send(f"グローバルNGワード `{word}` ({match_type}) は既に登録されています。", ephemeral=True)
                return

            new_word = GlobalNgWord(
                word=word,
                match_type=match_type,
                added_by=str(interaction.user.id)
            )
            db.add(new_word)
            db.commit()
            await interaction.followup.send(f"✅ グローバルNGワード `{word}` ({match_type}) を追加しました。", ephemeral=True)
        except Exception as e:
            db.rollback()
            logger.error(f"Error in /owner gngword_add: {e}", exc_info=True)
            await interaction.followup.send("❌ エラーが発生しました。", ephemeral=True)
        finally:
            db.close()

    @owner.command(name="gngword_remove", description="グローバルNGワードを削除します。")
    @app_commands.describe(word="削除するNGワード")
    @app_commands.check(is_owner_check)
    async def gngword_remove(self, interaction: discord.Interaction, word: str):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        try:
            target_word = db.query(GlobalNgWord).filter_by(word=word).first()
            if not target_word:
                await interaction.followup.send(f"❌ グローバルNGワード `{word}` は見つかりませんでした。", ephemeral=True)
                return
            
            db.delete(target_word)
            db.commit()
            await interaction.followup.send(f"✅ グローバルNGワード `{word}` を削除しました。", ephemeral=True)
        except Exception as e:
            db.rollback()
            logger.error(f"Error in /owner gngword_remove: {e}", exc_info=True)
            await interaction.followup.send("❌ エラーが発生しました。", ephemeral=True)
        finally:
            db.close()

    @owner.command(name="gngword_list", description="登録されているグローバルNGワードを一覧表示します。")
    @app_commands.check(is_owner_check)
    async def gngword_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        try:
            words = db.query(GlobalNgWord).order_by(GlobalNgWord.created_at).all()
            if not words:
                await interaction.followup.send("ℹ️ グローバルNGワードは登録されていません。", ephemeral=True)
                return

            embed = discord.Embed(title="グローバルNGワード一覧", color=discord.Color.orange())
            description = "\n".join([f"- `{w.word}` ({w.match_type})" for w in words])
            embed.description = description
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"Error in /owner gngword_list: {e}", exc_info=True)
            await interaction.followup.send("❌ エラーが発生しました。", ephemeral=True)
        finally:
            db.close()


async def setup(bot: commands.Bot):
    await bot.add_cog(GlobalChatCog(bot))