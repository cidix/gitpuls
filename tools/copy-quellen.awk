# Die Quellen aller COPY-Zeilen eines Dockerfiles, eine je Zeile (11.10.2026, Codex-Audit Befund 4: die Aufrufer lesen sie als Array und
# geben sie als Argumente weiter, nie als Text in eine Shell): in Shell-Form und in JSON-Form (COPY ["app/", "./app/"]),
# ohne Flags (--chown=…) und ohne Zeilen mit --from= (sie kopieren aus einer anderen Stufe, nicht aus dem Checkout). Das letzte Feld ist das
# Ziel im Bild und fällt weg. Eine Stelle für tools/bildrelevant.sh und deploy/ausrollprobe.sh (10.10.2026, Audit); vorher stand je ein
# awk in beiden, und die JSON-Form ergab Pfade wie «["app/",», die nichts trafen.
toupper($1) == "COPY" && $0 !~ /--from=/ {
    sub(/^[ \t]*[Cc][Oo][Pp][Yy][ \t]+/, "")
    while ($0 ~ /^--/) sub(/^--[^ \t]+[ \t]+/, "")
    if ($0 ~ /^\[/) {
        gsub(/^\[[ \t]*"|"[ \t]*\][ \t]*$/, "")
        n = split($0, f, /"[ \t]*,[ \t]*"/)
    } else n = split($0, f, /[ \t]+/)
    for (i = 1; i < n; i++) print f[i]
}
