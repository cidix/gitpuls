-- gitpuls: eine Zeile je Commit, keine vorberechneten Summen.
-- Tag, Woche und Monat entstehen bei der Abfrage aus zeitstempel_utc.
--
-- Zeitzone: zeitstempel_utc ist UTC-Text im API-Format (YYYY-MM-DDTHH:MM:SSZ).
-- Die Einteilung nach Tag/Woche/Monat rechnet mit dem Modifikator 'localtime',
-- und der liest TZ des Prozesses; app/web.py::systemzone() liest dieselbe Quelle, damit
-- Python und SQL dieselbe Zone verwenden. TZ setzt der Betreiber in der .env (compose.yaml).
-- Fehlt TZ, fällt die Einteilung still auf UTC zurück.
-- %G-W%V (ISO-Woche, Montag als Wochenbeginn) braucht SQLite >= 3.46.
-- Gemessen 2026-09-02: Mac 3.53.4, Zielbild python:3.13-slim-trixie 3.46.1.

CREATE TABLE IF NOT EXISTS commits (
    sha             TEXT PRIMARY KEY,
    repo            TEXT NOT NULL,              -- nameWithOwner, z.B. octo-org/hello-world
    zeitstempel_utc TEXT NOT NULL,              -- commit.author.date
    autor_adresse   TEXT NOT NULL,              -- commit.author.email
    autor_login     TEXT                        -- author.login, NULL wenn GitHub die Adresse keinem Konto zuordnet
) WITHOUT ROWID;

-- Für die Ansichten "alle Repos": Bereichssuche über den Zeitraum, repo und
-- autor_adresse liegen mit im Index, die Tabelle wird nicht angefasst.
CREATE INDEX IF NOT EXISTS commits_zeit ON commits (zeitstempel_utc, repo, autor_adresse);

-- Für die Ansichten "ein Repo": erst repo, dann Zeitraum.
CREATE INDEX IF NOT EXISTS commits_repo_zeit ON commits (repo, zeitstempel_utc, autor_adresse);

-- Ein Eintrag je Sammellauf. Das ist keine Summe, sondern die Quelle für
-- "Alter der Daten" in der Kopfzeile: das Alter des letzten Laufs, nicht des
-- letzten Commits.
CREATE TABLE IF NOT EXISTS laeufe (
    gestartet_utc   TEXT PRIMARY KEY,
    beendet_utc     TEXT NOT NULL,
    neu             INTEGER NOT NULL            -- in diesem Lauf eingefügte Zeilen
) WITHOUT ROWID;
-- Bestehende Datenbanken (Mac, VPS) tragen zusätzlich zustand TEXT und token_bis TEXT
-- aus 4b3d201 (03.09.2026), das der Reset auf 0a457fc am 04.09.2026 verwarf. Der Sammler
-- nennt seine Spalten deshalb ausdrücklich; die zwei bleiben leer.

-- Bonusteil: fremde Repos aus der Search-API, je Repo und Tag ein Sternstand.
-- Keine Zuwächse gespeichert, die entstehen bei der Abfrage aus zwei Tagen.
CREATE TABLE IF NOT EXISTS sterne (
    repo    TEXT NOT NULL,                      -- full_name
    tag     TEXT NOT NULL,                      -- Abruftag, YYYY-MM-DD in der Zone des Systems
    sterne  INTEGER NOT NULL,                   -- stargazers_count am Abruftag
    PRIMARY KEY (repo, tag)
) WITHOUT ROWID;

-- Metadaten je fremdem Repo, einmal je Repo, beim Sternlauf aufgefrischt.
-- Sternstände bleiben in `sterne`, die Ranglisten entstehen bei der Abfrage.
CREATE TABLE IF NOT EXISTS fremde (
    repo         TEXT PRIMARY KEY,               -- full_name
    erstellt     TEXT NOT NULL,                  -- created_at, UTC
    sprache      TEXT,                           -- language, NULL wenn GitHub keine nennt
    beschreibung TEXT,                           -- description
    gesehen      TEXT NOT NULL                   -- letzter Sternlauf, der das Repo lieferte
) WITHOUT ROWID;

