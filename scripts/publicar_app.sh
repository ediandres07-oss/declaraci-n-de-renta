#!/bin/zsh
# Publica una app en tributando.co/apps/<slug>/ (13-oct-2026).
# Uso: scripts/publicar_app.sh <slug> <archivo.html | carpeta> "Nombre" "Descripción corta"
# - Copia el HTML (o la carpeta entera) a static/apps/<slug>/
# - Agrega o actualiza la app en static/apps/apps.json
# - Commit SOLO de static/apps (no toca otros cambios sin commitear) y push → Render despliega.
# REGLA: nada con datos de clientes; todo lo de /apps es público con el enlace.
set -euo pipefail
cd "$(dirname "$0")/.."
slug="$1"; origen="$2"; nombre="${3:-$1}"; desc="${4:-}"
[[ "$slug" =~ ^[a-z0-9-]+$ ]] || { echo "slug inválido (solo a-z, 0-9 y -)"; exit 1; }
dest="static/apps/$slug"
mkdir -p "$dest"
if [[ -d "$origen" ]]; then
  rsync -a --delete "$origen/" "$dest/"
else
  cp "$origen" "$dest/index.html"
fi
python3 - "$slug" "$nombre" "$desc" <<'EOF'
import json, sys, datetime, pathlib
slug, nombre, desc = sys.argv[1:4]
p = pathlib.Path("static/apps/apps.json")
cat = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
cat = [a for a in cat if a.get("slug") != slug]
cat.append({"slug": slug, "nombre": nombre, "descripcion": desc,
            "fecha": datetime.date.today().isoformat()})
p.write_text(json.dumps(cat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
EOF
git add static/apps
git commit -q -m "Apps: publica $slug en tributando.co/apps/$slug/

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- static/apps
git push -q
echo "https://tributando.co/apps/$slug/"
