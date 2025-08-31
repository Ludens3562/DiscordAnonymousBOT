#!/bin/sh

# 'log' ディレクトリの所有権を 'appuser' に変更
chown -R appuser:appuser /home/appuser/app/log

# appuser として、渡されたコマンドを実行
exec gosu appuser "$@"