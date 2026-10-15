#!/bin/bash
# Deploy-Abstand: welcher Commit läuft im Container, wie weit ist der Default-Branch auf origin voraus.
# Läuft als root auf dem Host (gitpuls-deploy.timer, stündlich), schreibt
# deploy.json ins gitpuls-Volume. Der Webprozess liest nur die Datei, dieselbe
# Trennung wie bei den Knöpfen der Kommandozentrale: kein Shell-Aufruf aus dem Web.
#
# Der Tag eines selbst gebauten Bildes ist ein Commit. Ein Release-Bild (ghcr.io/cidix/gitpuls:1.0.1, seit 11.10.2026) trägt einen
# Versions-Tag: der Stand kommt dann aus dem Label org.opencontainers.image.revision oder aus dem Git-Tag v<tag>.
# Messvertrag (03.10.2026, Codex-Audit): scheitert eine Messung (Container fehlt, fetch, rev-parse, bildrelevant.sh, log), bleibt der
# letzte gelungene Eintrag des Repos mit seinem alten Zeitpunkt "gemessen" stehen und trägt dazu "fehler"; ohne früheren Eintrag
# steht nur "fehler". Vorher fiel das Repo weg (kein Befund) oder ein alter origin/main bekam nach gescheitertem fetch einen frischen
# Zeitpunkt. Die Seite zeigt beides als «nicht gemessen».
# Seit 10.10.2026 (Audit): gemessen wird gegen den Default-Branch des Dienstes auf origin (ls-remote), nicht fest gegen origin/main; ein
# Tag, der im Checkout keinen Commit benennt, ist nicht gemessen; ein Dienst ohne Container oder ohne Repo in der Tabelle wird mit
# Meldung übersprungen. Ziel und Tabelle kommen aus der Umgebung (GITPULS_DEPLOY_ZIEL, GITPULS_VOLUME, GITPULS_DIENSTE), die Unit liest sie
# aus der .env des Checkouts wie compose.yaml.
set -uo pipefail
VOLUME=${GITPULS_VOLUME:-gitpuls-daten}
ZIEL=${GITPULS_DEPLOY_ZIEL:-/var/lib/docker/volumes/$VOLUME/_data/deploy.json}
DIENSTE=${GITPULS_DIENSTE:-$(dirname "$0")/dienste.json}
# Fehlt die Tabelle, ist das ein Fehler der Einrichtung, kein Host ohne Dienste (10.10.2026): Meldung und Exit 1, der Timer zeigt ihn als gescheitert.
[ -f "$DIENSTE" ] || { echo "Keine Diensttabelle unter $DIENSTE (GITPULS_DIENSTE oder tools/dienste.json), kein Deploy-Abstand gemessen." >&2; exit 1; }
# Je Dienst Name, Repo, erster Container und Checkout aus der Diensttabelle, derselben wie beim Serverstand (tools/server-stand), durch
# \x1f getrennt (ein Tabulator ist für read Leerraum, zwei hintereinander fielen zusammen und ein leeres Feld verrutschte). Seit 11.10.2026 (Codex-Audit) ist der Schlüssel der Dienst, nicht das Repo (Befund 9: Produktion und Staging eines
# Repos überschrieben sich, und der veraltete Stand verschwand), und ein ungültiger Eintrag betrifft nur sich selbst (Befund 8): er steht
# als «fehler»-Zeile mit Grund, statt mit KeyError die ganze Messung zu beenden. Eine Tabelle ohne Liste unter "dienste" endet mit Exit 1.
EINTRAEGE=$(python3 -c 'import json,sys
tab = json.load(open(sys.argv[1]))
if not isinstance(tab, dict) or not isinstance(tab.get("dienste"), list):
    sys.exit("Diensttabelle ungültig: erwartet ein Objekt mit einer Liste unter \"dienste\"")
namen = [d.get("name") if isinstance(d, dict) else None for d in tab["dienste"]]
for i, d in enumerate(tab["dienste"]):
    name = namen[i]
    if not isinstance(name, str) or not name or any(z in name for z in "\x1f\t\n"):
        print("fehler", name if isinstance(name, str) and name.strip() else f"Eintrag {i + 1}", "", "Diensttabelle: Eintrag ohne gültigen Namen", sep="\x1f")
        continue
    if namen.count(name) > 1:   # der Name ist der Schlüssel in deploy.json: doppelt hiesse wieder überschreiben
        print("fehler", name, "", "Diensttabelle: Name mehrfach vergeben", sep="\x1f")
        continue
    c = d.get("container", [])
    werte = [d.get("repo") or "", c[0] if isinstance(c, list) and c else "", d.get("checkout") or ""]
    if not isinstance(c, list) or not all(isinstance(x, str) for x in c) or not all(isinstance(w, str) and not any(z in w for z in "\x1f\t\n") for w in werte):
        print("fehler", name, werte[0] if isinstance(werte[0], str) else "", "Diensttabelle: repo, checkout und container ungültig", sep="\x1f")
        continue
    print("ok", name, *werte, sep="\x1f")' "$DIENSTE") || exit 1

# Die temporären Dateien liegen im Zielverzeichnis, damit mv im selben Dateisystem umbenennt und der Webprozess nie ein
# halbes JSON liest; dasselbe Muster wie os.replace in tools/server-stand. Bis 13.09.2026 stand sie in /tmp, auf dem
# VPS ein tmpfs neben dem ext4-Volume: mv kopierte dort über die Dateisystemgrenze, statt zu tauschen.
tmp=$(mktemp "$(dirname "$ZIEL")/.deploy.json.XXXXXX") || exit 1
mess=$(mktemp "$(dirname "$ZIEL")/.deploy.mess.XXXXXX") || { rm -f "$tmp"; exit 1; }
# Je Dienst eine Zeile «name TAB ok TAB repo TAB tag TAB kopf TAB hinter TAB bild TAB gemessen TAB branch TAB gleich TAB commits-json» oder
# «name TAB fehler TAB repo TAB text».
US=$(printf '\037')   # \x1f ohne $'…', das dash nicht kennt (die Tests starten das Skript mit sh)
printf '%s\n' "$EINTRAEGE" | while IFS=$US read -r art name repo behaelter checkout; do
    [ -n "$name" ] || continue
    fehler() { printf '%s\tfehler\t%s\t%s\n' "$name" "$repo" "$1" >> "$mess"; }
    [ "$art" = ok ] || { fehler "$behaelter"; continue; }   # bei «fehler» steht der Grund an der Stelle des Containers
    [ -n "$repo" ] || { echo "$name: kein Repo in der Diensttabelle, kein Deploy-Abstand" >&2; continue; }
    [ -n "$behaelter" ] || { echo "$name: kein Container in der Diensttabelle, übersprungen" >&2; continue; }
    bild=$(docker inspect "$behaelter" --format '{{.Config.Image}}' 2>/dev/null) || { fehler "Container $behaelter nicht gefunden"; continue; }
    tag=${bild##*:}
    git -C "$checkout" fetch -q --tags origin 2>/dev/null || { fehler "git fetch in $checkout gescheitert"; continue; }   # mit den Release-Tags v*
    branch=$(git -C "$checkout" ls-remote --symref origin HEAD 2>/dev/null | awk '$1 == "ref:" && $3 == "HEAD" { sub("^refs/heads/", "", $2); print $2 }')
    [ -n "$branch" ] || { fehler "Default-Branch von origin in $checkout nicht lesbar"; continue; }
    kopf=$(git -C "$checkout" rev-parse --short "origin/$branch" 2>/dev/null) || { fehler "origin/$branch in $checkout nicht lesbar"; continue; }
    # Stand: zuerst das Revisionslabel des Containers (nur eine SHA zählt), dann der Tag als Commit, dann der Git-Tag v<tag>.
    rev=$(docker inspect "$behaelter" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' 2>/dev/null)
    case $rev in *[!0-9a-f]*) rev=;; esac
    stand=
    for ref in $rev "$tag" "v$tag"; do
        voll=$(git -C "$checkout" rev-parse --verify -q "$ref^{commit}") && { stand=$ref; break; }
    done
    [ -n "$stand" ] || { fehler "Tag $tag benennt im Checkout keinen Commit"; continue; }
    # Release-Bild (11.10.2026): trägt der Container das Label org.opencontainers.image.version (release.yml), ist der Vergleich das neueste
    # Release-Tag v*, nicht main. Auf dem neuesten Release gibt es keinen Befund, auch wenn main weiter ist; je neueres Release steht eine
    # Zeile «neuere Version verfügbar» mit seiner Nummer in "commits", und "hinter" zählt die neueren Releases.
    version=$(docker inspect "$behaelter" --format '{{index .Config.Labels "org.opencontainers.image.version"}}' 2>/dev/null)
    case $version in *[!0-9.]*|.*|*.) version=;; esac
    neuer=
    if [ -n "$version" ]; then
        neuere=$(git -C "$checkout" tag -l 'v[0-9]*' --sort=-v:refname | awk -v eigen="v$version" '$0 == eigen { exit } { print }')
        branch=$(git -C "$checkout" tag -l 'v[0-9]*' --sort=-v:refname | head -n 1)
        [ -n "$branch" ] || { fehler "kein Release-Tag v* in $checkout"; continue; }
        kopf=$(git -C "$checkout" rev-parse --short "$branch^{commit}" 2>/dev/null) || { fehler "Release-Tag $branch in $checkout nicht lesbar"; continue; }
        hinter=$(printf '%s' "$neuere" | grep -c . || true); bild=$hinter
        gleich=0; [ "$hinter" = 0 ] && gleich=1
        neuer=$(printf '%s\n' "$neuere" | head -n 1); neuer=${neuer#v}
        commits=$(printf '%s\n' "$neuere" | python3 -c 'import json,sys; print(json.dumps([{"sha": t, "betreff": "neuere Version verfügbar"} for t in sys.stdin.read().split()], ensure_ascii=False))') \
            || { fehler "Release-Liste nicht lesbar"; continue; }
    else
        # «gleich» nur, wenn der laufende Commit der Kopf des Default-Branch ist (Codex-Audit 11.10.2026, Befund 13): 0 hinter heisst nur «nichts
        # fehlt», auch ein Stand vor main oder nach einem Zurücksetzen von main neben ihm hat 0 hinter.
        kopf_voll=$(git -C "$checkout" rev-parse --verify -q "origin/$branch^{commit}") || { fehler "origin/$branch in $checkout nicht lesbar"; continue; }
        gleich=0; [ "$voll" = "$kopf_voll" ] && gleich=1
        # Gezählt werden nur Commits, die das Bild berühren (tools/bildrelevant.sh, derselbe Massstab wie der Tag-Wächter eines anderen
        # eigenen Dienstes): ein Commit an tests/ oder docs/ ist kein Rückstand des Dienstes (11.10.2026, vorher zählte "hinter" alle).
        zahlen=$("$(dirname "$0")/bildrelevant.sh" "$checkout" "$stand" "origin/$branch" 2>/dev/null) || { fehler "bildrelevant.sh für $tag gescheitert"; continue; }
        bild=${zahlen##* }; hinter=$bild
        # Die nicht ausgerollten Commits mit Betreff (02.10.2026) für Projektzeile und Handlungsbedarf, aus dem Checkout, kein
        # GitHub-Abruf; seit 11.10.2026 nur die bildrelevanten. Höchstens 20, neuester zuerst; die Zahl steht vollständig in "hinter".
        log=$("$(dirname "$0")/bildrelevant.sh" "$checkout" "$stand" "origin/$branch" --log 2>/dev/null) || { fehler "Commit-Liste $stand..origin/$branch gescheitert"; continue; }
        commits=$(printf '%s\n' "$log" | python3 -c 'import json,sys; print(json.dumps([dict(zip(("sha", "betreff"), l.split("\t", 1))) for l in sys.stdin.read().splitlines() if l], ensure_ascii=False))') \
            || { fehler "Commit-Liste nicht lesbar"; continue; }
    fi
    printf '%s\tok\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$name" "$repo" "$tag" "$kopf" "$hinter" "$bild" "$(date -Iseconds)" "$branch" "$gleich" "$neuer" "$commits" >> "$mess"
done
python3 - "$ZIEL" "$mess" "$tmp" <<'PY' || { rm -f "$tmp" "$mess"; exit 1; }
import json, sys
ziel, mess, tmp = sys.argv[1:]
try:
    alt = json.load(open(ziel))
    alt = alt if isinstance(alt, dict) else {}
except (OSError, ValueError):
    alt = {}
neu = {}
for z in open(mess, encoding="utf-8").read().splitlines():
    name, art, repo, *rest = z.split("\t")
    if art == "ok":
        tag, kopf, hinter, bild, gemessen, branch, gleich, neuer, commits = rest
        neu[name] = {"repo": repo, "tag": tag, "origin_main": kopf, "branch": branch, "hinter": int(hinter), "bildrelevant": int(bild),
                     "gleich": gleich == "1", "commits": json.loads(commits), "gemessen": gemessen, **({"neuer": neuer} if neuer else {})}
    else:
        frueher = alt.get(name) if isinstance(alt.get(name), dict) else {}
        neu[name] = {**{k: v for k, v in frueher.items() if k != "fehler"}, "repo": repo or frueher.get("repo"), "fehler": rest[0]}
json.dump(neu, open(tmp, "w"), ensure_ascii=False, indent=1)
PY
rm -f "$mess"
chmod 644 "$tmp"
mv "$tmp" "$ZIEL"
cat "$ZIEL"