-- Dritter Umbau (2026-09-03): Betreffzeile je Commit, Repo-Metadaten, Zweige.
-- Nachziehen bestehender Datenbanken passiert in db.py (ALTER TABLE), weil
-- CREATE TABLE IF NOT EXISTS eine vorhandene Tabelle nicht erweitert.
CREATE TABLE IF NOT EXISTS repos (
    repo            TEXT PRIMARY KEY,
    default_branch  TEXT NOT NULL,
    sprache         TEXT,
    pushed_at       TEXT,
    gesehen         TEXT NOT NULL,                  -- letzter Sammellauf
    id              INTEGER                         -- GitHub-ID: überlebt eine Umbenennung (11.09.2026); bestehende DBs per db.py
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS zweige (
    repo            TEXT NOT NULL,
    name            TEXT NOT NULL,
    ahead           INTEGER NOT NULL,               -- Commits vor dem Default-Branch
    behind          INTEGER NOT NULL,               -- Commits hinter dem Default-Branch
    letzter_utc     TEXT,                           -- Datum des Zweigkopfs
    PRIMARY KEY (repo, name)
) WITHOUT ROWID;

-- Sechster Umbau: Details je fremdem Repo, nur für die, die auf der Seite erscheinen können.
CREATE TABLE IF NOT EXISTS fremde_detail (
    repo            TEXT PRIMARY KEY,
    release         TEXT,                           -- tag_name des letzten Releases
    release_datum   TEXT,
    readme          TEXT,                           -- erster Absatz, 300 Zeichen
    hn              TEXT,                           -- JSON-Liste der Hacker-News-Treffer
    gesehen         TEXT NOT NULL
) WITHOUT ROWID;

-- CI und Sicherheitswarnungen je Repo (25.09.2026). Seit 03.10.2026 (Messvertrag, db.py NACHTRAEGE) tragen ci und sicherheit das Ergebnis
-- der letzten gelungenen Messung: ci in ok, fehlgeschlagen, keine (kein Actions-Lauf), unbekannt (nie gemessen); sicherheit in ok, aus
-- (Dependabot ausgeschaltet), unbekannt. Der Fehler der letzten Abfrage steht getrennt in ci_fehler und sicher_fehler.
CREATE TABLE IF NOT EXISTS pruefungen (
    repo            TEXT PRIMARY KEY,
    ci              TEXT NOT NULL,
    ci_workflow     TEXT,
    ci_utc          TEXT,                           -- Ende des letzten abgeschlossenen Laufs auf dem Default-Branch
    sicherheit      TEXT NOT NULL,
    warnungen       INTEGER,                        -- offene Dependabot-Warnungen, NULL ohne Messung
    warn_stufe      TEXT,                           -- höchste Stufe der offenen Warnungen
    gesehen         TEXT NOT NULL
) WITHOUT ROWID;

-- Zeilen je Datei und Commit (25.09.2026), roh aus dem Commit-Detail. Welche davon als Codezeilen zählen, entscheidet db.zaehlt() bei
-- jeder Abfrage (SQL-Funktion zaehlt); so lässt sich die Regel ändern, ohne neu abzurufen.
CREATE TABLE IF NOT EXISTS commit_dateien (
    sha             TEXT NOT NULL,
    pfad            TEXT NOT NULL,
    plus            INTEGER NOT NULL,
    minus           INTEGER NOT NULL,
    PRIMARY KEY (sha, pfad)
) WITHOUT ROWID;

-- Offene Dependabot-Warnungen je Repo (25.09.2026), bei jedem gelungenen Abruf des Repos ersetzt: eine geschlossene Warnung verschwindet
-- mit dem nächsten Lauf. Ist Dependabot aus, wird geleert; bei jedem Fehler (auch 404) bleibt der letzte Stand und die Seite zeigt ihn,
-- dazu den Fehler als «nicht gemessen» (seit 03.10.2026).
CREATE TABLE IF NOT EXISTS warnungen (
    repo            TEXT NOT NULL,
    nummer          INTEGER NOT NULL,               -- number der Warnung im Repo
    paket           TEXT NOT NULL,
    oekosystem      TEXT,                           -- npm, pip …
    manifest        TEXT,                           -- manifest_path, etwa web/package-lock.json
    stufe           TEXT,                           -- low, medium, high, critical
    behoben         TEXT,                           -- erste behobene Version, NULL ohne Fix
    url             TEXT NOT NULL,                  -- html_url der Warnung
    PRIMARY KEY (repo, nummer)
) WITHOUT ROWID;
