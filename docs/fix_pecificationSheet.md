# Discord匿名チャットBOT 開発仕様書

## 目次

1. 概要
2. 対象範囲
3. 技術スタック
4. システム構成（アーキテクチャ）
5. データベース設計
6. BOT（Discord）機能仕様

- 6.1 匿名投稿機能
- 6.2 匿名スレッド作成機能
- 6.3 誤投稿変換機能
- 6.4 投稿制限（レート・文字数）
- 6.5 管理機能（管理者コマンド）
- 6.6 一括削除フロー
- 6.7 スラッシュコマンド 実行例（抜粋）

7. セキュリティ要件
8. ログ・監査・保管ルール
9. 運用・保守（省略）
10. テスト計画
11. デプロイ / インフラ
12. 付録（設定デフォルト等）

---

## 1. 概要

本仕様書は、要件定義書を基に、実装チームが開発を進めるための詳細な技術仕様をまとめたものです。目的は匿名での安心発言を保証しつつ、必要時に管理者が発言者を特定できる仕組みと、運用に適したモデレーション機能を提供することです。

## 2. 対象範囲

- Discord 上で動作する BOT（discord.py を使用）
- PostgreSQL を用いた永続層（マルチサーバ導入を前提）
- 管理者向けのスラッシュコマンドによる運用（Web 管理画面は不要）

## 4. 技術スタック

- 言語: Python 3.11+（discord.py）
- DB: PostgreSQL
- コンテナ: docker-compose で BOT と DB を管理
## 5. システム構成（アーキテクチャ）

- Discord クライアント ⇄ Discord API ⇄ BOT（複数ワーカー可）
- BOT ⇄ PostgreSQL
- バックアップ／バッチジョブ

---

## 6. データベース設計

マルチサーバ導入を前提に `guild_id` を主軸にしたスコープ分離を行います。管理ログ系は用途別にテーブルを分離します。

### 6.1 テーブル一覧（主要）

- anonymous\_posts
- conversion\_history
- admin\_command\_logs         -- 管理者が実行した管理系コマンドのログ
- user\_command\_logs          -- 一般ユーザーが自身の照会等を行った場合のログ（管理者ログとは別）
- bulk\_delete\_history
- batch\_delete\_jobs
- guild\_settings (per-guild)
- config\_history
- guild\_banned\_users         -- 各ギルドの BAN リスト
- bot\_banned\_users           -- BOT レベルのグローバル BAN（管理者専用）
- ng\_words
- anon\_id\_mappings
- bot\_logs                   -- BOTの動作ログ
- anonymous\_threads

### 6.2 anonymous\_posts（DDL 例）

```sql
CREATE TABLE anonymous_posts (
  id BIGSERIAL PRIMARY KEY,
  guild_id VARCHAR(30) NOT NULL,
  user_id_encrypted VARCHAR(512) NOT NULL,
  anonymous_id VARCHAR(64) NOT NULL,
  message_id VARCHAR(64) NOT NULL,
  channel_id VARCHAR(30) NOT NULL,
  thread_id VARCHAR(30),
  content TEXT NOT NULL,
  attachment_urls JSONB DEFAULT '[]'::jsonb, -- Discord が提供するファイル URL の配列を格納する
  is_converted BOOLEAN DEFAULT FALSE,
  original_message_id VARCHAR(64),
  created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
  deleted_at TIMESTAMP WITH TIME ZONE,
  deleted_by VARCHAR(30)
);
CREATE INDEX idx_anonymous_posts_guild_channel ON anonymous_posts (guild_id, channel_id);
CREATE INDEX idx_anonymous_posts_anon_id ON anonymous_posts (anonymous_id);
```

### 6.3 conversion\_history（DDL 例）

```sql
CREATE TABLE conversion_history (
  id BIGSERIAL PRIMARY KEY,
  guild_id VARCHAR(30) NOT NULL,
  user_id_encrypted VARCHAR(512) NOT NULL,
  original_message_id VARCHAR(64) NOT NULL,
  converted_message_id VARCHAR(64),
  channel_id VARCHAR(30) NOT NULL,
  thread_id VARCHAR(30),
  status VARCHAR(20) NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);
CREATE INDEX idx_conversion_history_guild ON conversion_history (guild_id);
```

