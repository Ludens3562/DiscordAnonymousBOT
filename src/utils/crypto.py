import os
import base64
from datetime import date
from dotenv import load_dotenv
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.backends import default_backend

load_dotenv()


class Encryptor:
    def __init__(self):
        self.master_key = os.getenv("ENCRYPTION_KEY")
        if not self.master_key:
            raise ValueError("ENCRYPTION_KEY not found in .env file.")
        self.backend = default_backend()
        self.iv_length = 12
        self.kdf_iterations = 100000

    def _derive_key(self, salt: bytes, length: int = 32) -> bytes:
        """マスターキーとソルトから鍵を導出する"""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=length,
            salt=salt,
            iterations=self.kdf_iterations,
            backend=self.backend
        )
        return kdf.derive(self.master_key.encode())

    def get_server_key(self, guild_salt: str) -> bytes:
        """サーバーソルトからサーバー固有の暗号鍵を導出する"""
        return self._derive_key(guild_salt.encode())

    def get_daily_user_hmac_key(self, user_id: str, guild_salt: str, current_date: date) -> bytes:
        """ユーザーID、サーバーソルト、日付から日次HMAC署名鍵を導出する"""
        date_str = current_date.strftime('%Y-%m-%d')
        user_salt = f"daily-{user_id}-{guild_salt}-{date_str}".encode()
        return self._derive_key(user_salt)

    def get_persistent_user_hmac_key(self, user_id: str, guild_salt: str) -> bytes:
        """ユーザーIDとサーバーソルトから永続的なHMAC署名鍵を導出する"""
        user_salt = f"persistent-{user_id}-{guild_salt}".encode()
        return self._derive_key(user_salt)

    def encrypt(self, data: str, guild_salt: str) -> str:
        """サーバー鍵で文字列を暗号化する"""
        if not isinstance(data, str):
            raise TypeError("Data must be a string.")
        
        server_key = self.get_server_key(guild_salt)
        iv = os.urandom(self.iv_length)
        cipher = Cipher(algorithms.AES(server_key), modes.GCM(iv), backend=self.backend)
        encryptor = cipher.encryptor()
        
        encrypted_data = encryptor.update(data.encode()) + encryptor.finalize()
        return base64.b64encode(iv + encryptor.tag + encrypted_data).decode()

    def decrypt(self, encrypted_b64_data: str, guild_salt: str) -> str | None:
        """サーバー鍵で暗号化された文字列を復号する"""
        if not isinstance(encrypted_b64_data, str):
            raise TypeError("Encrypted data must be a string.")
        
        try:
            encrypted_data_with_iv_tag = base64.b64decode(encrypted_b64_data.encode())
            iv = encrypted_data_with_iv_tag[:self.iv_length]
            tag = encrypted_data_with_iv_tag[self.iv_length:self.iv_length + 16]
            encrypted_data = encrypted_data_with_iv_tag[self.iv_length + 16:]

            server_key = self.get_server_key(guild_salt)
            cipher = Cipher(algorithms.AES(server_key), modes.GCM(iv, tag), backend=self.backend)
            decryptor = cipher.decryptor()
            
            decrypted_data = decryptor.update(encrypted_data) + decryptor.finalize()
            return decrypted_data.decode()
        except Exception:
            return None

    def sign_daily_user_id(self, user_id: str, guild_salt: str, current_date: date) -> str:
        """日次鍵でユーザーIDに署名し、daily_user_id_signatureを生成する"""
        hmac_key = self.get_daily_user_hmac_key(user_id, guild_salt, current_date)
        h = hmac.HMAC(hmac_key, hashes.SHA256(), backend=self.backend)
        h.update(user_id.encode())
        return base64.b64encode(h.finalize()).decode()

    def sign_search_tag(self, daily_signature: str, user_id: str, guild_salt: str) -> str:
        """永続鍵でdaily_user_id_signatureに署名し、search_tagを生成する"""
        hmac_key = self.get_persistent_user_hmac_key(user_id, guild_salt)
        h = hmac.HMAC(hmac_key, hashes.SHA256(), backend=self.backend)
        h.update(daily_signature.encode())
        return base64.b64encode(h.finalize()).decode()

    def sign_persistent_user_id(self, user_id: str, guild_salt: str) -> str:
        """永続鍵でユーザーIDに署名し、user_id_signatureを生成する"""
        hmac_key = self.get_persistent_user_hmac_key(user_id, guild_salt)
        h = hmac.HMAC(hmac_key, hashes.SHA256(), backend=self.backend)
        h.update(user_id.encode())
        return base64.b64encode(h.finalize()).decode()