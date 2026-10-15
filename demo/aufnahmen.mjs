// Aufnahmen der Oberfläche für die Landingpage gitpuls.com (09.10.2026, Ausschnitte bei 1100 px seit Runde 2). Erzeugt mit demo/demodaten.py beide Szenen in einem
// temporären Ordner, startet je Szene eine lokale Instanz (app/web.py, freier Port auf 127.0.0.1) und nimmt mit Playwright in
// Chromium auf: Englisch und Deutsch (?lang), hell und dunkel, 1440 und 430 px, deviceScaleFactor 2. Der Ausschnitt kommt je
// Aufnahme aus dem Selektor des Elements; bei 1100 px nur dunkel und nur die Einzelausschnitte (AUSSCHNITTE), die die Website in
// nahezu Originalgrösse zeigt. Der Ausschnitt kommt je Aufnahme aus dem Selektor des Elements, nie aus festen Koordinaten. Zustände eines Paars (busy und ruhig, zu und auf, die zwei
// Stufen eines Umschalters) bekommen exakt dieselbe Grösse: das kleinere Element wird unten bis zur Höhe des grösseren aufgefüllt,
// damit die Website die zwei Bilder überblenden kann. Ausgabe nach demo/aufnahmen/ (ignoriert): PNG, manifest.json, index.html.
//
//   cd demo && npm install && node aufnahmen.mjs
import { chromium } from 'playwright';
import { execFileSync, spawn } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const DEMO = dirname(fileURLToPath(import.meta.url));
const REPO = dirname(DEMO);
const AUS = join(DEMO, 'aufnahmen');
const PY = existsSync(join(REPO, '.venv/bin/python')) ? join(REPO, '.venv/bin/python') : 'python3';
const SPRACHEN = { en: 'en-US', de: 'de-DE' };   // Accept-Language, daraus die Schreibweise der Zahlen (app/format.py)
const MODI = ['hell', 'dunkel'];
const BREITEN = [1440, 430, 1100];

// Je Aufnahme ein Selektor und ihre Zustände. «wahl» klickt eine Stufe eines Umschalters, «oeffnen» klappt ein <details> auf.
const AUFNAHMEN = [
  { id: 'github', gruppe: 'GitHub-Zone ganz', sel: 'section.zone.github', zustaende: [{ szene: 'busy', zustand: 'ganz' }] },
  { id: 'kopf', gruppe: 'Kopf mit Lagesatz', sel: '.zone.github > .lage', zustaende: [{ szene: 'busy', zustand: 'lagesatz' }] },
  { id: 'heute', gruppe: 'Heute-Kachel', sel: '.tiles3 > .kpi:nth-child(1)', zustaende: [{ szene: 'busy', zustand: 'heute' }] },
  { id: 'woche', gruppe: 'Woche: 7 Tage und Woche', sel: '.tiles3 > .kpi:nth-child(2)',
    zustaende: [{ szene: 'busy', zustand: '7-tage', wahl: ['k2', '7'] }, { szene: 'busy', zustand: 'woche', wahl: ['k2', 'woche'] }] },
  { id: 'monat', gruppe: 'Monat: 30 Tage und Monat', sel: '.tiles3 > .kpi:nth-child(3)',
    zustaende: [{ szene: 'busy', zustand: '30-tage', wahl: ['k3', '30'] }, { szene: 'busy', zustand: 'monat', wahl: ['k3', 'monat'] }] },
  { id: 'handlung', gruppe: 'Handlungsbedarf busy und ruhig', sel: 'section.card.handlung',
    zustaende: [{ szene: 'busy', zustand: 'busy' }, { szene: 'ruhig', zustand: 'ruhig' }] },
  { id: 'projekte', gruppe: 'Projektliste zu und aufgeklappt', sel: 'section.card.projekte',
    zustaende: [{ szene: 'busy', zustand: 'zu' }, { szene: 'busy', zustand: 'auf', oeffnen: 'section.card.projekte details.prow' }] },
  { id: 'warnung', gruppe: 'Sicherheitswarnung zu und aufgeklappt', sel: '#hb-warn-fernwerk-studio_2f_client-portal',
    zustaende: [{ szene: 'busy', zustand: 'zu' }, { szene: 'busy', zustand: 'auf', oeffnen: '#hb-warn-fernwerk-studio_2f_client-portal details' }] },
  { id: 'server', gruppe: 'Serverzone', sel: 'section.zone.server', zustaende: [{ szene: 'busy', zustand: 'ganz' }] },
  { id: 'dienst', gruppe: 'Dienst zu und aufgeklappt', sel: 'details.dienst',
    zustaende: [{ szene: 'busy', zustand: 'zu' }, { szene: 'busy', zustand: 'auf', oeffnen: 'details.dienst' }] },
  { id: 'rueckblick', gruppe: 'Rückblick', sel: 'section.card.rueckblick', zustaende: [{ szene: 'busy', zustand: '30-tage' }] },
  // die Seite von oben am Stück, Serverzone und GitHub-Zone bis unter die Kacheln (Hero der Website, Runde 5); nur 1440 und dunkel
  { id: 'seite', gruppe: 'Seite oben bis zu den Kacheln', seite: '.tiles3', zustaende: [{ szene: 'busy', zustand: 'oben' }] },
];

