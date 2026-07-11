#!/bin/zsh
set -e

cd "$(dirname "$0")"

echo "啟動 Project2 bot66tornado official..."
echo "工作目錄：$(pwd)"
echo

echo "先停止現有 official，避免重複 getUpdates 或 stale lock..."
npm run launchd:uninstall:official || true
npm run stop:official || true
sleep 3

npm run preflight:official
npm run launchd:install:official
sleep 5
npm run status:official

echo
echo "完成。如果狀態是 running 且 health=ok，就可以進 LiveChat 測試。"
