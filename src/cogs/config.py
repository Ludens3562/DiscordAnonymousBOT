import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy.orm import Session
import json
import os
import base64

from models import GuildSettings, ConfigHistory, NgWord
from database import get_db

# 仕様書の付録にあるデフォルト設定
DEFAULT_SETTINGS = {
    "rate_limit_count": 3,
    "rate_limit_window": 60,
    "rate_limit_timeslot": 3600,
    "max_message_length": 2000,
    "anon_id_duration": 86400,
    "anon_id_format": "匿名ユーザー_{id}",
    "conversion_timeout": 30,
    "conversion_enabled": True,
    "ngword_action": "block",
    "log_retention_days": 180,
    "anonymous_id_reset_mode": "daily",
    "bulk_delete_max_count": 1000,
    "bulk_delete_require_reason_threshold": 10,
    "bulk_delete_notify_admins": True,
    # guild_saltはここには含めず、動的に生成する
    "conversion_channels": [],
}

# 設定キーの説明
SETTING_DESCRIPTIONS = {
    "rate_limit_count": "投稿のレート制限値",
    "rate_limit_window": "レート制限の期間(秒)",
    "rate_limit_timeslot": "レート制限のタイムスロット(秒)",
    "max_message_length": "最大メッセージ長(文字)",
    "anon_id_duration": "匿名IDの有効期間(秒)",
    "anon_id_format": "匿名IDのフォーマット",
    "conversion_timeout": "誤投稿変換の有効時間(秒)",
    "conversion_enabled": "誤投稿変換機能",
    "ngword_action": "NGワード検知時のアクション",
    "log_retention_days": "ログの保持期間(日)",
    "anonymous_id_reset_mode": "匿名IDのリセットモード",
    "bulk_delete_max_count": "一括削除の最大件数",
    "bulk_delete_require_reason_threshold": "理由必須となる一括削除の閾値",
    "bulk_delete_notify_admins": "一括削除時の管理者通知",
    "conversion_channels": "誤投稿変換の対象チャンネル",
}


class ConfigCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.settings_cache = {}

    async def get_guild_settings(self, db: Session, guild_id: str) -> dict:
        """ギルドの設定を取得または作成する(キャッシュ対応)"""
        if guild_id in self.settings_cache:
            return self.settings_cache[guild_id]

        settings_model = db.query(GuildSettings).filter_by(guild_id=guild_id).first()
        
        if not settings_model:
            new_settings = DEFAULT_SETTINGS.copy()
            # 新規作成時にソルトを生成
            new_settings['guild_salt'] = base64.b64encode(os.urandom(16)).decode()
            settings_model = GuildSettings(guild_id=guild_id, settings=new_settings)
            db.add(settings_model)
            db.commit()
            self.settings_cache[guild_id] = new_settings
            return new_settings

        # 既存の設定にソルトがない場合は追加
        if 'guild_salt' not in settings_model.settings:
            new_settings = settings_model.settings.copy()
            new_settings['guild_salt'] = base64.b64encode(os.urandom(16)).decode()
            settings_model.settings = new_settings
            db.commit()
            self.settings_cache[guild_id] = new_settings
            return new_settings
        
        self.settings_cache[guild_id] = settings_model.settings
        return settings_model.settings

    async def key_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        db: Session = next(get_db())
        try:
            guild_id = str(interaction.guild.id)
            settings = await self.get_guild_settings(db, guild_id)
            
            choices = []
            # guild_saltは除外
            settable_keys = {k: v for k, v in settings.items() if k != 'guild_salt'}
            
            for key, value in settable_keys.items():
                if current.lower() in key.lower():
                    description = SETTING_DESCRIPTIONS.get(key, key)
                    # 説明と現在の値を表示
                    value = settable_keys.get(key)
                    display_value = value if value not in [None, ""] else "未設定"
                    choices.append(app_commands.Choice(name=f"{description} (現在値: {display_value})", value=key))
            return choices[:25]  # Discordの制限
        finally:
            db.close()

    @app_commands.command(name="config", description="サーバーの設定を管理します。引数なしで実行すると設定一覧を表示します。")
    @app_commands.describe(key="設定キー", value="設定値")
    @app_commands.autocomplete(key=key_autocomplete)
    @app_commands.default_permissions(manage_guild=True)
    async def config(self, interaction: discord.Interaction, key: str = None, value: str = None):
        """設定管理コマンド"""
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        try:
            guild_id = str(interaction.guild.id)
            settings_data = await self.get_guild_settings(db, guild_id)

            # 引数なし：設定一覧表示
            if key is None and value is None:
                embed = discord.Embed(title=f"{interaction.guild.name} の設定", color=discord.Color.green())
                # SETTING_DESCRIPTIONS の順序で表示を固定し、意図しないキーが表示されるのを防ぐ
                for key, description in SETTING_DESCRIPTIONS.items():
                    value = settings_data.get(key, DEFAULT_SETTINGS.get(key))
                    
                    # 表示用に値を整形
                    display_value = value
                    if isinstance(value, list) and not value:
                        display_value = "未設定"
                    elif value in [None, ""]:
                        display_value = "未設定"

                    embed.add_field(name=f"{description} ({key})", value=f"`{display_value}`", inline=False)
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # keyとvalueあり：設定変更
            elif key is not None and value is not None:
                if key == 'guild_salt':
                    await interaction.followup.send("このキーは変更できません。", ephemeral=True)
                    return
                if key not in settings_data:
                    await interaction.followup.send(f"設定キー '{key}' は存在しません。", ephemeral=True)
                    return
                
                # 型変換を試みる
                original_type = type(DEFAULT_SETTINGS.get(key))
                try:
                    if original_type == bool:
                        new_value = value.lower() in ['true', '1', 'yes']
                    else:
                        new_value = original_type(value)
                except (ValueError, TypeError):
                    await interaction.followup.send(f"値の型が不正です。'{key}' は {original_type.__name__} 型である必要があります。", ephemeral=True)
                    return

                guild_settings = db.query(GuildSettings).filter_by(guild_id=guild_id).first()
                old_value = guild_settings.settings.get(key)

                # 履歴を記録
                history = ConfigHistory(
                    guild_id=guild_id,
                    key=key,
                    old_value=json.dumps(old_value),
                    new_value=json.dumps(new_value),
                    changed_by=str(interaction.user.id)
                )
                db.add(history)

                # JSONBを更新するために新しい辞書を作成
                new_settings = guild_settings.settings.copy()
                new_settings[key] = new_value
                guild_settings.settings = new_settings
                
                db.commit()
                
                # キャッシュを更新
                self.settings_cache[guild_id] = new_settings
                
                await interaction.followup.send(f"設定 '{key}' を `{new_value}` に更新しました。", ephemeral=True)

            # 引数が不完全な場合
            else:
                await interaction.followup.send("設定を変更するには、`key` と `value` の両方を指定してください。", ephemeral=True)

        except Exception as e:
            db.rollback()
            await interaction.followup.send(f"エラーが発生しました: {e}", ephemeral=True)
        finally:
            db.close()

    conversion = app_commands.Group(name="conversionchannel", description="誤投稿変換機能の対象チャンネルを管理します。", default_permissions=discord.Permissions(manage_guild=True))

    @conversion.command(name="add", description="変換対象にチャンネルを追加します。")
    @app_commands.describe(channel="追加するテキストチャンネル")
    @app_commands.default_permissions(manage_guild=True)
    async def conversion_add(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        try:
            guild_id = str(interaction.guild.id)
            settings = await self.get_guild_settings(db, guild_id)
            
            conversion_channels = settings.get("conversion_channels", [])
            
            if str(channel.id) in conversion_channels:
                await interaction.followup.send(f"{channel.mention} は既に対象チャンネルです。", ephemeral=True)
                return

            conversion_channels.append(str(channel.id))
            
            guild_settings = db.query(GuildSettings).filter_by(guild_id=guild_id).first()
            new_settings = guild_settings.settings.copy()
            new_settings["conversion_channels"] = conversion_channels
            guild_settings.settings = new_settings
            
            db.commit()
            self.settings_cache[guild_id] = new_settings
            
            await interaction.followup.send(f"{channel.mention} を変換対象チャンネルに追加しました。", ephemeral=True)
        except Exception as e:
            db.rollback()
            await interaction.followup.send(f"エラーが発生しました: {e}", ephemeral=True)
        finally:
            db.close()

    @conversion.command(name="remove", description="変換対象からチャンネルを削除します。")
    @app_commands.describe(channel="削除するテキストチャンネル")
    @app_commands.default_permissions(manage_guild=True)
    async def conversion_remove(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        try:
            guild_id = str(interaction.guild.id)
            settings = await self.get_guild_settings(db, guild_id)
            
            conversion_channels = settings.get("conversion_channels", [])
            
            if str(channel.id) not in conversion_channels:
                await interaction.followup.send(f"{channel.mention} は対象チャンネルではありません。", ephemeral=True)
                return

            conversion_channels.remove(str(channel.id))
            
            guild_settings = db.query(GuildSettings).filter_by(guild_id=guild_id).first()
            new_settings = guild_settings.settings.copy()
            new_settings["conversion_channels"] = conversion_channels
            guild_settings.settings = new_settings
            
            db.commit()
            self.settings_cache[guild_id] = new_settings
            
            await interaction.followup.send(f"{channel.mention} を変換対象チャンネルから削除しました。", ephemeral=True)
        except Exception as e:
            db.rollback()
            await interaction.followup.send(f"エラーが発生しました: {e}", ephemeral=True)
        finally:
            db.close()

    @conversion.command(name="list", description="変換対象のチャンネル一覧を表示します。")
    @app_commands.default_permissions(manage_guild=True)
    async def conversion_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        try:
            guild_id = str(interaction.guild.id)
            settings = await self.get_guild_settings(db, guild_id)
            conversion_channels = settings.get("conversion_channels", [])
            
            if not conversion_channels:
                await interaction.followup.send("変換対象のチャンネルはありません。", ephemeral=True)
                return
            
            channel_mentions = [f"<#{channel_id}>" for channel_id in conversion_channels]
            embed = discord.Embed(title="変換対象チャンネル一覧", description="\n".join(channel_mentions), color=discord.Color.blue())
            await interaction.followup.send(embed=embed, ephemeral=True)
        finally:
            db.close()

    # NGワード管理コマンド
    ngword = app_commands.Group(name="ngword", description="NGワードを管理します。", default_permissions=discord.Permissions(manage_guild=True))

    @ngword.command(name="add", description="NGワードを追加します。")
    @app_commands.describe(
        word="追加するNGワード",
        match_type="一致種別を選択してください"
    )
    @app_commands.choices(match_type=[
        app_commands.Choice(name="部分一致 (partial)", value="partial"),
        app_commands.Choice(name="完全一致 (exact)", value="exact"),
        app_commands.Choice(name="正規表現 (regex)", value="regex"),
    ])
    @app_commands.default_permissions(manage_guild=True)
    async def ngword_add(self, interaction: discord.Interaction, word: str, match_type: str):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        try:
            guild_id = str(interaction.guild.id)
            
            existing_word = db.query(NgWord).filter_by(guild_id=guild_id, word=word, match_type=match_type).first()
            if existing_word:
                await interaction.followup.send(f"NGワード `{word}` ({match_type}) は既に登録されています。", ephemeral=True)
                return

            new_ng_word = NgWord(
                guild_id=guild_id,
                word=word,
                match_type=match_type,
                added_by=str(interaction.user.id)
            )
            db.add(new_ng_word)
            db.commit()
            
            await interaction.followup.send(f"NGワード `{word}` ({match_type}) を追加しました。", ephemeral=True)
        except Exception as e:
            db.rollback()
            await interaction.followup.send(f"エラーが発生しました: {e}", ephemeral=True)
        finally:
            db.close()

    @ngword.command(name="remove", description="NGワードを削除します。")
    @app_commands.describe(
        word="削除するNGワード",
        match_type="一致種別"
    )
    @app_commands.choices(match_type=[
        app_commands.Choice(name="部分一致 (partial)", value="partial"),
        app_commands.Choice(name="完全一致 (exact)", value="exact"),
        app_commands.Choice(name="正規表現 (regex)", value="regex"),
    ])
    @app_commands.default_permissions(manage_guild=True)
    async def ngword_remove(self, interaction: discord.Interaction, word: str, match_type: str):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        try:
            guild_id = str(interaction.guild.id)
            
            ng_word_to_delete = db.query(NgWord).filter_by(guild_id=guild_id, word=word, match_type=match_type).first()
            
            if not ng_word_to_delete:
                await interaction.followup.send(f"NGワード `{word}` ({match_type}) は見つかりませんでした。", ephemeral=True)
                return

            db.delete(ng_word_to_delete)
            db.commit()
            
            await interaction.followup.send(f"NGワード `{word}` ({match_type}) を削除しました。", ephemeral=True)
        except Exception as e:
            db.rollback()
            await interaction.followup.send(f"エラーが発生しました: {e}", ephemeral=True)
        finally:
            db.close()

    @ngword.command(name="list", description="登録されているNGワードの一覧を表示します。")
    @app_commands.default_permissions(manage_guild=True)
    async def ngword_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        try:
            guild_id = str(interaction.guild.id)
            ng_words = db.query(NgWord).filter_by(guild_id=guild_id).order_by(NgWord.added_at).all()
            
            if not ng_words:
                await interaction.followup.send("登録されているNGワードはありません。", ephemeral=True)
                return
            
            embed = discord.Embed(title="NGワード一覧", color=discord.Color.orange())
            description = ""
            for ng_word in ng_words:
                description += f"- `{ng_word.word}` ({ng_word.match_type})\n"
            
            embed.description = description
            await interaction.followup.send(embed=embed, ephemeral=True)
        finally:
            db.close()


async def setup(bot: commands.Bot):
    await bot.add_cog(ConfigCog(bot))
