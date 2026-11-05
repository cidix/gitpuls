// Selbstprüfung der Aufnahmen: gleich grosse Vergleichsgruppen und Geometrie im Bild bestehen, ein ungleiches Paar Hell/Dunkel,
// eine fehlende Datei und ein Klickpunkt ausserhalb brechen ab.
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pruefen } from './pruefung.mjs';

// kleinstes PNG mit IHDR: Breite und Höhe ab Byte 16
const png = (b, h) => { const x = Buffer.alloc(33); x.writeUInt32BE(b, 16); x.writeUInt32BE(h, 20); return x; };
const eintrag = (modus, h, mehr = {}) => ({ id: 'server', zustand: 'ganz', sprache: 'en', breite: 1100, modus, datei: `server--ganz--en-${modus}-1100.png`, pixel_breite: 2136, pixel_hoehe: h, ...mehr });
const lauf = (eintraege) => {
  const dir = mkdtempSync(join(tmpdir(), 'pruefung-'));
  for (const e of eintraege) writeFileSync(join(dir, e.datei), png(e.pixel_breite, e.pixel_hoehe));
  try { return pruefen(eintraege, dir); } finally { rmSync(dir, { recursive: true }); }
};

test('Hell und Dunkel gleich gross, Geometrie im Bild: keine Befunde', () => {
  assert.deepEqual(lauf([eintrag('dunkel', 1508, { ziel: { x: .5, y: .2 }, spalte: { y: .06, h: .2, w: .49 } }), eintrag('hell', 1508)]), []);
});

test('Gegenprobe: Hell und Dunkel ungleich hoch bricht ab', () => {
  assert.match(lauf([eintrag('dunkel', 1508), eintrag('hell', 1608)]).join('\n'), /server en 1100: dunkel ganz 2136x1508, hell ganz 2136x1608/);
});

test('Gegenprobe: PNG passt nicht zum Manifest, Datei fehlt, Klickpunkt ausserhalb', () => {
  const dir = mkdtempSync(join(tmpdir(), 'pruefung-'));
  writeFileSync(join(dir, 'server--ganz--en-dunkel-1100.png'), png(2136, 1500));
  const ab = pruefen([eintrag('dunkel', 1508, { ziel: { x: 1.2, y: .5 } }), eintrag('hell', 1508)], dir).join('\n');
  rmSync(dir, { recursive: true });
  assert.match(ab, /dunkel-1100\.png: PNG 2136x1500, Manifest 2136x1508/);
  assert.match(ab, /hell-1100\.png: Datei fehlt/);
  assert.match(ab, /dunkel-1100\.png: ziel ausserhalb/);
});