### 6.4 admin\_command\_logs（DDL 例）

管理者（Admin/Moderator）が実行した管理コマンドは必ずこちらに記録します。**記録されるログは該当ギルド内の操作のみ**です。

```sql
CREATE TABLE admin_command_logs (
  id BIGSERIAL PRIMARY KEY,
  guild_id VARCHAR(30) NOT NULL,
  command_name VARCHAR(100) NOT NULL,
  executed_by VARCHAR(30) NOT NULL,
  target_user_id VARCHAR(30),
  channel_id VARCHAR(30),
  params JSONB,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);
CREATE INDEX idx_admin_logs_guild_time ON admin_command_logs (guild_id, created_at DESC);
CREATE INDEX idx_admin_logs_executed_by ON admin_command_logs (executed_by);
```

### 6.5 user\_command\_logs（DDL 例）

一般ユーザーが自身の照会などを行った場合は、プライバシー保護のため管理者ログとは別テーブルに記録します（例: 非公開の自己照会履歴など）。

```sql
CREATE TABLE user_command_logs (
  id BIGSERIAL PRIMARY KEY,
  guild_id VARCHAR(30) NOT NULL,
  command_name VARCHAR(100) NOT NULL,
  executed_by VARCHAR(30) NOT NULL,
  params JSONB,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);
CREATE INDEX idx_user_logs_guild_time ON user_command_logs (guild_id, created_at DESC);
```

### 6.6 bulk\_delete\_history（DDL 例）

```sql
CREATE TABLE bulk_delete_history (
  id BIGSERIAL PRIMARY KEY,
  guild_id VARCHAR(30),
  executed_by VARCHAR(30) NOT NULL,
  target_user_id VARCHAR(30),
  target_type VARCHAR(20) NOT NULL,
  scope VARCHAR(50) NOT NULL,
  conditions JSONB NOT NULL,
  deleted_count INTEGER NOT NULL DEFAULT 0,
  dry_run BOOLEAN DEFAULT FALSE,
  execution_time TIMESTAMP WITH TIME ZONE DEFAULT now()
);
CREATE INDEX idx_bulk_delete_guild_time ON bulk_delete_history (guild_id, execution_time DESC);
```

### 6.7 anon\_id\_mappings（DDL 例）

匿名ID は nanoid ベースで生成し、guild+channel\_or\_thread 単位でマッピングを保持します。

```sql
CREATE TABLE anon_id_mappings (
  id BIGSERIAL PRIMARY KEY,
  guild_id VARCHAR(30) NOT NULL,
  channel_or_thread_id VARCHAR(30) NOT NULL,
  user_id_encrypted VARCHAR(512) NOT NULL,
  anon_id VARCHAR(64) NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);
CREATE UNIQUE INDEX uq_anon_mapping_scope_user ON anon_id_mappings (guild_id, channel_or_thread_id, user_id_encrypted);
```

### 6.8 anonymous\_threads（DDL 例）

```sql
CREATE TABLE anonymous_threads (
  id BIGSERIAL PRIMARY KEY,
  guild_id VARCHAR(30) NOT NULL,
  thread_discord_id VARCHAR(64) NOT NULL,
  board VARCHAR(100) NOT NULL,
  title VARCHAR(200) NOT NULL,
  created_by_encrypted VARCHAR(512) NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);
CREATE INDEX idx_threads_guild_board ON anonymous_threads (guild_id, board);
```

### 6.9 guild\_banned\_users / bot\_banned\_users（DDL 例）

ギルド毎の BAN リストと BOT レベル（グローバル）BAN を分離して管理します。チェックは両方を参照し、いずれかに該当すれば投稿を拒否します。