// Einzelausschnitte bei 1100 px Viewport, nur dunkel (Website, Runde 2). «ziel» ist das echte Bedienelement, das zum anderen
// Zustand führt; das Manifest trägt seinen Klickpunkt als Anteil von Breite und Höhe des Ausschnitts (Zeiger auf der Website).
const AUSSCHNITTE = [
  { id: 'github', gruppe: 'GitHub-Zone ganz', sel: 'section.zone.github', zustaende: [{ szene: 'busy', zustand: 'ganz' }], hell: true },
  { id: 'server', gruppe: 'Serverzone', sel: 'section.zone.server', zustaende: [{ szene: 'busy', zustand: 'ganz' }], hell: true },
  { id: 'github-kopf', gruppe: 'GitHub-Zonenkopf', sel: 'section.zone.github > .zonenkopf', zustaende: [{ szene: 'busy', zustand: 'kopf' }] },
  { id: 'github-lage', gruppe: 'GitHub-Lagesatz mit Kennzahlen', sel: 'section.zone.github > .lage', zustaende: [{ szene: 'busy', zustand: 'lage' }] },
  { id: 'server-kopf', gruppe: 'Server-Zonenkopf', sel: 'section.zone.server > .zonenkopf', zustaende: [{ szene: 'busy', zustand: 'kopf' }] },
  { id: 'server-lage', gruppe: 'Server-Lagesatz mit Zahl', sel: 'section.zone.server .tile.lage', zustaende: [{ szene: 'busy', zustand: 'lage' }] },
  { id: 'dienste', gruppe: 'Dienstliste', sel: 'section.zone.server .tile.dienste', zeilen: 'details.dienst > summary',
    zustaende: [{ szene: 'busy', zustand: 'liste', ziel: { sel: 'details.dienst > summary', an: 'mitte' } }] },
  { id: 'dienst', gruppe: 'Dienst zu und aufgeklappt', sel: 'details.dienst',
    zustaende: [{ szene: 'busy', zustand: 'zu', ziel: { sel: 'summary', an: 'mitte' } },
                { szene: 'busy', zustand: 'auf', oeffnen: 'details.dienst', ziel: { sel: 'summary', an: 'mitte' } }] },
  { id: 'maschine', gruppe: 'Karte Maschine', sel: 'section.zone.server .fuss > .meta:nth-child(1)', zustaende: [{ szene: 'busy', zustand: 'karte' }] },
  { id: 'installiert', gruppe: 'Karte Installiert', sel: 'section.zone.server .fuss > .meta:nth-child(2)', zustaende: [{ szene: 'busy', zustand: 'karte' }] },
  { id: 'firewall', gruppe: 'Karte Firewall', sel: 'section.zone.server .fuss > .meta:nth-child(3)', zustaende: [{ szene: 'busy', zustand: 'karte' }] },
  { id: 'updates', gruppe: 'Karte Updates', sel: 'section.zone.server .fuss > .meta:nth-child(4)', zustaende: [{ szene: 'busy', zustand: 'karte' }] },
  AUFNAHMEN.find((a) => a.id === 'heute'),
  { id: 'woche', gruppe: 'Woche: 7 Tage und Woche', sel: '.tiles3 > .kpi:nth-child(2)',
    zustaende: [{ szene: 'busy', zustand: '7-tage', wahl: ['k2', '7'], ziel: { sel: 'nav[data-gruppe="k2"] button[data-fenster="woche"]', an: 'mitte' } },
                { szene: 'busy', zustand: 'woche', wahl: ['k2', 'woche'], ziel: { sel: 'nav[data-gruppe="k2"] button[data-fenster="7"]', an: 'mitte' } }] },
  { id: 'monat', gruppe: 'Monat: 30 Tage und Monat', sel: '.tiles3 > .kpi:nth-child(3)',
    zustaende: [{ szene: 'busy', zustand: '30-tage', wahl: ['k3', '30'], ziel: { sel: 'nav[data-gruppe="k3"] button[data-fenster="monat"]', an: 'mitte' } },
                { szene: 'busy', zustand: 'monat', wahl: ['k3', 'monat'], ziel: { sel: 'nav[data-gruppe="k3"] button[data-fenster="30"]', an: 'mitte' } }] },
  AUFNAHMEN.find((a) => a.id === 'handlung'),
  { id: 'warnung', gruppe: 'Sicherheitswarnung zu und aufgeklappt', sel: '#hb-warn-fernwerk-studio_2f_client-portal',
    zustaende: [{ szene: 'busy', zustand: 'zu', ziel: { sel: 'details > summary', an: 'rechts' } },
                { szene: 'busy', zustand: 'auf', oeffnen: '#hb-warn-fernwerk-studio_2f_client-portal details', ziel: { sel: 'details > summary', an: 'rechts' } }] },
  // Handlungsbedarf zu und mit aufgeklappter Sicherheitswarnung (Website, Entwurf 9): «reihe» misst die erste Reihe des Rasters
  // (Arten nebeneinander bis zur Unterkante der höchsten), die Website schneidet sie aus und lässt sie wie in der App wachsen
  { id: 'handlung-klapp', gruppe: 'Handlungsbedarf mit Warnung zu und aufgeklappt', sel: 'section.card.handlung', reihe: true,
    zustaende: [{ szene: 'busy', zustand: 'zu', ziel: { sel: '#hb-warn-fernwerk-studio_2f_client-portal summary', an: 'rechts' } },
                { szene: 'busy', zustand: 'auf', oeffnen: '#hb-warn-fernwerk-studio_2f_client-portal details', ziel: { sel: '#hb-warn-fernwerk-studio_2f_client-portal summary', an: 'rechts' } }] },
  AUFNAHMEN.find((a) => a.id === 'rueckblick'),
  { id: 'projektzeile', gruppe: 'Projektzeile zu und aufgeklappt', sel: 'section.card.projekte details.prow',
    zustaende: [{ szene: 'busy', zustand: 'zu', ziel: { sel: 'summary', an: 'rechts' } },
                { szene: 'busy', zustand: 'auf', oeffnen: 'section.card.projekte details.prow', ziel: { sel: 'summary', an: 'rechts' } }] },
  { id: 'projektliste', gruppe: 'Projektliste', sel: 'section.card.projekte', zustaende: [{ szene: 'busy', zustand: 'liste' }] },
  { id: 'sterne', gruppe: 'Sterne-Rangliste', sel: 'section.card.fremde', zustaende: [{ szene: 'busy', zustand: 'rangliste' }] },
];
// derselbe Block auch bei 430 px, einspaltig wie in der App unter 820 px (Website unter 760 px)
AUFNAHMEN.push(AUSSCHNITTE.find((a) => a.id === 'handlung-klapp'));

