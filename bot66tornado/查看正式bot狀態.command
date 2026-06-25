#!/bin/zsh
set -e

cd "$(dirname "$0")"

echo "查看 Project2 bot66tornado official 狀態..."
echo "工作目錄：$(pwd)"
echo

npm run status:official
npm run launchd:status:official || true
