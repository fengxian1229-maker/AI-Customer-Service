#!/bin/zsh
set -e
set -u

LICENSE_ID="19282375"

usage() {
  cat <<'TEXT'
Usage:
  ./open-livechat.command TEST
  ./open-livechat.command ZAP69
  ./open-livechat.command list

LiveChat direct URLs must use this format:
  https://direct.lc.chat/19282375/<group_id>

Do not use ?group=23 or /?group=23. Those can route to the wrong LiveChat group.
TEXT
}

print_row() {
  local platform="$1"
  local group="$2"
  printf "%-7s  group=%-2s  https://direct.lc.chat/%s/%s\n" "$platform" "$group" "$LICENSE_ID" "$group"
}

case "${1:-TEST}" in
  list|urls|all)
    print_row "JUE999" 2
    print_row "GNA777" 12
    print_row "JG7" 11
    print_row "PAG99" 13
    print_row "CUM777" 24
    print_row "CON777" 25
    print_row "ZAP69" 28
    print_row "TEST" 23
    exit 0
    ;;
  TEST|test|23)
    PLATFORM="TEST"; GROUP="23"
    ;;
  JUE999|jue999|2)
    PLATFORM="JUE999"; GROUP="2"
    ;;
  GNA777|gna777|12)
    PLATFORM="GNA777"; GROUP="12"
    ;;
  JG7|jg7|11)
    PLATFORM="JG7"; GROUP="11"
    ;;
  PAG99|pag99|13)
    PLATFORM="PAG99"; GROUP="13"
    ;;
  CUM777|cum777|24)
    PLATFORM="CUM777"; GROUP="24"
    ;;
  CON777|con777|25)
    PLATFORM="CON777"; GROUP="25"
    ;;
  ZAP69|zap69|28)
    PLATFORM="ZAP69"; GROUP="28"
    ;;
  -h|--help|help)
    usage
    exit 0
    ;;
  *)
    usage
    exit 2
    ;;
esac

URL="https://direct.lc.chat/${LICENSE_ID}/${GROUP}"
echo "$PLATFORM group=$GROUP"
echo "$URL"

if command -v open >/dev/null 2>&1; then
  open "$URL"
fi
