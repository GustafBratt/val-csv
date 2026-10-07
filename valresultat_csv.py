"""Skapar csv-filer med valresultatet från valmyndighetens json-filer.

Sverige betraktas som tre träd, ett per valtyp. Riket är roten och
valdistrikten är löven. Nivåerna inom parentes finns inte överallt i trädet
(se vagar_rd, vagar_rf och vagar_kf):

    RD: riket, riksdagsvalkrets, kommun, (kommunvalkrets), valdistrikt
    RF: riket, region, (regionvalkrets), (kommun), (kommunvalkrets), valdistrikt
    KF: riket, län, kommun, (kommunvalkrets), valdistrikt

En nods kod är inte unik ens inom ett träd. Ett uppsamlingsdistrikt har till
exempel samma kod som sin kommunvalkrets, och i RF har regionvalkretsen 1401
samma kod som kommunen 1401 (Härryda). Det är därför vägen från riket ner till
noden som identifierar den.

Varje nod får en csv-fil med nodens röster följt av rösterna i vart och ett
av valdistrikten under noden. Filen heter <valtyp>_<koderna från riket ner
till noden>_<räkning>.csv, där räkning är P (preliminär) eller S (slutlig):

    output-csv/S/RD_00_S.csv                         riket
    output-csv/S/RD_10_S.csv                         riksdagsvalkretsen 10
    output-csv/S/RD_10_1080_S.csv                    kommunen 1080
    output-csv/S/RD_10_1080_108002_10801926_S.csv    ett valdistrikt

Skriptet summerar ingenting själv. Varje nods röster hämtas från nodens egen
summering i json-filerna. Valdistrikten talar om var i trädet varje nod sitter.

Indatat hämtas med fetch.sh, som packar upp varje zip-fil i en egen katalog.

Användning:
    python valresultat_csv.py
"""

import csv
import json
import shutil
from collections import defaultdict, namedtuple
from pathlib import Path

INPUT_DIR = Path("input-json")
OUTPUT_DIR = Path("output-csv")

RAKNINGAR = ["P", "S"]  # Preliminär och slutlig. Indatat ligger i input-json/p och /s.

RIKET_KOD = "00"

# De tre typerna av ogiltiga röster, som i csv-filen redovisas som partier.
BLANKA = "Blanka"
EJ_ANMALDA = "Ej anmälda"
OVRIGA_OGILTIGA = "övriga ogiltiga"


# ---------------------------------------------------------------------------
# Csv-formatet. Allt som rör filernas innehåll och format finns här.
# ---------------------------------------------------------------------------

CSV_ENCODING = "utf-8-sig"  # Med BOM, så att Excel läser å, ä och ö rätt.
CSV_DELIMITER = ";"
CSV_HEADER = ["Nod-kod", "områdesnamn", "parti", "antal_röster"]


def file_name(valtyp, node, rakning):
    koder = node.kodvag() or [RIKET_KOD]
    return "_".join([valtyp, *koder, rakning]) + ".csv"


def csv_rows(node):
    """Raderna i nodens csv-fil: först noden själv, sedan löven under den."""
    for area in [node, *node.leaves()]:
        for parti, antal in area.roster:
            yield [area.kod, area.namn, parti, antal]


def write_csv(path, node):
    with open(path, "w", encoding=CSV_ENCODING, newline="") as f:
        writer = csv.writer(f, delimiter=CSV_DELIMITER)
        writer.writerow(CSV_HEADER)
        writer.writerows(csv_rows(node))


# ---------------------------------------------------------------------------
# Trädet
# ---------------------------------------------------------------------------

# Namnet och rösterna för en nod, som de står i json-filerna.
Summering = namedtuple("Summering", ["namn", "roster"])


class Node:
    def __init__(self, niva, kod, summering, parent=None):
        self.niva = niva  # t ex "kommun"
        self.kod = kod
        self.namn = summering.namn
        self.roster = summering.roster  # [(parti eller typ av ogiltig röst, antal röster)]
        self.parent = parent
        self.children = {}  # kod -> Node

    def kodvag(self):
        """Koderna på vägen från riket ner till noden. Riket självt är inte med."""
        if self.parent is None:
            return []
        return self.parent.kodvag() + [self.kod]

    def is_leaf(self):
        return not self.children

    def leaves(self):
        """Alla löv under noden. Ett löv har inga löv under sig."""
        for child in self.children.values():
            if child.is_leaf():
                yield child
            else:
                yield from child.leaves()

    def all_nodes(self):
        yield self
        for child in self.children.values():
            yield from child.all_nodes()