const warte = (ms) => new Promise((r) => setTimeout(r, ms));
const freierPort = () => new Promise((r) => { const s = createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => r(p)); }); });

async function starten(ordner) {
  const env = { ...process.env, ...JSON.parse(readFileSync(join(ordner, 'umgebung.json'), 'utf8')), GITPULS_SPRACHE: 'en' };
  delete env.GITPULS_FORMAT;   // die Schreibweise folgt Accept-Language der Aufnahme, nicht der Shell
  delete env.GITPULS_TAG;
  // Die Aufnahmeinstanz (10.10.2026, Audit B3) lauscht nur auf 127.0.0.1, sieht kein Token und startet keinen Takt: sie zeigt Demodaten
  // und darf weder im Netz stehen noch GitHub abfragen.
  delete env.GITHUB_TOKEN;
  delete env.GITPULS_GITHUB_ENV;
  env.GITPULS_BIND = '127.0.0.1';
  env.GITPULS_TAKT_MIN = '0';
  env.GITPULS_PORT = String(await freierPort());
  const prozess = spawn(PY, ['-m', 'app.web'], { cwd: REPO, env, stdio: ['ignore', 'ignore', 'inherit'] });
  const adresse = `http://127.0.0.1:${env.GITPULS_PORT}`;
  for (let i = 0; i < 100; i++) {
    try {
      const r = await fetch(`${adresse}/gesund`);   // 200 erst, wenn jeder Block der Seite gezeichnet wurde
      if (r.ok) return { adresse, prozess };
      if (r.status === 503) throw new Error(`${ordner}: ${await r.text()}`);
    } catch (e) { if (String(e.message).includes('blockfehler')) { prozess.kill(); throw e; } }
    await warte(100);
  }
  prozess.kill();
  throw new Error(`Instanz auf ${ordner} antwortet nicht`);
}

