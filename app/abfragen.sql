-- Die drei Ansichten. Parameter: :seit (UTC-Text), :adressen über IN (...),
-- optional :repo. "Alle Repos" lässt die repo-Bedingung weg.

-- Tag (Kalenderraster, letzte 12 Monate)
SELECT date(zeitstempel_utc, 'localtime') AS tag, COUNT(*) AS n
FROM commits
WHERE zeitstempel_utc >= :seit AND autor_adresse IN (:a1, :a2, :a3)
GROUP BY tag ORDER BY tag;

-- Woche (Balken, 26 Wochen), ISO-Woche ab Montag
SELECT strftime('%G-W%V', zeitstempel_utc, 'localtime') AS woche, COUNT(*) AS n
FROM commits
WHERE zeitstempel_utc >= :seit AND autor_adresse IN (:a1, :a2, :a3)
GROUP BY woche ORDER BY woche;

-- Monat (Balken, 12 Monate)
SELECT strftime('%Y-%m', zeitstempel_utc, 'localtime') AS monat, COUNT(*) AS n
FROM commits
WHERE zeitstempel_utc >= :seit AND autor_adresse IN (:a1, :a2, :a3)
GROUP BY monat ORDER BY monat;

-- Ein Repo: dieselben drei Abfragen mit zusätzlich  AND repo = :repo
