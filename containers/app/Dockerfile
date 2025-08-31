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

# 非rootユーザーを作成
RUN useradd --create-home appuser
WORKDIR /home/appuser/app
# Builder Stageから仮想環境をコピー
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# アプリケーションコードをコピー
# .dockerignoreを使用して不要なファイルを除外することを推奨
COPY src/ .

# logディレクトリを作成し、作業ディレクトリ全体の所有権をappuserに変更
RUN mkdir -p log && chown -R appuser:appuser /home/appuser/app

# ユーザーを切り替え
USER appuser

# BOTを実行
CMD ["python", "bot.py"]