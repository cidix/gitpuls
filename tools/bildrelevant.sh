#!/bin/bash
# Wie viele Commits zwischen <von> und <bis> berühren das Bild?
# Aufruf: bildrelevant.sh <checkout> <von> <bis>   ->   "<gesamt> <bildrelevant>"
#         bildrelevant.sh <checkout> <von> <bis> --log   ->   je bildrelevanter Commit «kurzer Hash TAB Betreff», höchstens 20, neuester zuerst
#
# Derselbe Massstab wie der Tag-Wächter eines anderen eigenen Dienstes
# (BILD_PFADE), nur nicht von Hand gepflegt, sondern aus dem Dockerfile abgeleitet:
# das Dockerfile selbst plus die Quellen ALLER COPY-Zeilen ohne --from, ohne Flags (tools/copy-quellen.awk).
# Gemessen am 03.09.2026 an vier eigenen Repos: dessen BILD_PFADE ist genau
# diese Menge (web/ deckt web/package*.json ab). Ein Commit an tests/ oder docs/
# zählt damit nicht als Rückstand des Servers.
# pipefail (10.10.2026, Audit): ohne Dockerfile in <bis> scheiterte git show in der Pipe still, und gezählt wurde nur das Dockerfile.
set -euo pipefail
checkout=$1; von=$2; bis=$3
# Die Pfade als Array, je ein Argument (Codex-Audit 11.10.2026): unquotiert zerfiel ein Name mit Leerzeichen, und * oder ? wurden zu Dateinamen.
dockerfile=$(git -C "$checkout" show "$bis:Dockerfile")
pfade=()
while IFS= read -r p; do pfade+=("$p"); done < <(printf '%s\n' "$dockerfile" | awk -f "$(dirname "$0")/copy-quellen.awk")
if [ "${4:-}" = --log ]; then
    git -C "$checkout" log -n 20 --format='%h%x09%s' "$von..$bis" -- Dockerfile ${pfade[@]+"${pfade[@]}"}
    exit 0
fi
gesamt=$(git -C "$checkout" rev-list --count "$von..$bis")
bild=$(git -C "$checkout" rev-list --count "$von..$bis" -- Dockerfile ${pfade[@]+"${pfade[@]}"})
echo "$gesamt $bild"