def build_tree(vagar, summeringar):
    """Bygger ett träd av vägarna från riket till varje valdistrikt.

    En väg är en lista av (nivå, kod), utan riket. Summeringar har namnet och
    rösterna för varje nod, med (nivå, kod) som nyckel.
    """
    riket = Node("riket", RIKET_KOD, summeringar[("riket", RIKET_KOD)])
    for vag in vagar:
        node = riket
        for niva, kod in vag:
            if kod not in node.children:
                node.children[kod] = Node(niva, kod, summeringar[(niva, kod)], parent=node)
            node = node.children[kod]
    return riket


# ---------------------------------------------------------------------------
# Vägarna genom de tre träden, en väg från riket till varje valdistrikt
#
# Valkretsnivåerna finns bara där kommunen eller regionen är indelad i
# valkretsar, och bara då har valkretsen en egen summering. Valdistrikten har
# ändå alltid en valkretskod, som då slutar på 00 (t ex 011400 för Upplands
# Väsby). Den valkretsen tas bara med i vägen om den har en summering.
# ---------------------------------------------------------------------------


def kommunvalkrets_och_valdistrikt(vd, summeringar):
    vag = []
    kommunvalkrets = ("kommunvalkrets", vd["kommunvalkretsKod"])
    if kommunvalkrets in summeringar:
        vag.append(kommunvalkrets)
    vag.append(("valdistrikt", vd["valdistriktskod"]))
    return vag


def kommun_och_nedat(vd, summeringar):
    return [("kommun", vd["kommunkod"])] + kommunvalkrets_och_valdistrikt(vd, summeringar)


def vagar_rd(valdistrikt, summeringar):
    return [[("riksdagsvalkrets", vd["kretskod"])] + kommun_och_nedat(vd, summeringar) for vd in valdistrikt]


def vagar_rf(valdistrikt, summeringar):
    """Vägarna i RF-trädet.

    En kommun som är delad mellan flera regionvalkretsar ryms inte i trädet:
    den skulle behöva ligga både över och under regionvalkretsarna. Där hoppas
    kommunen över, och kommunvalkretsarna ligger direkt under regionvalkretsen.
    """
    delade_kommuner = kommuner_i_flera_regionvalkretsar(valdistrikt)
    vagar = []
    for vd in valdistrikt:
        vag = [("region", vd["valomradeskod"])]
        regionvalkrets = ("regionvalkrets", vd["kretskod"])
        if regionvalkrets in summeringar:
            vag.append(regionvalkrets)
        if vd["kommunkod"] in delade_kommuner:
            vag += kommunvalkrets_och_valdistrikt(vd, summeringar)
        else:
            vag += kommun_och_nedat(vd, summeringar)
        vagar.append(vag)
    return vagar


def kommuner_i_flera_regionvalkretsar(valdistrikt):
    """Kommunerna vars valdistrikt ligger i mer än en regionvalkrets.

    I valet 2026 gäller det bara Stockholms stad, delad mellan regionvalkretsarna
    0101-0106, där varje regionvalkrets är en av kommunens kommunvalkretsar.
    """
    regionvalkretsar = defaultdict(set)
    for vd in valdistrikt:
        regionvalkretsar[vd["kommunkod"]].add(vd["kretskod"])
    return {kommun for kommun, kretsar in regionvalkretsar.items() if len(kretsar) > 1}


def vagar_kf(valdistrikt, summeringar):
    return [[("län", vd["lankod"])] + kommun_och_nedat(vd, summeringar) for vd in valdistrikt]


# ---------------------------------------------------------------------------
# Inläsning av json-filerna
#
# Varje zip-fil från valmyndigheten är uppackad i en egen katalog, t ex
# input-json/s/rf/Val_2026_slutlig_01_RF/. Den överordnade summeringen för
# hela landet ligger i katalogen som slutar på _OS_RF (finns för RF och KF).
# ---------------------------------------------------------------------------


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def zip_dirs(rakning, valtyp):
    """Katalogerna för valtypens valområden: riket (RD), regionerna (RF) eller kommunerna (KF)."""
    katalog = INPUT_DIR / rakning.lower() / valtyp.lower()
    return sorted(d for d in katalog.glob(f"Val_*_{valtyp}") if "_OS_" not in d.name)


def os_dir(rakning, valtyp):
    """Katalogen med den överordnade summeringen för hela landet."""
    katalog = INPUT_DIR / rakning.lower() / valtyp.lower()
    [d] = katalog.glob(f"Val_*_OS_{valtyp}")
    return d


def read_from(katalog, filtyp):
    """Läser katalogens fil av en viss typ, t ex "summering" eller "mandatfordelning"."""
    [path] = katalog.glob(f"Val_*_{filtyp}*.json")
    return read_json(path)