```sql
CREATE TABLE guild_banned_users (
  guild_id VARCHAR(30) NOT NULL,
  user_id_encrypted VARCHAR(512) NOT NULL,
  banned_by VARCHAR(30),
  banned_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
  PRIMARY KEY (guild_id, user_id_encrypted)
);

CREATE TABLE bot_banned_users (
  user_id_encrypted VARCHAR(512) PRIMARY KEY,
  banned_by VARCHAR(30),
  banned_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);
```

---

## 7. BOT（Discord）機能仕様

各コマンドの詳細、入力・出力、権限チェック、エラーハンドリング、実行フローを定義します。**管理コマンドは実行ギルドの範囲内でのみデータを参照／操作します。**

### 7.1 匿名投稿機能

**コマンド**:

- `/post [message] [attachments...]` — 同一チャンネル/スレッドに匿名で代理投稿。`attachments` は画像等のファイルを複数指定可能（任意）。
- `/reply [message_id] [message] [attachments...]` — 指定投稿に匿名で返信（添付ファイル可）
- `/delete [message_id]` — 対象の匿名投稿に対して削除を実行（投稿者自身または管理者）

**挙動（要点）**:

- anon_id は nanoid で生成し、anon_id_mappings に保存して再利用する。
- `/post` および `/reply` はテキスト本文に加えて複数の添付ファイル（画像等）を受け付ける。添付されたファイルは Discord に投稿されたまま扱い、**システム側で別クラウドへ再アップロードすることは行わない**。
- DB には添付ファイルのバイナリは格納せず、**添付ファイルの URL（Discord が提供するファイル URL）** を `attachment_urls` フィールド（JSONB）に格納する。テキスト本文は `content` に保存する。
- 投稿時のログチャンネル出力（guild 設定がある場合）は **匿名ID ベース** で記載し、添付ファイルがある場合はその URL を一覧で出力する（実際のユーザーID や復号済み情報は含めない）。
- `/delete` 実行時は admin_command_logs へ管理者の実行ログを残し、一般ユーザーが自己削除した場合は `user_command_logs` に記録する（管理ログと分離）。ログ出力チャンネルでも削除ログは匿名ID ベースで通知され、削除対象に添付ファイルがあればその URL を併記する。

**権限・削除の挙動**:

- 投稿の削除は投稿者本人が可能（自身の Discord ID と anon_posts の user_id_encrypted を照合して許可）。
- 管理者は任意の投稿を削除可能で、削除時は `anonymous_posts` の `deleted_at` と `deleted_by` を設定する（論理削除）。

**エラーハンドリング**:

- メッセージが `max_message_length` を超える場合はエラー応答。
- 添付ファイル数や総容量が guild_settings のポリシーを超える場合は拒否し、エラーメッセージで拒否理由を返す。
- BAN リスト（guild_banned_users / bot_banned_users の両方）に登録されているユーザーは投稿を拒否。
- NGワードフィルターに一致した場合、設定に従い `warn` / `block` / `delete` を適用。block は投稿拒否。

### 7.2 匿名スレッド作成機能

**コマンド**:

- `/th board:<board> title:<title> content:<content>`

**挙動（要点）**:

- スレッド作成者は `anonymous_threads.created_by_encrypted` に暗号化保存され管理者が必要時復号できる
- ログチャンネル出力は作成イベントを匿名ID で通知

### 7.3 誤投稿変換機能（直接発言の検知と変換）

**検知方法**:
- 指定された匿名チャットチャンネル群で通常メッセージ（ユーザーが直接投稿したメッセージ）を監視します。
- チャンネルリストは `guild_settings` の `conversion_channels` で管理します。

**ユーザー確認フロー**:
1. BOT が直接投稿を検知すると、当該メッセージに対して確認メッセージ（Embed + ボタン）を投稿します。ボタンは `変換する`（`🔄` 相当）と `キャンセル` の2つを提供します。
2. ユーザーが指定時間（`conversion_timeout` 秒、guild 設定）以内に `変換する` をクリックした場合のみ変換処理を実行します。
3. クリックがなければタイムアウト扱いとなり、確認メッセージを削除して元の投稿はそのまま残します。

