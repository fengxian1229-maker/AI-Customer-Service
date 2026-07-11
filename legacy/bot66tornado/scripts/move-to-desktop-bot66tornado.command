#!/bin/zsh
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-$HOME/Desktop/bot66tornado}"

echo "來源：$SRC"
echo "目標：$DEST"

cd "$SRC"

if npm run status:official | grep -q "running"; then
  echo "official bot 還在跑，請先停止後再搬。"
  exit 1
fi

if [ -e "$DEST" ]; then
  BACKUP="${DEST}-backup-$(date +%Y%m%d-%H%M%S)"
  echo "目標資料夾已存在，先改名備份：$BACKUP"
  mv "$DEST" "$BACKUP"
fi

mkdir -p "$DEST"

rsync -a \
  --exclude ".DS_Store" \
  --exclude ".git/" \
  --exclude "node_modules/" \
  --exclude "reports/" \
  --exclude "scripts/__pycache__/" \
  --exclude "bot66tornado@0.1.0" \
  --exclude "node" \
  --exclude "runtime/test-state.json" \
  --exclude "runtime/test-live-state.json" \
  --exclude "runtime/*.stop" \
  --exclude "runtime/*.log" \
  "$SRC/" "$DEST/"

mkdir -p "$DEST/reports" "$DEST/runtime"

echo ""
echo "搬移完成。新資料夾："
echo "$DEST"
echo ""
echo "請接著跑："
echo "cd \"$DEST\""
echo "npm test"
echo "npm run preflight:official"
echo ""
echo "確認都通過後，正式啟動再跑："
echo "npm run start:official"
echo "npm run status:official"
