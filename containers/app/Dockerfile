# ---- Builder Stage ----
# 依存関係をインストールするためのステージ
FROM python:3.11-slim as builder

WORKDIR /app

# 仮想環境を作成
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 依存関係ファイルをコピーしてインストール
COPY containers/app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- Final Stage ----
# 実際の実行環境となるステージ
FROM python:3.11-slim

# gosuをインストール
RUN apt-get update && apt-get install -y gosu && rm -rf /var/lib/apt/lists/*

# 非rootユーザーを作成
RUN useradd --create-home appuser
WORKDIR /home/appuser/app
# Builder Stageから仮想環境をコピー
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# アプリケーションコードをコピー
# .dockerignoreを使用して不要なファイルを除外することを推奨
COPY src/ .
COPY containers/app/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# logディレクトリを作成
RUN mkdir -p log

ENTRYPOINT ["entrypoint.sh"]

# BOTを実行
CMD ["python", "bot.py"]