**変換処理**:
- 変換実行時は可能なら Discord 上の元メッセージを削除し（Bot に削除権限がある場合）、`anonymous_posts` に `is_converted = true` のレコードとして新規登録します。
- 変換イベントの詳細（status: converted / timeout / cancelled）は `conversion_history` に記録されます。
- 変換で作成された匿名投稿は通常の `/post` と同じ匿名ID付与ルール（anon_id_mappings を参照）に従います。

**権限と監査**:
- 変換は原則としてメッセージ投稿者本人によるボタン操作でのみ行います。管理者による強制変換オプションを提供する場合は、別途 `/config` による切替と厳密な権限チェックを実装してください。

- 変換の通知や確認は Discord のメッセージコンポーネント（ボタン）を利用するのが UX も実装も安定します。

### 7.4 投稿制限（レート・文字数）

**レート制限**:
- デフォルト: `rate_limit_count = 3`、`rate_limit_window = 60`（秒）。
- 実装方式: Redis などのインメモリ TTL ストアを推奨。Redis 未導入時は DB ベースで高速にカウントできる仕組みを実装（トランザクションとインデックス最適化が必要）。
- guild ごとに設定可能で、`/config rate_limit [count] [window_seconds]` で変更できます。
- レート違反時の挙動は `guild_settings` の `rate_limit_action` に従い（例: warn/block）制御します。

**文字数制限**:
- `max_message_length` を guild 設定で管理。送信メッセージが超過する場合は拒否し、エラーを返します。

**実装上の注意**:
- レートや文字数の判定は BOT 側で行い、スラッシュコマンドのパラメータ受け取り時点で早期検出することで不要な操作を防ぎます。
- これらの設定は `guild_settings` の `settings` JSONB フィールドに保存し、`/config show_all` で出力可能にします。

### 7.5 管理機能（管理者コマンド）（管理者コマンド）

**主要コマンド（抜粋）**

- `/trace [message_id]` — 管理者のみ（guild スコープ）。実行ログは admin_command_logs に記録。
- `/user_posts [user_id]` — 管理者のみ（guild スコープ）。実行ログは admin\_command\_logs に記録
- `/delete [message_id]` — 投稿者自身は user\_command\_logs に記録、管理者は admin\_command\_logs に記録
- `/config` — guild ごとの設定管理
  - 引数なし: 現在のギルド設定（すべて）を表示します。
  - `key` と `value` を指定: 設定の更新を行います。すべての設定変更は `config_history` に記録されます。
- `/bulk_delete ...` — 一括削除（条件は下記）
- `/admin_logs search [filters]` — guild 内の admin\_command\_logs を検索（管理者限定）
- `/view_bot_logs [level] [days] [limit]` -- DBに記録されたBOTのログを閲覧（BOTオーナー限定）

**管理ログのスコープ制限**:

- すべての管理コマンドは呼び出しギルドのデータのみを参照・操作します。他ギルドのデータへのアクセスは不可とします。

### 7.6 一括削除フロー（最終仕様）

**重要ルール**

- 条件は「どれか 1 つのみ」必須で指定する（複数条件の AND 指定は不可）。
- `reason` は不要。`limit` はコマンド内で指定しない（別途管理 UI / CLI などで制御）。

**コマンド構文（例）** `/bulk_delete scope:<current_channel|all_channels|channels> condition_type:<messages|hours|since|between|contains|pattern|anonymous_id|converted_only|direct_only|reactions_less> condition_value:<value> dry_run:true`

**範囲（scope）**:

- `current_channel`
- `all_channels`
- `channels [id1,id2,...]`

**条件（いずれか 1 つ必須）**:

- `messages [数]` : 最新 N 件
- `hours [数]` : 過去 N 時間以内
- `user [ユーザーIDもしくはメンション]` : ユーザー指定
- `contains [テキスト]` : 指定テキストを含む投稿のみ
- `pattern [正規表現]` : パターンマッチする投稿のみ
- `anonymous_id [ID]` : 特定の匿名ID の投稿のみ
- `converted_only` : 変換された投稿のみ
- `direct_only` : 直接投稿のみ

**オプション**:

- `dry_run` : true/false（デフォルト true）

**処理**:

1. 指定ギルド・範囲内で条件を満たすメッセージを検索
2. dry\_run\:true -> 件数サマリと最大 N 件のプレビューを返す
3. dry\_run\:false -> 対象行に対して論理削除（`deleted_at`, `deleted_by`）を実行し、`bulk_delete_history` に記録


## 8. セキュリティ要件

- DB 内の個人情報（Discord User ID 等）は暗号化保存（アプリケーションレベルで暗号化キーは KMS 等に保存）
- 管理コマンドの実行はロールベースで厳格に制御
- 通信は常に TLS（WSS / HTTPS）
- ログ情報は最小限に留め、不必要な個人情報を露出しない

---

## 8. ログ・監査・保管ルール

### 8.1. 基本方針

1.  **最小情報の原則**: Discord 上のログ出力には復号済みの実ユーザー識別子を含めない。出力されるのは匿名ID、操作の要約、関連メッセージの `message_id`、タイムスタンプ、および添付がある場合は `attachment_urls` である。
2.  **参照のみの添付管理**: 画像等の添付ファイルは Discord がホストする URL を記録するのみで、別ストレージへの再アップロードは行わない。これによりストレージコストを抑制し、二次的なデータ移転によるリスクを排除する。
3.  **ギルド境界の遵守**: 管理者用ログ検索・操作は呼び出しギルドのデータのみを対象とする。横断的検索は不可であり、マルチギルドでのデータ隔離を厳守する。
4.  **監査ログの分離**: 管理者操作ログ（`admin_command_logs`）と一般ユーザーの自己操作ログ（`user_command_logs`）は物理的に分離して記録する。これによりアクセス制御と公開ポリシーが明確になる。

### 8.2. ログの種類と保持期間

#### 8.2.1. ファイルログ

-   **目的**: 主にリアルタイムのデバッグと短期的な問題追跡に使用。
-   **通常ログ (NLOG)**: `log/NLOG.log` にINFOレベル以上を記録。
-   **エラーログ (ELOG)**: `log/ELOG.log` にERRORレベル以上を記録。
-   **ローテーション**:
    -   毎日深夜0時にローテーションを実施。
    -   古いログは `log/archive/YYYYMMDD_NLOG.log` の形式でアーカイブ。
    -   **保持期間**: 7日間。

#### 8.2.2. データベースログ (`bot_logs` テーブル)

-   **目的**: 長期的な監査、傾向分析、および管理者による事後調査に使用。
-   **記録内容**: 全てのログレベルのレコードがDBに保存される。
-   **ローテーション**:
    -   **保持期間**: 1年間。1年を超えたログは毎日自動的に削除される。
-   **アクセス**: BOTオーナーは `/view_logs` コマンドで、レベル、期間、件数を指定してログを検索・閲覧できる。

---

## 9. デプロイ / インフラ（docker-compose）
- 開発・本番環境ともに `docker-compose.yml` を用い、BOT コンテナ（service: bot）と PostgreSQL コンテナ（service: db）を一括管理する。シークレット（DB 接続情報・暗号鍵への参照）は外部シークレットマネージャ（例: Vault）で管理することを推奨する。
- データベースのマイグレーションはコンテナ起動後のジョブ（例: alembic）で実行する。バックアップは定期スナップショットを採る。

## 10. 付録（初期設定値・主要 DDL）
### 初期設定（例）
```json
{
  "rate_limit_count": 3,
  "rate_limit_window": 60,
  "max_message_length": 2000,
  "anon_id_duration": 86400,
  "anon_id_format": "匿名ユーザー_{id}",
  "conversion_timeout": 30,
  "conversion_enabled": true,
  "ngword_action": "block",
  "log_retention_days": 180,
  "anonymous_id_reset_mode": "daily",
  "bulk_delete_max_count": 1000,
  "bulk_delete_require_reason_threshold": 10,
  "bulk_delete_notify_admins": true,
  "log_channel_id": ""
}
```
（その他の詳細 DDL は本文を参照のこと）

---