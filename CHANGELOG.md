# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.1.0] - 2026-10-26

### Added

- A meta line "Full last week: N" in the Week view of the second tile: the commits of the whole previous week,
  Monday to Sunday, from the same series as the dashed line. An absolute number, no percentage and no comparison.

### Changed

- The ring in the header shows a closed ring with 0 instead of a sentence when there are 0 commits, in both the
  7 days and the Week view. The status sentence above keeps its wording.

## [2.0.0] - 2026-10-17

Same content as 1.1.0, released again as a major version. 1.1.0 already contained this breaking change
(operators with the host timer `gitpuls-sterne` must remove it) and should have been released as a major version.

### Removed

- The ranking of young public repositories by stars ("Other repos by stars") with its 7, 30 and 90 day tabs and the
  `?sterne=` parameter. Old links with `?sterne=` still open the page; the parameter is ignored.
- The GitHub search API and Hacker News (Algolia) requests behind the ranking. gitpuls now talks only to the
  GitHub API for your own repositories.
- The star run: `app/sterne.py`, the host units `deploy/gitpuls-sterne.service` and `deploy/gitpuls-sterne.timer`,
  and the daily star run of the in-container interval (`GITPULS_TAKT_MIN` now starts only the collector).
- The setting `GITPULS_STACK`. A value left in `.env` or `compose.yaml` is ignored and does not stop the start.

### Upgrade notes

- The tables `sterne`, `fremde` and `fremde_detail` stay in your database untouched; gitpuls no longer creates or
  reads them. You may drop them, e.g. `sqlite3 gitpuls.db 'DROP TABLE sterne; DROP TABLE fremde; DROP TABLE fremde_detail;'`
  while no collector runs.
- If you installed the host timer, remove it: `systemctl disable --now gitpuls-sterne.timer`, delete the two unit
  files, `systemctl daemon-reload`, and drop `gitpuls-sterne` from the `timer` list in your `tools/dienste.json`.

## [1.1.0] - 2026-10-17

Contains the breaking change described under [2.0.0](#200---2026-10-17); use 2.0.0 instead.

### Removed

- The ranking of young public repositories by stars ("Other repos by stars") with its 7, 30 and 90 day tabs and the
  `?sterne=` parameter. Old links with `?sterne=` still open the page; the parameter is ignored.
- The GitHub search API and Hacker News (Algolia) requests behind the ranking. gitpuls now talks only to the
  GitHub API for your own repositories.
- The star run: `app/sterne.py`, the host units `deploy/gitpuls-sterne.service` and `deploy/gitpuls-sterne.timer`,
  and the daily star run of the in-container interval (`GITPULS_TAKT_MIN` now starts only the collector).
- The setting `GITPULS_STACK`. A value left in `.env` or `compose.yaml` is ignored and does not stop the start.

### Upgrade notes

- The tables `sterne`, `fremde` and `fremde_detail` stay in your database untouched; gitpuls no longer creates or
  reads them. You may drop them, e.g. `sqlite3 gitpuls.db 'DROP TABLE sterne; DROP TABLE fremde; DROP TABLE fremde_detail;'`
  while no collector runs.
- If you installed the host timer, remove it: `systemctl disable --now gitpuls-sterne.timer`, delete the two unit
  files, `systemctl daemon-reload`, and drop `gitpuls-sterne` from the `timer` list in your `tools/dienste.json`.

## [1.0.4] - 2026-10-15

### Changed

- A service running a release image with a newer release tag `v*` no longer reads "behind main": the summary
  sentence, the heading in the action list, the entry, the short form in the project row and its details name the
  newer version instead, e.g. "Server 1 with newer version v1.0.4". A service running a commit image still reads
  "behind main".
- Footer: "gitpuls.com" in the copyright links to https://gitpuls.com, in English and German.

## [1.0.3] - 2026-10-11

### Fixed

- Server zone: the boxes Machine, Installed, Firewall and Updates share one label column again, wide enough for the
  longest label in English and German, so the values start at the same place in all four boxes. 1.0.1 sized the
  column per box.

## [1.0.2] - 2026-10-11

### Fixed

- The footer shows the version built into the image. With the `.env` from `.env.example` the quick start showed
  "Version unbekannt"; `GITPULS_TAG` now only selects the image. An image built from source without the build argument
  shows `GITPULS_TAG` as before.

## [1.0.1] - 2026-10-11

### Fixed

- Server zone: in the boxes Machine, Installed, Firewall and Updates the label column takes the width of the longest
  label of its box, with the same distance to the value in every box; a long label such as "Automatische Updates" in
  the German interface no longer runs into its value.

## [1.0.0] - 2026-10-11

First public release.

### Added

- Server zone: a summary sentence for the host and a list of your services with their last timer run; each service
  opens to its containers, runs and distance to the default branch.
- Machine and installed software: load, memory, disk and boot time next to the versions of Docker, Compose, containerd,
  Tailscale, the system and the kernel.
- Firewall and updates: rules in INPUT and DOCKER-USER per IP family (iptables), whether they survive a reboot, and
  pending apt updates including the ones to pull by hand. Every value the host script cannot measure is shown as
  "not measured" instead of failing.
- "Needs attention" block: security alerts, failed Actions runs, services behind the default branch, branches waiting
  on the default branch, Dependabot updates, values that could not be measured, and a warning 14 days before the
  GitHub token expires.
- Commit tiles: today against all of yesterday; the last 7 days or this week and the last 30 days or this month against
  the period before, up to the same point in time.
- Project list with one row per repository, its commits, a 30-day sparkline and its findings; a row opens to the week,
  the lines changed and the latest subjects.
- History of commits per day over 7, 30, 60 or 90 days or since the start, with streaks and records.
- Ranking of young public repositories that gain stars fastest, over 7, 30 or 90 days.
- Light and dark theme following the system or chosen by hand, a layout for phones, an English and a German interface
  and number and date formats per locale; days, weeks and months are counted in your own time zone.
- Collector inside the container on its own interval, or from the systemd timers in `deploy/`; the image is published
  for linux/amd64 and linux/arm64 with build provenance.

### Security

- Published on `127.0.0.1` only, allowed host names against DNS rebinding, same-origin check for the refresh button,
  token read from a file mounted read-only, container runs as UID 10001 with a read-only root file system and no
  capabilities.

[Unreleased]: https://github.com/cidix/gitpuls/compare/v2.1.0...HEAD
[2.1.0]: https://github.com/cidix/gitpuls/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/cidix/gitpuls/compare/v1.1.0...v2.0.0
[1.1.0]: https://github.com/cidix/gitpuls/compare/v1.0.4...v1.1.0
[1.0.4]: https://github.com/cidix/gitpuls/compare/v1.0.3...v1.0.4
[1.0.3]: https://github.com/cidix/gitpuls/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/cidix/gitpuls/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/cidix/gitpuls/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/cidix/gitpuls/releases/tag/v1.0.0