function pngMasse(buffer) {   // IHDR: Breite und Höhe ab Byte 16
  return { breite: buffer.readUInt32BE(16), hoehe: buffer.readUInt32BE(20) };
}

async function laden(page, instanz, sprache, z, sel) {
  await page.goto(`${instanz.adresse}/?lang=${sprache}`, { waitUntil: 'load' });
  await page.evaluate(() => document.fonts.ready);
  if (z.wahl) await page.locator(`nav[data-gruppe="${z.wahl[0]}"] button[data-fenster="${z.wahl[1]}"]`).locator('visible=true').first().click();
  if (z.oeffnen) await page.locator(z.oeffnen).first().evaluate((d) => { d.open = true; });
  const el = page.locator(sel).first();
  await el.waitFor({ state: 'visible' });
  return el;
}

const daten = mkdtempSync(join(tmpdir(), 'gitpuls-demo-'));
const instanzen = {};
const browser = await chromium.launch();
try {
  for (const szene of ['busy', 'ruhig']) {
    execFileSync(PY, [join(DEMO, 'demodaten.py'), join(daten, szene), '--szene', szene], { stdio: 'inherit' });
    instanzen[szene] = await starten(join(daten, szene));
  }
  mkdirSync(AUS, { recursive: true });
  for (const f of readdirSync(AUS)) if (f.endsWith('.png')) rmSync(join(AUS, f));
  const manifest = [];
  for (const [sprache, locale] of Object.entries(SPRACHEN)) {
    for (const modus of MODI) {
      for (const breite of BREITEN) {
        const ctx = await browser.newContext({ viewport: { width: breite, height: 900 }, deviceScaleFactor: 2, locale, reducedMotion: 'reduce',
                                               colorScheme: modus === 'dunkel' ? 'dark' : 'light' });
        // Jeder Aufruf beginnt mit den Vorgaben: gemerkte Wahl der Umschalter weg, nur der Modus gesetzt.
        await ctx.addInitScript((m) => { try { localStorage.clear(); localStorage.setItem('gitpuls-modus', m); } catch (e) {} }, modus);
        const page = await ctx.newPage();
        // bei 1100 px im Modus hell nur die ganzen Zonen (Vergleich hell und dunkel auf der Website)
        for (const a of (breite === 1100 ? AUSSCHNITTE : AUFNAHMEN).filter((a) => breite !== 1100 || modus === 'dunkel' || a.hell)) {
          if (a.seite) {
            if (breite !== 1440 || modus !== 'dunkel') continue;
            const z = a.zustaende[0];
            await laden(page, instanzen[z.szene], sprache, z, a.seite);
            const unten = await page.locator(a.seite).first().evaluate((e) => e.getBoundingClientRect().bottom + scrollY + 24);
            const datei = `${a.id}--${z.zustand}--${sprache}-${modus}-${breite}.png`;
            const px = pngMasse(await page.screenshot({ path: join(AUS, datei), clip: { x: 0, y: 0, width: breite, height: Math.ceil(unten) }, fullPage: true, animations: 'disabled' }));
            manifest.push({ id: a.id, gruppe: a.gruppe, szene: z.szene, zustand: z.zustand, sprache, modus, breite, datei, pixel_breite: px.breite, pixel_hoehe: px.hoehe, paar: false });
            continue;
          }
          // Erst die Höhe jedes Zustands, dann alle auf die grösste aufgefüllt aufnehmen.
          let hoehe = 0;
          for (const z of a.zustaende) hoehe = Math.max(hoehe, (await (await laden(page, instanzen[z.szene], sprache, z, a.sel)).boundingBox()).height);
          for (const z of a.zustaende) {
            const el = await laden(page, instanzen[z.szene], sprache, z, a.sel);
            await el.evaluate((e, h) => {
              const ist = e.getBoundingClientRect().height;
              if (ist < h) e.style.paddingBottom = `${parseFloat(getComputedStyle(e).paddingBottom) + h - ist}px`;
            }, hoehe);
            const datei = `${a.id}--${z.zustand}--${sprache}-${modus}-${breite}.png`;
            // Element mit eigener Fläche (Karte, Zone): alles dahinter transparent (Seite und umgebende Zone), sonst stünde deren
            // Grund in den Ecken ausserhalb der Rundung und als zweite Kante um die Karte auf der Website
            const eigen = await el.evaluate((e) => {
              const ja = getComputedStyle(e).backgroundColor !== 'rgba(0, 0, 0, 0)';
              if (ja) for (let x = e.parentElement; x; x = x.parentElement) x.style.setProperty('background', 'transparent', 'important');
              return ja;
            });
            const px = pngMasse(await el.screenshot({ path: join(AUS, datei), animations: 'disabled', omitBackground: eigen }));
            // Klickpunkt: Mitte des Elements oder 14 px vor seinem rechten Rand (dort sitzt der Aufklapper)
            const ziel = z.ziel ? await el.evaluate((e, { sel, an }) => {
              const t = e.matches(sel) ? e : e.querySelector(sel);
              if (!t) return null;
              const a = e.getBoundingClientRect(), b = t.getBoundingClientRect();
              const x = an === 'rechts' ? b.right - 14 : b.left + b.width / 2;
              return { x: +((x - a.left) / a.width).toFixed(4), y: +((b.top + b.height / 2 - a.top) / a.height).toFixed(4) };
            }, z.ziel) : undefined;
            if (z.ziel && !ziel) throw new Error(`${datei}: Ziel ${z.ziel.sel} nicht gefunden`);
            // erste Reihe des Rasters in CSS-px ab Oberkante des Elements; das Ziel dann als Anteil der Reihe, nicht des Elements
            const reihe = a.reihe ? await el.evaluate((e, sel) => {
              const s = e.getBoundingClientRect(), zellen = [...e.querySelectorAll(':scope > .hraster > div')].map((d) => d.getBoundingClientRect());
              const oben = zellen[0].top, unten = Math.max(...zellen.filter((r) => Math.abs(r.top - oben) < 1).map((r) => r.bottom));
              const b = e.querySelector(sel).getBoundingClientRect();
              return { y: +(oben - s.top).toFixed(2), h: +(unten - oben).toFixed(2), breite: +s.width.toFixed(2),
                       ziel: { x: +((b.right - 14 - s.left) / s.width).toFixed(4), y: +((b.top + b.height / 2 - oben) / (unten - oben)).toFixed(4) } };
            }, z.ziel.sel) : undefined;
            // Zeilen (Dienstliste): Unterkante jeder Zeile und Mitte ihres Statuspunkts als Anteil des Ausschnitts
            const zeilen = a.zeilen ? await el.evaluate((e, sel) => {
              const a = e.getBoundingClientRect();
              return [...e.querySelectorAll(sel)].map((s) => {
                const b = s.getBoundingClientRect(), p = s.querySelector('.punkt').getBoundingClientRect();
                return { unten: +((b.bottom - a.top) / a.height).toFixed(4), x: +((p.left + p.width / 2 - a.left) / a.width).toFixed(4), y: +((p.top + p.height / 2 - a.top) / a.height).toFixed(4) };
              });
            }, a.zeilen) : undefined;
            manifest.push({ id: a.id, gruppe: a.gruppe, szene: z.szene, zustand: z.zustand, sprache, modus, breite, datei,
                            pixel_breite: px.breite, pixel_hoehe: px.hoehe, paar: a.zustaende.length > 1, ...(ziel && { ziel }), ...(zeilen && { zeilen }),
                            ...(reihe && { reihe }) });
          }
        }
        await ctx.close();
      }
    }
  }
  await readmeAufnahmen(browser, instanzen.busy);
  // Selbstprüfung: die Zustände eines Paars haben je Sprache, Modus und Breite dieselben Pixelmasse.
  const ab = [];
  const paare = Map.groupBy(manifest.filter((m) => m.paar), (m) => `${m.id} ${m.sprache} ${m.modus} ${m.breite}`);
  for (const [k, v] of paare) if (new Set(v.map((m) => `${m.pixel_breite}x${m.pixel_hoehe}`)).size > 1) ab.push(`${k}: ${v.map((m) => `${m.zustand} ${m.pixel_breite}x${m.pixel_hoehe}`).join(', ')}`);
  writeFileSync(join(AUS, 'manifest.json'), JSON.stringify(manifest, null, 1));
  writeFileSync(join(AUS, 'index.html'), kontaktbogen(manifest));
  console.log(`${manifest.length} Aufnahmen, ${paare.size} Paare, nach ${AUS}`);
  if (ab.length) { console.error(`Paare mit ungleichen Massen:\n${ab.join('\n')}`); process.exitCode = 1; }
} finally {
  await browser.close();
  for (const i of Object.values(instanzen)) i.prozess.kill();
  rmSync(daten, { recursive: true, force: true });
}

