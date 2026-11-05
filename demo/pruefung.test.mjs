// Selbstprüfung der Aufnahmen vor dem Tausch: eine Datei, die nicht zum Manifest passt, eine fehlende Datei und ein Klickpunkt
// ausserhalb brechen ab; eine stimmige Aufnahme besteht.
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

test('Gegenprobe: stimmige Aufnahme ohne Befund', () => assert.deepEqual(lauf([eintrag('dunkel', 1508, { ziel: { x: .5, y: .2 } })]), []));

test('PNG passt nicht zum Manifest, Datei fehlt, Klickpunkt ausserhalb', () => {
  const dir = mkdtempSync(join(tmpdir(), 'pruefung-'));
  writeFileSync(join(dir, 'server--ganz--en-dunkel-1100.png'), png(2136, 1500));
  const ab = pruefen([eintrag('dunkel', 1508, { ziel: { x: 1.2, y: .5 } }), eintrag('hell', 1508)], dir).join('\n');
  rmSync(dir, { recursive: true });
  assert.match(ab, /dunkel-1100\.png: PNG 2136x1500, Manifest 2136x1508/);
  assert.match(ab, /hell-1100\.png: Datei fehlt/);
  assert.match(ab, /dunkel-1100\.png: ziel ausserhalb/);
});
