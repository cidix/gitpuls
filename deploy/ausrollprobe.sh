#!/bin/bash
# Läuft der Stand, der im Checkout steht? Gehört an das Ende JEDES Ausrollens.
#
# WARUM ES DIESE PROBE GIBT. Am 28.08.2026 lief auf dem VPS `855dbed`, während
# `gitpuls:a30bd09` und `gitpuls:fcbced4` gebaut daneben lagen und
# nie aktiviert worden waren. Zweimal stand an dem Abend "ausgerollt" in der
# Notiz, und zweimal war es für diese beiden Commits nicht wahr. GEBAUT UND
# AKTIV SEHEN VON AUSSEN GLEICH AUS: `docker images` zeigt beide, `git log`
# zeigt den neuen Stand, und nichts davon sagt, was der Container ausführt.
#
# GEMESSEN WIRD DER INHALT, NICHT EIN SCHILD. `GITPULS_TAG` in der `.env` ist
# eine Absichtserklärung — genau die Datei stand im Fehlerfall auf `855dbed`
# und sagte damit die Wahrheit über eine Absicht, die niemand ausgeführt
# hatte. Auch der Bildname am laufenden Container (`docker inspect
# .Config.Image`) ist nur ein Schild: er nennt den Tag, mit dem gestartet
# wurde, nicht das, was im Bild steht. Verglichen werden deshalb die BYTES
# der ausgelieferten Dateien im laufenden Container gegen die im Checkout.
#
# Der Tag wird trotzdem genannt, weil er beim Aufräumen hilft. Er ist die
# Auskunft, nicht der Beleg.
# pipefail (10.10.2026, Audit): ein scheiternder Teil einer Pipe beendet die Probe, statt einen leeren Abdruck als GLEICH zu vergleichen.
set -euo pipefail

REPO=${1:-$(cd "$(dirname "$0")/.." && pwd)}   # Vorgabe: der Checkout, in dem dieses Skript liegt
BEHAELTER=${2:-gitpuls-gitpuls-1}

# DIE PFADE KOMMEN AUS DEM DOCKERFILE, nicht aus einer Liste daneben.
#
# Der erste Stand dieses Skripts hashte nur `*.py` und hatte damit denselben
# Fehler, gegen den es gebaut ist: `app/web/index.html` liegt im Bild und
# wurde nicht verglichen — ein Container mit veraltetem Dashboard kam als
# GLEICH durch, und genau dort lag am 28.08.2026 die meiste Arbeit. Eine von
# Hand gepflegte Liste hätte beim nächsten COPY dieselbe Lücke wieder
# aufgemacht. Kommt eine COPY-Zeile dazu, ist sie ab sofort mitgemessen.
#
# Das letzte Feld einer COPY-Zeile ist das Ziel im Bild und fällt weg; tools/copy-quellen.awk versteht auch die JSON-Form und Flags.
# Die Pfade sind DATEN (11.10.2026, Codex-Audit Befund 4): als Array gelesen und als Argumente übergeben. Vorher standen sie als Text in
# einem `bash -c`, und ein COPY-Quellname wie «$(id)requirements.txt» lief als Befehl, als root auf dem Host.
kopf=$(git -C "$REPO" rev-parse --short HEAD)
schild=$(docker inspect "$BEHAELTER" --format '{{.Config.Image}}')

# EIN RELEASE-BILD WIRD GEGEN SEINE REVISION GEMESSEN (11.10.2026). Seit ghcr.io/cidix/gitpuls:1.0.1 läuft die eigene Instanz aus dem
# öffentlichen Bild, und main ist dem Release voraus: gegen den Arbeitsbaum wäre jede spätere Änderung eine Abweichung. Die Revision
# kommt aus dem Label org.opencontainers.image.revision (nur eine SHA zählt) oder aus dem Git-Tag v<tag>; ohne beides, beim Bau aus dem
# Checkout, gilt wie bisher der Checkout.
rev=$(docker inspect "$BEHAELTER" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' 2>/dev/null || true)
case $rev in *[!0-9a-f]*) rev=;; esac
herkunft="Label org.opencontainers.image.revision"
if [ -z "$rev" ] && rev=$(git -C "$REPO" rev-parse --verify -q "v${schild##*:}^{commit}"); then herkunft="Tag v${schild##*:}"; fi
if [ -n "$rev" ]; then
    git -C "$REPO" cat-file -e "$rev^{commit}" 2>/dev/null || { echo "Ergebnis : KEIN BELEG — Revision $rev fehlt im Checkout (git fetch)."; exit 2; }
    ziel="von $(git -C "$REPO" rev-parse --short "$rev") ($herkunft)"
    stand=$(mktemp -d)
    trap 'rm -rf "$stand"' EXIT
    git -C "$REPO" archive "$rev" | tar -x -C "$stand"