// Bilder für das README (11.10.2026): englisch, Szene busy, 1280 px Viewport, jedes auf denselben waagrechten Ausschnitt (main) zugeschnitten,
// damit alle dieselbe Breite haben. Handy und «hell und dunkel» werden im Browser aus echten Aufnahmen zusammengesetzt. Ausgabe nach
// demo/aufnahmen/readme/ als PNG in voller Qualität; verkleinert und nach docs/screenshots/ übernommen werden sie von Hand (README, Development).
async function readmeAufnahmen(browser, instanz) {
  const RM = join(AUS, 'readme'), B = 1280, DSF = 1.25, RAND = 16;   // ab 1280 px passt die Rangliste samt Spalte Alter
  rmSync(RM, { recursive: true, force: true });
  mkdirSync(RM, { recursive: true });
  const kontext = (modus, mehr = {}) => browser.newContext({ viewport: { width: B, height: 900 }, deviceScaleFactor: DSF, locale: 'en-US',
    reducedMotion: 'reduce', colorScheme: modus === 'dunkel' ? 'dark' : 'light', ...mehr }).then(async (ctx) => {
    await ctx.addInitScript((m) => { try { localStorage.clear(); localStorage.setItem('gitpuls-modus', m); } catch (e) {} }, modus);
    return ctx;
  });
  // Ein Bild von oben (erstes Element) bis unten (letztes Element), waagrecht immer main samt RAND
  const bild = async (page, datei, oben, unten = oben, vorher) => {
    await page.goto(`${instanz.adresse}/?lang=en`, { waitUntil: 'load' });
    await page.evaluate(() => document.fonts.ready);
    if (vorher) await vorher(page);
    const r = await page.evaluate(([o, u]) => {
      const m = document.querySelector('main').getBoundingClientRect(), a = o ? document.querySelector(o).getBoundingClientRect() : { top: -scrollY };
      const b = document.querySelector(u).getBoundingClientRect();
      return { x: m.left, w: m.width, y: a.top + scrollY, h: b.bottom - a.top };
    }, [oben, unten]);
    r.x -= RAND; r.w += 2 * RAND;   // derselbe Rand links und rechts, die Karten kleben sonst am Bildrand
    const y = Math.max(0, r.y - (oben ? RAND : 0));
    await page.screenshot({ path: join(RM, datei), clip: { x: r.x, y, width: r.w, height: r.y + r.h + RAND - y }, fullPage: true, animations: 'disabled' });
    return r.w;
  };
  let breite = 0;
  for (const modus of ['dunkel', 'hell']) {
    const ctx = await kontext(modus), page = await ctx.newPage();
    // GitHub-Zone bis unter die Kacheln: Kopf, Lagesatz mit Ring und Handlungsbedarf, drei Kacheln (Hero des README)
    breite = await bild(page, `github-${modus}.png`, 'section.zone.github', 'section.zone.github',
      (p) => p.addStyleTag({ content: 'section.zone.github > :not(.zonenkopf, .lage, .tiles3) { display: none }' }));
    await bild(page, `server-${modus}.png`, 'section.zone.server', 'section.zone.server > .zwei');
    // die ganze Serverzone am Stück (Kopf, Lagesatz, Dienste, die vier Kästen) und die ganze Seite bis zur Fusszeile (README, 15.10.2026)
    await bild(page, `serverzone-${modus}.png`, 'section.zone.server');
    await bild(page, `seite-${modus}.png`, null, 'footer.fusszeile');
    if (modus === 'dunkel') {
      await bild(page, 'maschine.png', 'section.zone.server .fuss > .meta:nth-child(1)', 'section.zone.server .fuss > .meta:nth-child(2)');
      await bild(page, 'firewall.png', 'section.zone.server .fuss > .meta:nth-child(3)', 'section.zone.server .fuss > .meta:nth-child(4)');
      await bild(page, 'handlung.png', 'section.card.handlung');
      // Kacheln im zweiten Zustand der Umschalter (Kachel 2 Woche, Kachel 3 Monat); der erste Zustand steht schon im Hero
      await bild(page, 'kacheln.png', '.tiles3', '.tiles3', async (p) => {
        for (const [g, f] of [['k2', 'woche'], ['k3', 'monat']]) await p.locator(`nav[data-gruppe="${g}"] button[data-fenster="${f}"]`).locator('visible=true').first().click();
      });
      await bild(page, 'projekte.png', 'section.card.projekte', 'section.card.projekte', (p) => p.locator('section.card.projekte details.prow').first().evaluate((d) => { d.open = true; }));
      await bild(page, 'rueckblick.png', 'section.card.rueckblick');
      await bild(page, 'sterne.png', 'section.card.fremde');
    }
    await ctx.close();
  }
  // Handy: drei Bildschirme bei 430 px nebeneinander (Server, Kacheln, Handlungsbedarf), zusammengesetzt in derselben Breite
  const handy = await kontext('dunkel', { viewport: { width: 430, height: 880 }, deviceScaleFactor: 2 }), hp = await handy.newPage();
  const schirme = [];
  for (const sel of ['section.zone.server', '.tiles3', 'section.card.handlung']) {
    await hp.goto(`${instanz.adresse}/?lang=en`, { waitUntil: 'load' });
    await hp.evaluate((s) => scrollTo(0, document.querySelector(s).getBoundingClientRect().top + scrollY - 12), sel);
    schirme.push((await hp.screenshot({ animations: 'disabled' })).toString('base64'));
  }
  await handy.close();
  const setzen = async (datei, html, modus = 'dunkel') => {
    const ctx = await kontext(modus), page = await ctx.newPage();
    await page.setContent(`<!doctype html><body style="margin:0;background:transparent"><div id="b" style="width:${breite}px">${html}</div></body>`);
    await page.evaluate(() => Promise.all([...document.images].map((i) => i.decode())));
    await page.locator('#b').screenshot({ path: join(RM, datei), omitBackground: true });
    await ctx.close();
  };
  await setzen('handy.png', `<div style="display:flex;gap:24px;justify-content:center">${schirme.map((s) =>
    `<img src="data:image/png;base64,${s}" style="width:${(breite - 48) / 3}px;border-radius:18px;border:1px solid #2a2f36">`).join('')}</div>`);
  // hell und dunkel: dieselbe Serverzone, links dunkel, rechts hell
  const b64 = (f) => readFileSync(join(RM, f)).toString('base64');
  await setzen('hell-dunkel.png', `<div style="position:relative"><img src="data:image/png;base64,${b64('server-dunkel.png')}" style="width:100%;display:block">`
    + `<img src="data:image/png;base64,${b64('server-hell.png')}" style="width:100%;display:block;position:absolute;inset:0;clip-path:inset(0 0 0 50%)">`
    + `<div style="position:absolute;top:0;bottom:0;left:50%;width:2px;background:#9cbcde"></div></div>`);
  // Hero (README): die GitHub-Zone auf einer Fläche, die sich auf GitHub dunkel (#0d1117) und hell (#ffffff) abhebt, Werte aus den Tokens von
  // gitpuls-web: dunkel --card mit Haarlinie --smoke, hell --accent-rgb zu 16 % auf --paper (deckend, sonst fällt die Fläche der Palette zum Opfer) mit Haarlinie --ink-2; die Ecken ausserhalb bleiben transparent
  for (const [modus, flaeche, linie] of [['dunkel', '#141414', '#383b3f'], ['hell', '#eff4fa', '#d0d6e0']])
    await setzen(`hero-${modus}.png`, `<div style="background:${flaeche};border:1px solid ${linie};border-radius:16px;padding:48px">`
      + `<img src="data:image/png;base64,${b64(`github-${modus}.png`)}" style="width:100%;display:block;border-radius:12px"></div>`, modus);
  console.log(`README: ${readdirSync(RM).length} Dateien nach ${RM}, Breite ${breite} CSS-px`);
}

