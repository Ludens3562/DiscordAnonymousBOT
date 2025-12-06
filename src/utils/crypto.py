import os
import base64
from datetime import date
from dotenv import load_dotenv
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.backends import default_backend

load_dotenv()


class Encryptor:
    def __init__(self):
        self.keys = self._load_keys()
        self.current_key_version = int(os.getenv("CURRENT_KEY_VERSION", "1"))
        self.pepper = os.getenv("ENCRYPTION_PEPPER")
        if not self.pepper:
            raise ValueError("ENCRYPTION_PEPPER not found in .env file.")
        self.backend = default_backend()
        self.iv_length = 12
        self.tag_length = 16

    def get_logging_salt(self, salt_type: str) -> bytes | None:
        """ログのソルトタイプ名から対応するソルトを返す"""
        salt_map = {
            'ban': b'logging_salt_for_ban',
            'unban': b'logging_salt_for_unban',
            'user_posts': b'logging_salt_for_user_posts',
            'bulk_delete': b'logging_salt_for_bulk_delete',
            'admin_logs': b'logging_salt_for_admin_logs',
        }
        return salt_map.get(salt_type)

    def _load_keys(self):
        keys = {}
        i = 1
        while True:
            key = os.getenv(f"ENCRYPTION_KEY_{i}")
            if key:
                keys[i] = base64.b64decode(key)
                i += 1
            else:
                break
        if not keys:
            raise ValueError("No ENCRYPTION_KEY_X found in .env file.")
        return keys

    def _derive_key(self, salt: bytes, info: bytes, length: int = 32) -> bytes:
        """鍵導出関数"""
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=length,
            salt=salt,
            info=info,
            backend=self.backend
        )
        return hkdf.derive(b'')

    def get_server_key(self, guild_salt: str) -> bytes:
        """サーバーソルトからサーバー固有の鍵を導出する"""
        return self._derive_key(base64.b64decode(guild_salt), b"server-key")

    def encrypt_user_id(self, user_id: str, key_version: int, salt: bytes) -> str:
        """マスターキーと投稿ごとのソルトでユーザーIDを暗号化する"""
        master_key = self.keys.get(key_version)
        if not master_key:
            raise ValueError(f"Key version {key_version} not found.")

        encryption_key = self._derive_key(salt, b"user-id-encryption", 32)
        
        iv = os.urandom(self.iv_length)
        cipher = Cipher(algorithms.AES(encryption_key), modes.GCM(iv), backend=self.backend)
        encryptor = cipher.encryptor()
        
        encrypted_data = encryptor.update(str(user_id).encode()) + encryptor.finalize()
        
        # salt, iv, tag, encrypted_data を結合して返す
        # ログ用の暗号化ではsaltをデータに含めない
        return base64.b64encode(iv + encryptor.tag + encrypted_data).decode()

    def decrypt_user_id(self, encrypted_b64_data: str) -> str | None:
        """暗号化されたユーザーIDを復号する"""
        try:
            encrypted_data_with_meta = base64.b64decode(encrypted_b64_data)
            
            salt = encrypted_data_with_meta[:16]
            iv = encrypted_data_with_meta[16:16 + self.iv_length]
            tag = encrypted_data_with_meta[16 + self.iv_length:16 + self.iv_length + self.tag_length]
            encrypted_data = encrypted_data_with_meta[16 + self.iv_length + self.tag_length:]

            # 全てのキーバージョンを試す
            for version, master_key in self.keys.items():
                try:
                    encryption_key = self._derive_key(salt, b"user-id-encryption", 32)
                    cipher = Cipher(algorithms.AES(encryption_key), modes.GCM(iv, tag), backend=self.backend)
                    decryptor = cipher.decryptor()
                    decrypted_data = decryptor.update(encrypted_data) + decryptor.finalize()
                    return decrypted_data.decode()
                except Exception:
                    continue  # 次のキーを試す
            return None  # どのキーでも復号できなかった
        except Exception:
            return None

    def decrypt_log_user_id(self, encrypted_b64_data: str, salt: bytes) -> str | None:
        """ログ用に暗号化されたユーザーIDを、与えられたソルトで復号する"""
        try:
            encrypted_data_with_meta = base64.b64decode(encrypted_b64_data)
            
            iv = encrypted_data_with_meta[:self.iv_length]
            tag = encrypted_data_with_meta[self.iv_length:self.iv_length + self.tag_length]
            encrypted_data = encrypted_data_with_meta[self.iv_length + self.tag_length:]

            # 全てのキーバージョンを試す
            for version, master_key in self.keys.items():
                try:
                    encryption_key = self._derive_key(salt, b"user-id-encryption", 32)
                    cipher = Cipher(algorithms.AES(encryption_key), modes.GCM(iv, tag), backend=self.backend)
                    decryptor = cipher.decryptor()
                    decrypted_data = decryptor.update(encrypted_data) + decryptor.finalize()
                    return decrypted_data.decode()
                except Exception:
                    continue
            return None
        except Exception:
            return None

    def decrypt_user_id_with_salt(self, encrypted_b64_data: str, salt: bytes) -> str | None:
        """暗号化されたユーザーIDを、与えられたソルトで復号する"""
        try:
            encrypted_data_with_meta = base64.b64decode(encrypted_b64_data)
            
            iv = encrypted_data_with_meta[:self.iv_length]
            tag = encrypted_data_with_meta[self.iv_length:self.iv_length + self.tag_length]
            encrypted_data = encrypted_data_with_meta[self.iv_length + self.tag_length:]

            # 全てのキーバージョンを試す
            for version, master_key in self.keys.items():
                try:
                    encryption_key = self._derive_key(salt, b"user-id-encryption", 32)
                    cipher = Cipher(algorithms.AES(encryption_key), modes.GCM(iv, tag), backend=self.backend)
                    decryptor = cipher.decryptor()
                    decrypted_data = decryptor.update(encrypted_data) + decryptor.finalize()
                    return decrypted_data.decode()
                except Exception:
                    continue
            return None
        except Exception:
            return None

    def generate_search_tag(self, user_id: str, guild_salt: str, nonce: bytes) -> str:
        """サーバー秘密鍵とノンスでsearch_tagを生成する"""
        server_key = self.get_server_key(guild_salt)
        h = hmac.HMAC(server_key, hashes.SHA256(), backend=self.backend)
        h.update(str(user_id).encode())
        h.update(nonce)
        return base64.b64encode(h.finalize()).decode()

    def generate_global_user_signature(self, user_id: str, key_version: int, nonce: bytes) -> str:
        """マスターキー、ペッパー、ノンスでglobal_user_signatureを生成する"""
        master_key = self.keys.get(key_version)
        if not master_key:
            raise ValueError(f"Key version {key_version} not found.")
        
        hmac_key = self._derive_key(master_key, self.pepper.encode() + b"global-signature")
        h = hmac.HMAC(hmac_key, hashes.SHA256(), backend=self.backend)
        h.update(str(user_id).encode())
        h.update(nonce)
        return base64.b64encode(h.finalize()).decode()

    def generate_rate_limit_key(self, user_id: str, guild_salt: str, current_date: date) -> str:
        """サーバー秘密鍵と日付でrate_limit_keyを生成する"""
        server_key = self.get_server_key(guild_salt)
        date_str = current_date.strftime('%Y-%m-%d')
        h = hmac.HMAC(server_key, hashes.SHA256(), backend=self.backend)
        h.update(str(user_id).encode())
        h.update(date_str.encode())
        return base64.b64encode(h.finalize()).decode()

    def generate_global_rate_limit_key(self, user_id: str, global_chat_salt: str, current_date: date) -> str:
        """マスターキー、グローバルSalt、日付でグローバルなrate_limit_keyを生成する"""
        master_key = self.keys.get(self.current_key_version)
        if not master_key:
            raise ValueError(f"Key version {self.current_key_version} not found.")
        
        date_str = current_date.strftime('%Y-%m-%d')
        salt_bytes = base64.b64decode(global_chat_salt)
        
        hmac_key = self._derive_key(master_key, salt_bytes + b"global-rate-limit")
        h = hmac.HMAC(hmac_key, hashes.SHA256(), backend=self.backend)
        h.update(str(user_id).encode())
        h.update(date_str.encode())
        return base64.b64encode(h.finalize()).decode()