else
    ziel="des Checkouts"
    stand=$REPO
fi

PFADE=()
while IFS= read -r p; do PFADE+=("$p"); done < <(awk -f "$REPO/tools/copy-quellen.awk" "$stand/Dockerfile")
[ "${#PFADE[@]}" -gt 0 ] || { echo "Keine COPY-Zeile im Dockerfile gefunden."; exit 3; }

# Alle regulären Dateien, nicht nur Python. `__pycache__` bleibt draussen: es
# ist erzeugt und nicht ausgeliefert (`PYTHONDONTWRITEBYTECODE=1`), und ein
# Verzeichnis, das nach dem Bauen im Checkout entsteht, ergäbe genau die
# Fehlmeldung, die diese Probe schon zweimal produziert hat. `[ -e ]` fängt
# `bots/__init__.py*` ab — ein Muster, das heute auf nichts passt.
# Ein fester Text, die Pfade kommen als "$@": derselbe Abdruck im Container (docker exec … bash -c) und im Checkout.
ABDRUCK='set -o pipefail; cd "$0"; for p in "$@"; do if [ -e "$p" ]; then find "$p" -type f ! -path "*__pycache__*"; fi; done | LC_ALL=C sort | while IFS= read -r f; do sha256sum -- "$f"; done' 

echo "Checkout : $kopf"
echo "Bildname : $schild"

# ZUERST DIE SAUBERKEIT, DANN DER VERGLEICH. Vorher stand sie hinter der
# Ausgabe, und ein schmutziger Checkout bekam erst "GLEICH" und dann eine
# Warnung — die Erfolgsmeldung war schon gelesen, wenn der Vorbehalt kam.
# Verglichen wird gegen ARBEITSDATEIEN, und über den Commit sagt das nichts.
#
# Gefragt werden dieselben Pfade wie beim Vergleich: die Probe schlug am
# 28.08.2026 auf eine unversionierte Datei im Checkout an, eine
# Leiche ausserhalb des Bildes. Eine Warnung, die bei etwas anschlägt, das
# die Sache nicht berührt, wird nach dem dritten Mal weggelesen.
dreckig=$( [ -n "$rev" ] || { cd "$REPO" && git status --porcelain -- "${PFADE[@]}"; })   # gegen eine Revision zählt der Arbeitsbaum nicht
if [ -n "$dreckig" ]; then
    echo "Ergebnis : KEIN BELEG — der Checkout ist in den ausgelieferten"
    echo "           Pfaden nicht sauber. Ein Vergleich gegen Arbeitsdateien"
    echo "           sagt nichts über $kopf."
    echo "$dreckig" | sed 's/^/           /'
    exit 2
fi

drin=$(docker exec "$BEHAELTER" bash -c "$ABDRUCK" /app "${PFADE[@]}")
draussen=$(bash -c "$ABDRUCK" "$stand" "${PFADE[@]}")

if [ "$drin" = "$draussen" ]; then
    echo "Ergebnis : GLEICH — der Container führt den Stand $ziel aus."
    exit 0
fi

echo "Ergebnis : ABWEICHUNG — der Container führt NICHT den Stand des"
echo "           Checkouts aus. Was gebaut wurde, sagt darüber nichts."
# Nur die Namen, nicht die Prüfsummen: die Frage ist WELCHE Datei, und zwei
# Spalten Hexadezimalzahlen beantworten sie nicht.
echo "$drin"     > /tmp/ausrollprobe.drin
echo "$draussen" > /tmp/ausrollprobe.draussen
diff /tmp/ausrollprobe.drin /tmp/ausrollprobe.draussen \
    | awk '/^[<>]/ {print $NF}' | LC_ALL=C sort -u | sed 's/^/           /'
exit 1
