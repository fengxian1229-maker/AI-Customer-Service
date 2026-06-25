#!/bin/zsh
set -e

cd "$(dirname "$0")"

echo "停止 Project2 bot66tornado official..."
echo "工作目錄：$(pwd)"
echo

npm run launchd:uninstall:official || true
npm run stop:official || true
npm run status:official || true

echo
echo "完成。如果狀態是 stopped，就代表已停止。"
