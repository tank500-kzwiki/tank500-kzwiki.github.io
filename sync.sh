#!/usr/bin/env bash
#
# Публикация Tank 500 Wiki: синхронизирует контент из Obsidian-вола в этот
# Quartz-репозиторий и пушит в GitHub. GitHub Action сам пересоберёт сайт.
#
# Использование:
#   ./sync.sh "сообщение коммита"
#
set -euo pipefail

# nvm-node в PATH (если запускается из не-login shell)
if [ -d "$HOME/.nvm/versions/node" ]; then
  NODE_BIN="$(ls -d "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | sort -V | tail -1)"
  [ -n "$NODE_BIN" ] && export PATH="$NODE_BIN:$PATH"
fi

VAULT="/Users/Artyom_Vetlugin/Library/Mobile Documents/iCloud~md~obsidian/Documents/avwiki/tank500-wiki"
REPO="/Users/Artyom_Vetlugin/AVPetProjects/tank500-kzwiki-publish"
MSG="${1:-update wiki}"

cd "$REPO"

if [ ! -d "$VAULT/wiki" ]; then
  echo "ОШИБКА: не найден $VAULT/wiki" >&2
  exit 1
fi

echo "==> Синхронизация wiki/ -> content/"
rsync -a --delete --exclude '.obsidian/' "$VAULT/wiki/" "$REPO/content/"

echo "==> LLM-кит: CLAUDE.md + .claude/skills/ -> llm-kit/"
mkdir -p "$REPO/llm-kit/skills"
cp "$VAULT/CLAUDE.md" "$REPO/llm-kit/CLAUDE.md"
rsync -a --delete "$VAULT/.claude/skills/" "$REPO/llm-kit/skills/"

echo "==> Инструменты: tools/ -> tools/"
rsync -a --delete "$VAULT/tools/" "$REPO/tools/"

echo "==> git commit + push"
git add -A
if git diff --cached --quiet; then
  echo "Нет изменений — коммитить нечего."
else
  git commit -m "$MSG"
  git push
  echo "==> Запушено. Сборку и публикацию выполнит GitHub Action."
fi
