#!/usr/bin/env bash
# Uçtan uca test: Claude Code'a gerçek kullanıcı soruları sorar, cevapları eval/e2e_out.md'ye yazar.
# Depo kopyasındaki sanal ortamı (.venv) kullanır; gerekli: `claude` CLI ve oluşturulmuş indeks.
cd "$(dirname "$0")/.." || exit 1
if [ -x .venv/Scripts/python.exe ]; then py="$(pwd -W 2>/dev/null || pwd)/.venv/Scripts/python.exe"; else py="$(pwd)/.venv/bin/python"; fi
cfg=$(mktemp)
cat > "$cfg" <<EOF
{"mcpServers": {"karabuk-universitesi": {"command": "${py//\\/\\\\}", "args": ["-m", "kbu_mcp.server"], "env": {"PYTHONIOENCODING": "utf-8", "KBU_AUTO_UPDATE": "0"}}}}
EOF
out=eval/e2e_out.md
: > "$out"
while IFS= read -r q; do
  [ -z "$q" ] && continue
  echo "## $q" >> "$out"
  timeout 400 claude -p "$q" --mcp-config "$cfg" --strict-mcp-config \
    --allowedTools "mcp__karabuk-universitesi__*" --output-format text < /dev/null >> "$out" 2>&1
  printf '\n\n---\n\n' >> "$out"
done < eval/e2e_questions.txt
rm -f "$cfg"