function kontaktbogen(manifest) {
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  const gruppen = Map.groupBy(manifest, (m) => m.gruppe);
  const teile = [...gruppen].map(([gruppe, liste]) => `<section><h2>${esc(gruppe)} <small>${liste.length}</small></h2><div class="raster">${liste.map((m) =>
    `<figure><a href="${esc(m.datei)}"><img src="${esc(m.datei)}" loading="lazy" alt="${esc(`${m.id} ${m.zustand}`)}"></a>`
    + `<figcaption><b>${esc(m.zustand)}</b> · ${esc(m.szene)} · ${m.sprache} · ${m.modus} · ${m.breite} px<br><span>${m.pixel_breite} × ${m.pixel_hoehe} px</span></figcaption></figure>`).join('')}</div></section>`);
  return `<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>gitpuls Aufnahmen</title><style>
:root { --bg: #f6f7f9; --karte: #fff; --text: #16191d; --grau: #66707a; --linie: #dde1e6; color-scheme: light dark; }
@media (prefers-color-scheme: dark) { :root { --bg: #0e1013; --karte: #171a1f; --text: #e8eaed; --grau: #9aa3ad; --linie: #2a2f36; } }
body { margin: 0; padding: 24px 16px 64px; background: var(--bg); color: var(--text); font: 14px/1.45 system-ui, sans-serif; }
h1 { font-size: 20px; margin: 0 0 4px; } p.sub { color: var(--grau); margin: 0 0 24px; }
h2 { font-size: 16px; margin: 32px 0 12px; } h2 small { color: var(--grau); font-weight: 400; }
.raster { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; align-items: start; }
figure { margin: 0; background: var(--karte); border: 1px solid var(--linie); border-radius: 8px; padding: 8px; }
img { display: block; width: 100%; height: auto; max-height: 420px; object-fit: contain; object-position: top; background: repeating-conic-gradient(var(--linie) 0 25%, transparent 0 50%) 0 0 / 12px 12px; }
figcaption { margin-top: 6px; font-size: 12px; color: var(--grau); } figcaption b { color: var(--text); } figcaption span { font-variant-numeric: tabular-nums; }
</style></head><body><h1>gitpuls · Aufnahmen</h1><p class="sub">${manifest.length} Aufnahmen aus dem Demodatensatz, Uhr fest auf Donnerstag, 15.10.2026, 22:40 (Europe/Zurich). Daten in manifest.json.</p>
${teile.join('\n')}</body></html>`;
}
