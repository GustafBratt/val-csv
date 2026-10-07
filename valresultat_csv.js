/*
Skapar csv-filer med valresultatet från valmyndighetens json-filer.

Gör samma sak som valresultat_csv.py, för den som hellre kör Node.js än
Python. Inga npm-paket behövs. Se valresultat_csv.py för en utförligare
beskrivning av träden och filnamnen; koden här följer den funktion för
funktion, med samma namn i camelCase.

Sverige betraktas som tre träd, ett per valtyp. Riket är roten och
valdistrikten är löven. Nivåerna inom parentes finns inte överallt i trädet
(se vagarRd, vagarRf och vagarKf):

    RD: riket, riksdagsvalkrets, kommun, (kommunvalkrets), valdistrikt
    RF: riket, region, (regionvalkrets), (kommun), (kommunvalkrets), valdistrikt
    KF: riket, län, kommun, (kommunvalkrets), valdistrikt

Varje nod får en csv-fil, t ex output-csv/S/RD_10_1080_S.csv, med nodens
röster följt av rösterna i vart och ett av valdistrikten under noden.
Rösterna hämtas från nodens egen summering i json-filerna.

Användning:
    node valresultat_csv.js
*/

"use strict";

const fs = require("fs");
const path = require("path");

const INPUT_DIR = "input-json";
const OUTPUT_DIR = "output-csv";

const RAKNINGAR = ["P", "S"]; // Preliminär och slutlig. Indatat ligger i input-json/p och /s.

const RIKET_KOD = "00";

// De tre typerna av ogiltiga röster, som i csv-filen redovisas som partier.
const BLANKA = "Blanka";
const EJ_ANMALDA = "Ej anmälda";
const OVRIGA_OGILTIGA = "övriga ogiltiga";

// ---------------------------------------------------------------------------
// Csv-formatet. Allt som rör filernas innehåll och format finns här.
// ---------------------------------------------------------------------------

const CSV_BOM = "﻿"; // Så att Excel läser å, ä och ö rätt.
const CSV_DELIMITER = ";";
const CSV_LINE_END = "\r\n";
const CSV_HEADER = ["Nod-kod", "områdesnamn", "parti", "antal_röster"];

function fileName(valtyp, node, rakning) {
  const koder = node.kodvag().length > 0 ? node.kodvag() : [RIKET_KOD];
  return [valtyp, ...koder, rakning].join("_") + ".csv";
}

/** Raderna i nodens csv-fil: först noden själv, sedan löven under den. */
function* csvRows(node) {
  for (const area of [node, ...node.leaves()]) {
    for (const [parti, antal] of area.roster) {
      yield [area.kod, area.namn, parti, antal];
    }
  }
}

