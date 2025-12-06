import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert
import json

from models import GlobalSettings, ConfigHistory
from database import get_db

# グローバル設定のデフォルト値
import base64
import os

DEFAULT_GLOBAL_SETTINGS = {
    "rate_limit_count": 10,
    "rate_limit_window": 60,
    "anon_id_format": "匿名さん_{id}",
    "global_chat_salt": None,
}

# グローバル設定キーの説明
GLOBAL_SETTING_DESCRIPTIONS = {
    "rate_limit_count": "グローバルチャットのレート制限回数",
    "rate_limit_window": "グローバルチャットのレート制限期間(秒)",
    "anon_id_format": "グローバルチャットの匿名IDフォーマット",
    "global_chat_salt": "グローバルチャット用のレート制限Salt（自動生成）",
}


class GlobalConfigCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.global_settings_cache = None

    async def get_global_settings(self, db: Session) -> dict:
        """グローバル設定を取得または作成する（競合対策済み）"""
        if self.global_settings_cache and self.global_settings_cache.get("global_chat_salt"):
            return self.global_settings_cache
        
        self.global_settings_cache = None

        try:
            # 1. 各デフォルト設定を個別にチェック・作成する
            for key, default_value in DEFAULT_GLOBAL_SETTINGS.items():
                existing = db.query(GlobalSettings).filter_by(key=key).first()
                if not existing:
                    # 存在しない場合のみ作成
                    new_setting = GlobalSettings(key=key, value=default_value)
                    db.add(new_setting)

            # 2. saltがなければ生成してアトミックに更新する (行ロックを使用)
            salt_setting = db.query(GlobalSettings).filter_by(key="global_chat_salt").with_for_update().first()

            # salt_settingが存在しない場合は作成
            if not salt_setting:
                new_salt = base64.b64encode(os.urandom(16)).decode()
                salt_setting = GlobalSettings(key="global_chat_salt", value=new_salt)
                db.add(salt_setting)
            elif salt_setting.value is None:
                # saltの値がNoneの場合、新しいsaltを生成して設定
                new_salt = base64.b64encode(os.urandom(16)).decode()
                salt_setting.value = new_salt
            
            # 3. トランザクションをコミット
            db.commit()

            # 4. 設定を再読み込みしてキャッシュに保存
            settings_from_db = db.query(GlobalSettings).all()
            self.global_settings_cache = {s.key: s.value for s in settings_from_db}
            return self.global_settings_cache
        
        except Exception as e:
            db.rollback()
            # エラーログなどをここに追加できます
            raise e
        finally:
            # 5. 最終的にDBから全設定を読み直してキャッシュを更新
            all_settings = db.query(GlobalSettings).all()
            self.global_settings_cache = {s.key: s.value for s in all_settings}

    async def key_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        db: Session = next(get_db())
        try:
            settings = await self.get_global_settings(db)
            choices = []
            for key, value in settings.items():
                if current.lower() in key.lower():
                    description = GLOBAL_SETTING_DESCRIPTIONS.get(key, key)
                    display_value = value if value not in [None, ""] else "未設定"
                    choices.append(app_commands.Choice(name=f"{description} (現在値: {display_value})", value=key))
            return choices[:25]
        finally:
            db.close()

    @app_commands.command(name="globalconfig", description="[BOTオーナー専用] グローバル設定を管理します。引数なしで実行すると設定一覧を表示します。")
    @app_commands.describe(key="設定キー", value="設定値")
    @app_commands.autocomplete(key=key_autocomplete)
    @commands.is_owner()
    async def globalconfig(self, interaction: discord.Interaction, key: str = None, value: str = None):
        await interaction.response.defer(ephemeral=True)
        db: Session = next(get_db())
        try:
            settings_data = await self.get_global_settings(db)

            # 引数なし: 設定一覧表示
            if key is None and value is None:
                embed = discord.Embed(title="グローバル設定", color=discord.Color.gold())
                for key, description in GLOBAL_SETTING_DESCRIPTIONS.items():
                    value = settings_data.get(key, DEFAULT_GLOBAL_SETTINGS.get(key))
                    
                    # 表示用に値を整形
                    display_value = value
                    if isinstance(value, list) and not value:
                        display_value = "未設定"
                    elif value in [None, ""]:
                        display_value = "未設定"

                    embed.add_field(name=key, value=f"**{description}**\n`{display_value}`", inline=False)
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # keyとvalueあり: 設定変更
            elif key is not None and value is not None:
                if key not in DEFAULT_GLOBAL_SETTINGS:
                    await interaction.followup.send(f"設定キー '{key}' は存在しません。", ephemeral=True)
                    return

                original_type = type(DEFAULT_GLOBAL_SETTINGS.get(key))
                try:
                    new_value = original_type(value)
                except (ValueError, TypeError):
                    await interaction.followup.send(f"値の型が不正です。'{key}' は {original_type.__name__} 型である必要があります。", ephemeral=True)
                    return

                settings_model = db.query(GlobalSettings).filter_by(key=key).first()
                old_value = settings_model.value if settings_model else None

                if not settings_model:
                    settings_model = GlobalSettings(key=key, value=new_value)
                    db.add(settings_model)
                else:
                    settings_model.value = new_value

                history = ConfigHistory(
                    guild_id='0',  # グローバル設定は guild_id 0
                    key=key,
                    old_value=json.dumps(old_value),
                    new_value=json.dumps(new_value),
                    changed_by=str(interaction.user.id)
                )
                db.add(history)
                
                db.commit()
                
                # キャッシュを更新
                updated_settings = await self.get_global_settings(db)
                updated_settings[key] = new_value
                self.global_settings_cache = updated_settings
                
                await interaction.followup.send(f"グローバル設定 '{key}' を `{new_value}` に更新しました。", ephemeral=True)

            # 引数が不完全な場合
            else:
                await interaction.followup.send("設定を変更するには、`key` と `value` の両方を指定してください。", ephemeral=True)

        except Exception as e:
            db.rollback()
            await interaction.followup.send(f"エラーが発生しました: {e}", ephemeral=True)
        finally:
            db.close()


async def setup(bot: commands.Bot):
    await bot.add_cog(GlobalConfigCog(bot))