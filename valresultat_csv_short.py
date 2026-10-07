import csv, json, shutil
from collections import Counter
from pathlib import Path

AREA = {"RD": ("riket", "riksdagsvalkrets"), "RF": ("region", "regionvalkrets"), "KF": ("kommun", "kommunvalkrets")}
LOWER = [("kommun", "kommunkod"), ("kommunvalkrets", "kommunvalkretsKod"), ("valdistrikt", "valdistriktskod")]
LEVELS = {"RD": [("riksdagsvalkrets", "kretskod")] + LOWER, "RF": [("region", "valomradeskod"), ("regionvalkrets", "kretskod")] + LOWER, "KF": [("län", "lankod")] + LOWER}
INVALID = [("Blanka", "blankaRoster"), ("Ej anmälda", "rosterEjAnmaltDeltagande"), ("övriga ogiltiga", "ovrigaOgiltiga")]


def read(folder, kind):
    return json.load(open(next(folder.glob(f"Val_*_{kind}*.json")), encoding="utf-8"))


def folders(count, election, overall=False):
    return sorted(folder for folder in Path("input-json", count.lower(), election.lower()).glob(f"Val_*_{election}") if ("_OS_" in folder.name) == overall)


def votes(source):
    valid, invalid = source["rostfordelning"]["rosterPaverkaMandat"], source["rostfordelning"]["rosterEjPaverkaMandat"]
    return [(party["partibeteckning"], party["antalRoster"]) for party in valid["partiRoster"]] + [(name, invalid[key]["antalRoster"]) for name, key in INVALID]


def summaries(count, election):
    summary = {}
    def add(level, code, name, source): summary[(level, code)] = (name, votes(source))
    for folder in folders(count, election):
        area = read(folder, "mandatfordelning")["valomrade"]
        add(AREA[election][0], area["kod"], area["namn"], area)
        for circuit in area.get("valkretsLista") or []: add(AREA[election][1], circuit["kod"], circuit["namnValkrets"], circuit)
        for municipality in read(folder, "summering")["kommuner"] if election != "KF" else []:
            add("kommun", municipality["kommunkod"], municipality["namn"], municipality)
            for circuit in municipality.get("kommunvalkretsar") or []: add("kommunvalkrets", circuit["kod"], circuit["namn"], circuit)
    for whole in [read(folder, "summering")["helaLandet"] for folder in folders(count, election, overall=True)]:
        add("riket", "00", whole["namn"], whole)
        for county in whole["lan"] if election == "KF" else []:
            add("län", county["lankod"], county["namn"], county)
            for municipality in county["kommuner"]: add("kommun", municipality["kommunkod"], municipality["namn"], municipality)
    return summary


for count in "PS":
    out = Path("output-csv", count)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    for election, levels in LEVELS.items():
        summary = summaries(count, election)
        districts = [district for folder in folders(count, election) for district in read(folder, "rostfordelning")["valdistrikt"]]
        summary.update({("valdistrikt", district["valdistriktskod"]): (district["namn"], votes(district)) for district in districts})
        split = {municipality for municipality, circuits in Counter(municipality for municipality, _ in {(district["kommunkod"], district["kretskod"]) for district in districts}).items() if circuits > 1} if election == "RF" else set()
        paths = [tuple((level, district[field]) for level, field in levels if (level, district[field]) in summary and not (level == "kommun" and district[field] in split)) for district in districts]
        rank, nodes = {}, {}
        for path in paths:
            for depth in range(len(path) + 1): rank.setdefault(path[:depth], len(rank))
        for path in sorted(paths, key=lambda path: [rank[path[:depth]] for depth in range(len(path) + 1)]):
            for depth in range(len(path) + 1): nodes.setdefault(path[:depth], []).append(path)
        for node, leaves in nodes.items():
            keys = [area[-1] if area else ("riket", "00") for area in [node] + [leaf for leaf in leaves if leaf != node]]
            with open(out / ("_".join([election, *([code for _, code in node] or ["00"]), count]) + ".csv"), "w", encoding="utf-8-sig", newline="") as file:
                csv.writer(file, delimiter=";").writerows([["Nod-kod", "områdesnamn", "parti", "antal_röster"]] + [[key[1], summary[key][0], party, number] for key in keys for party, number in summary[key][1]])
        print(f"{count} {election}: {len(nodes)} filer")