/** Ett fält i csv-filen, inom citattecken bara om det behövs. */
function csvField(value) {
  const text = String(value);
  if (/[;"\r\n]/.test(text)) {
    return '"' + text.replaceAll('"', '""') + '"';
  }
  return text;
}

function writeCsv(filePath, node) {
  const lines = [CSV_HEADER, ...csvRows(node)].map((row) => row.map(csvField).join(CSV_DELIMITER));
  fs.writeFileSync(filePath, CSV_BOM + lines.join(CSV_LINE_END) + CSV_LINE_END, "utf8");
}

// ---------------------------------------------------------------------------
// Trädet
// ---------------------------------------------------------------------------

class Node {
  constructor(niva, kod, summering, parent = null) {
    this.niva = niva; // t ex "kommun"
    this.kod = kod;
    this.namn = summering.namn;
    this.roster = summering.roster; // [[parti eller typ av ogiltig röst, antal röster]]
    this.parent = parent;
    this.children = new Map(); // kod -> Node
  }

  /** Koderna på vägen från riket ner till noden. Riket självt är inte med. */
  kodvag() {
    if (this.parent === null) {
      return [];
    }
    return [...this.parent.kodvag(), this.kod];
  }

  isLeaf() {
    return this.children.size === 0;
  }

  /** Alla löv under noden. Ett löv har inga löv under sig. */
  *leaves() {
    for (const child of this.children.values()) {
      if (child.isLeaf()) {
        yield child;
      } else {
        yield* child.leaves();
      }
    }
  }

  *allNodes() {
    yield this;
    for (const child of this.children.values()) {
      yield* child.allNodes();
    }
  }
}

/** Nyckeln för en nod i summeringar: nivån och koden. */
function nyckel(niva, kod) {
  return `${niva}|${kod}`;
}

/**
 * Bygger ett träd av vägarna från riket till varje valdistrikt.
 *
 * En väg är en lista av [nivå, kod], utan riket. Summeringar har namnet och
 * rösterna för varje nod, med nyckel(nivå, kod) som nyckel.
 */
function buildTree(vagar, summeringar) {
  const riket = new Node("riket", RIKET_KOD, summeringar.get(nyckel("riket", RIKET_KOD)));
  for (const vag of vagar) {
    let node = riket;
    for (const [niva, kod] of vag) {
      if (!node.children.has(kod)) {
        node.children.set(kod, new Node(niva, kod, summeringar.get(nyckel(niva, kod)), node));
      }
      node = node.children.get(kod);
    }
  }
  return riket;
}

// ---------------------------------------------------------------------------
// Vägarna genom de tre träden, en väg från riket till varje valdistrikt
//
// Valkretsnivåerna finns bara där kommunen eller regionen är indelad i
// valkretsar, och bara då har valkretsen en egen summering. Valdistrikten har
// ändå alltid en valkretskod, som då slutar på 00 (t ex 011400 för Upplands
// Väsby). Den valkretsen tas bara med i vägen om den har en summering.
// ---------------------------------------------------------------------------

function kommunvalkretsOchValdistrikt(vd, summeringar) {
  const vag = [];
  if (summeringar.has(nyckel("kommunvalkrets", vd.kommunvalkretsKod))) {
    vag.push(["kommunvalkrets", vd.kommunvalkretsKod]);
  }
  vag.push(["valdistrikt", vd.valdistriktskod]);
  return vag;
}

function kommunOchNedat(vd, summeringar) {
  return [["kommun", vd.kommunkod], ...kommunvalkretsOchValdistrikt(vd, summeringar)];
}

function vagarRd(valdistrikt, summeringar) {
  return valdistrikt.map((vd) => [["riksdagsvalkrets", vd.kretskod], ...kommunOchNedat(vd, summeringar)]);
}

/**
 * Vägarna i RF-trädet.
 *
 * En kommun som är delad mellan flera regionvalkretsar ryms inte i trädet:
 * den skulle behöva ligga både över och under regionvalkretsarna. Där hoppas
 * kommunen över, och kommunvalkretsarna ligger direkt under regionvalkretsen.
 */
function vagarRf(valdistrikt, summeringar) {
  const deladeKommuner = kommunerIFleraRegionvalkretsar(valdistrikt);
  return valdistrikt.map((vd) => {
    const vag = [["region", vd.valomradeskod]];
    if (summeringar.has(nyckel("regionvalkrets", vd.kretskod))) {
      vag.push(["regionvalkrets", vd.kretskod]);
    }
    if (deladeKommuner.has(vd.kommunkod)) {
      vag.push(...kommunvalkretsOchValdistrikt(vd, summeringar));
    } else {
      vag.push(...kommunOchNedat(vd, summeringar));
    }
    return vag;
  });
}

/**
 * Kommunerna vars valdistrikt ligger i mer än en regionvalkrets.
 *
 * I valet 2026 gäller det bara Stockholms stad, delad mellan regionvalkretsarna
 * 0101-0106, där varje regionvalkrets är en av kommunens kommunvalkretsar.
 */
function kommunerIFleraRegionvalkretsar(valdistrikt) {
  const regionvalkretsar = new Map(); // kommunkod -> Set av regionvalkretskoder
  for (const vd of valdistrikt) {
    if (!regionvalkretsar.has(vd.kommunkod)) {
      regionvalkretsar.set(vd.kommunkod, new Set());
    }
    regionvalkretsar.get(vd.kommunkod).add(vd.kretskod);
  }
  const delade = new Set();
  for (const [kommun, kretsar] of regionvalkretsar) {
    if (kretsar.size > 1) {
      delade.add(kommun);
    }
  }
  return delade;
}

function vagarKf(valdistrikt, summeringar) {
  return valdistrikt.map((vd) => [["län", vd.lankod], ...kommunOchNedat(vd, summeringar)]);
}

// ---------------------------------------------------------------------------
// Inläsning av json-filerna
//
// Varje zip-fil från valmyndigheten är uppackad i en egen katalog, t ex
// input-json/s/rf/Val_2026_slutlig_01_RF/. Den överordnade summeringen för
// hela landet ligger i katalogen som slutar på _OS_RF (finns för RF och KF).
// ---------------------------------------------------------------------------

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

/** Namnen i katalogen som matchar mönstret, i bokstavsordning. */
function matching(katalog, monster) {
  return fs
    .readdirSync(katalog)
    .filter((namn) => monster.test(namn))
    .sort()
    .map((namn) => path.join(katalog, namn));
}

/** Det enda namnet i katalogen som matchar mönstret. */
function single(katalog, monster) {
  const traffar = matching(katalog, monster);
  if (traffar.length !== 1) {
    throw new Error(`Väntade en träff för ${monster} i ${katalog}, fick ${traffar.length}`);
  }
  return traffar[0];
}

function valtypDir(rakning, valtyp) {
  return path.join(INPUT_DIR, rakning.toLowerCase(), valtyp.toLowerCase());
}

/** Katalogerna för valtypens valområden: riket (RD), regionerna (RF) eller kommunerna (KF). */
function zipDirs(rakning, valtyp) {
  return matching(valtypDir(rakning, valtyp), new RegExp(`^Val_.*_${valtyp}$`)).filter(
    (d) => !path.basename(d).includes("_OS_") && fs.statSync(d).isDirectory()
  );
}

/** Katalogen med den överordnade summeringen för hela landet. */
function osDir(rakning, valtyp) {
  return single(valtypDir(rakning, valtyp), new RegExp(`^Val_.*_OS_${valtyp}$`));
}

/** Läser katalogens fil av en viss typ, t ex "summering" eller "mandatfordelning". */
function readFrom(katalog, filtyp) {
  return readJson(single(katalog, new RegExp(`^Val_.*_${filtyp}.*\\.json$`)));
}

/** Partiernas röster i samma ordning som i json-filen, sedan de ogiltiga rösterna. */
function rosterFrom(rostfordelning) {
  const giltiga = rostfordelning.rosterPaverkaMandat;
  const ogiltiga = rostfordelning.rosterEjPaverkaMandat;
  const roster = giltiga.partiRoster.map((parti) => [parti.partibeteckning, parti.antalRoster]);
  roster.push([BLANKA, ogiltiga.blankaRoster.antalRoster]);
  roster.push([EJ_ANMALDA, ogiltiga.rosterEjAnmaltDeltagande.antalRoster]);
  roster.push([OVRIGA_OGILTIGA, ogiltiga.ovrigaOgiltiga.antalRoster]);
  return roster;
}

/** Namnet och rösterna för en nod, som de står i json-filerna. */
function summeringOf(namn, jsonObjekt) {
  return { namn, roster: rosterFrom(jsonObjekt.rostfordelning) };
}

/** Lägger till ett valområde (från en mandatfördelningsfil) och dess valkretsar. */
function addValomrade(summeringar, niva, valkretsniva, valomrade) {
  summeringar.set(nyckel(niva, valomrade.kod), summeringOf(valomrade.namn, valomrade));
  for (const krets of valomrade.valkretsLista ?? []) {
    summeringar.set(nyckel(valkretsniva, krets.kod), summeringOf(krets.namnValkrets, krets));
  }
}

/** Lägger till kommunerna och kommunvalkretsarna i en RD- eller RF-summeringsfil. */
function addKommuner(summeringar, summeringsfil) {
  for (const kommun of summeringsfil.kommuner) {
    summeringar.set(nyckel("kommun", kommun.kommunkod), summeringOf(kommun.namn, kommun));
    for (const krets of kommun.kommunvalkretsar ?? []) {
      summeringar.set(nyckel("kommunvalkrets", krets.kod), summeringOf(krets.namn, krets));
    }
  }
}

function readSummeringarRd(rakning) {
  const summeringar = new Map();
  for (const katalog of zipDirs(rakning, "RD")) {
    // Bara en katalog, för hela riket.
    addValomrade(summeringar, "riket", "riksdagsvalkrets", readFrom(katalog, "mandatfordelning").valomrade);
    addKommuner(summeringar, readFrom(katalog, "summering"));
  }
  return summeringar;
}

function readSummeringarRf(rakning) {
  const helaLandet = readFrom(osDir(rakning, "RF"), "summering").helaLandet;
  const summeringar = new Map([[nyckel("riket", RIKET_KOD), summeringOf(helaLandet.namn, helaLandet)]]);
  for (const katalog of zipDirs(rakning, "RF")) {
    // En katalog per region.
    addValomrade(summeringar, "region", "regionvalkrets", readFrom(katalog, "mandatfordelning").valomrade);
    addKommuner(summeringar, readFrom(katalog, "summering"));
  }
  return summeringar;
}

function readSummeringarKf(rakning) {
  const helaLandet = readFrom(osDir(rakning, "KF"), "summering").helaLandet;
  const summeringar = new Map([[nyckel("riket", RIKET_KOD), summeringOf(helaLandet.namn, helaLandet)]]);
  for (const lan of helaLandet.lan) {
    summeringar.set(nyckel("län", lan.lankod), summeringOf(lan.namn, lan));
    for (const kommun of lan.kommuner) {
      summeringar.set(nyckel("kommun", kommun.kommunkod), summeringOf(kommun.namn, kommun));
    }
  }
  for (const katalog of zipDirs(rakning, "KF")) {
    // En katalog per kommun.
    for (const krets of readFrom(katalog, "mandatfordelning").valomrade.valkretsLista ?? []) {
      summeringar.set(nyckel("kommunvalkrets", krets.kod), summeringOf(krets.namnValkrets, krets));
    }
  }
  return summeringar;
}

function readValdistrikt(rakning, valtyp) {
  return zipDirs(rakning, valtyp).flatMap((katalog) => readFrom(katalog, "rostfordelning").valdistrikt);
}

const TRAD = {
  RD: [readSummeringarRd, vagarRd],
  RF: [readSummeringarRf, vagarRf],
  KF: [readSummeringarKf, vagarKf],
};

// ---------------------------------------------------------------------------

function main() {
  for (const rakning of RAKNINGAR) {
    const utkatalog = path.join(OUTPUT_DIR, rakning);
    fs.rmSync(utkatalog, { recursive: true, force: true }); // Inga filer kvar från tidigare körningar.
    fs.mkdirSync(utkatalog, { recursive: true });
    for (const [valtyp, [readSummeringar, vagarFor]] of Object.entries(TRAD)) {
      const summeringar = readSummeringar(rakning);
      const valdistrikt = readValdistrikt(rakning, valtyp);
      for (const vd of valdistrikt) {
        summeringar.set(nyckel("valdistrikt", vd.valdistriktskod), summeringOf(vd.namn, vd));
      }
      const vagar = vagarFor(valdistrikt, summeringar);
      const riket = buildTree(vagar, summeringar);
      let antal = 0;
      for (const node of riket.allNodes()) {
        writeCsv(path.join(utkatalog, fileName(valtyp, node, rakning)), node);
        antal += 1;
      }
      console.log(`${rakning} ${valtyp}: ${antal} filer`);
    }
  }
}

main();