def roster_from(rostfordelning):
    """Partiernas röster i samma ordning som i json-filen, sedan de ogiltiga rösterna."""
    giltiga = rostfordelning["rosterPaverkaMandat"]
    ogiltiga = rostfordelning["rosterEjPaverkaMandat"]
    roster = [(parti["partibeteckning"], parti["antalRoster"]) for parti in giltiga["partiRoster"]]
    roster.append((BLANKA, ogiltiga["blankaRoster"]["antalRoster"]))
    roster.append((EJ_ANMALDA, ogiltiga["rosterEjAnmaltDeltagande"]["antalRoster"]))
    roster.append((OVRIGA_OGILTIGA, ogiltiga["ovrigaOgiltiga"]["antalRoster"]))
    return roster


def summering_of(namn, json_objekt):
    return Summering(namn, roster_from(json_objekt["rostfordelning"]))


def add_valomrade(summeringar, niva, valkretsniva, valomrade):
    """Lägger till ett valområde (från en mandatfördelningsfil) och dess valkretsar."""
    summeringar[(niva, valomrade["kod"])] = summering_of(valomrade["namn"], valomrade)
    for krets in valomrade.get("valkretsLista") or []:
        summeringar[(valkretsniva, krets["kod"])] = summering_of(krets["namnValkrets"], krets)


def add_kommuner(summeringar, summeringsfil):
    """Lägger till kommunerna och kommunvalkretsarna i en RD- eller RF-summeringsfil."""
    for kommun in summeringsfil["kommuner"]:
        summeringar[("kommun", kommun["kommunkod"])] = summering_of(kommun["namn"], kommun)
        for krets in kommun.get("kommunvalkretsar") or []:
            summeringar[("kommunvalkrets", krets["kod"])] = summering_of(krets["namn"], krets)


def read_summeringar_rd(rakning):
    summeringar = {}
    for katalog in zip_dirs(rakning, "RD"):  # Bara en katalog, för hela riket.
        add_valomrade(summeringar, "riket", "riksdagsvalkrets", read_from(katalog, "mandatfordelning")["valomrade"])
        add_kommuner(summeringar, read_from(katalog, "summering"))
    return summeringar


def read_summeringar_rf(rakning):
    hela_landet = read_from(os_dir(rakning, "RF"), "summering")["helaLandet"]
    summeringar = {("riket", RIKET_KOD): summering_of(hela_landet["namn"], hela_landet)}
    for katalog in zip_dirs(rakning, "RF"):  # En katalog per region.
        add_valomrade(summeringar, "region", "regionvalkrets", read_from(katalog, "mandatfordelning")["valomrade"])
        add_kommuner(summeringar, read_from(katalog, "summering"))
    return summeringar


def read_summeringar_kf(rakning):
    hela_landet = read_from(os_dir(rakning, "KF"), "summering")["helaLandet"]
    summeringar = {("riket", RIKET_KOD): summering_of(hela_landet["namn"], hela_landet)}
    for lan in hela_landet["lan"]:
        summeringar[("län", lan["lankod"])] = summering_of(lan["namn"], lan)
        for kommun in lan["kommuner"]:
            summeringar[("kommun", kommun["kommunkod"])] = summering_of(kommun["namn"], kommun)
    for katalog in zip_dirs(rakning, "KF"):  # En katalog per kommun.
        for krets in read_from(katalog, "mandatfordelning")["valomrade"].get("valkretsLista") or []:
            summeringar[("kommunvalkrets", krets["kod"])] = summering_of(krets["namnValkrets"], krets)
    return summeringar


def read_valdistrikt(rakning, valtyp):
    for katalog in zip_dirs(rakning, valtyp):
        yield from read_from(katalog, "rostfordelning")["valdistrikt"]


TRAD = {
    "RD": (read_summeringar_rd, vagar_rd),
    "RF": (read_summeringar_rf, vagar_rf),
    "KF": (read_summeringar_kf, vagar_kf),
}


# ---------------------------------------------------------------------------


def main():
    for rakning in RAKNINGAR:
        utkatalog = OUTPUT_DIR / rakning
        shutil.rmtree(utkatalog, ignore_errors=True)  # Inga filer kvar från tidigare körningar.
        utkatalog.mkdir(parents=True)
        for valtyp, (read_summeringar, vagar_for) in TRAD.items():
            summeringar = read_summeringar(rakning)
            valdistrikt = list(read_valdistrikt(rakning, valtyp))
            for vd in valdistrikt:
                summeringar[("valdistrikt", vd["valdistriktskod"])] = summering_of(vd["namn"], vd)
            vagar = vagar_for(valdistrikt, summeringar)
            riket = build_tree(vagar, summeringar)
            antal = 0
            for node in riket.all_nodes():
                write_csv(utkatalog / file_name(valtyp, node, rakning), node)
                antal += 1
            print(f"{rakning} {valtyp}: {antal} filer")


if __name__ == "__main__":
    main()
