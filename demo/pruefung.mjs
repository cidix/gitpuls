// Selbstprüfung der Aufnahmen (demo/aufnahmen.mjs), ohne Browser testbar: node --test demo/pruefung.test.mjs
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

export function pngMasse(buffer) {   // IHDR: Breite und Höhe ab Byte 16
  return { breite: buffer.readUInt32BE(16), hoehe: buffer.readUInt32BE(20) };
}

// Selbstprüfung über das fertige Arbeitsverzeichnis: je Vergleichsgruppe (id, Sprache, Breite, über Modus und Zustand) dieselben
// Masse, jede Datei mit den Massen aus dem Manifest, jeder Geometriewert endlich und innerhalb seines Bildes.
export function pruefen(manifest, dir) {
  const ab = [];
  const anteil = (v) => typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 1;
  const positiv = (v) => typeof v === 'number' && Number.isFinite(v) && v > 0;
  for (const [k, v] of Map.groupBy(manifest, (m) => `${m.id} ${m.sprache} ${m.breite}`))
    if (new Set(v.map((m) => `${m.pixel_breite}x${m.pixel_hoehe}`)).size > 1) ab.push(`${k}: ${v.map((m) => `${m.modus} ${m.zustand} ${m.pixel_breite}x${m.pixel_hoehe}`).join(', ')}`);
  for (const m of manifest) {
    const d = m.datei, px = existsSync(join(dir, d)) ? pngMasse(readFileSync(join(dir, d))) : null;
    if (!px) { ab.push(`${d}: Datei fehlt`); continue; }
    if (px.breite !== m.pixel_breite || px.hoehe !== m.pixel_hoehe) ab.push(`${d}: PNG ${px.breite}x${px.hoehe}, Manifest ${m.pixel_breite}x${m.pixel_hoehe}`);
    if (!positiv(m.pixel_breite) || !positiv(m.pixel_hoehe)) ab.push(`${d}: pixel_breite/pixel_hoehe ungültig`);
    if (m.ziel && !(anteil(m.ziel.x) && anteil(m.ziel.y))) ab.push(`${d}: ziel ausserhalb des Bildes ${JSON.stringify(m.ziel)}`);
    if (m.spalte && !(anteil(m.spalte.y) && positiv(m.spalte.h) && positiv(m.spalte.w) && m.spalte.w <= 1 && (m.spalte.y + m.spalte.h) * m.pixel_breite <= m.pixel_hoehe + 1))
      ab.push(`${d}: spalte ausserhalb des Bildes ${JSON.stringify(m.spalte)}`);
    if (m.reihe && !(m.reihe.y >= 0 && positiv(m.reihe.h) && positiv(m.reihe.breite) && (m.reihe.y + m.reihe.h) * m.pixel_breite / m.reihe.breite <= m.pixel_hoehe + 1
      && anteil(m.reihe.ziel?.x) && anteil(m.reihe.ziel?.y))) ab.push(`${d}: reihe ausserhalb des Bildes ${JSON.stringify(m.reihe)}`);
    if (m.zeilen && !m.zeilen.every((z) => anteil(z.unten) && anteil(z.x) && anteil(z.y))) ab.push(`${d}: zeilen ausserhalb des Bildes`);
  }
  return ab;
}
