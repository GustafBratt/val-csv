#!/usr/bin/env bash
# Hämtar alla filer som listas i index.md5 för val 2026.
# Checksummorna i första kolumnen ignoreras.
#
# Användning: ./fetch.sh [målkatalog]   (standard: ./val2026)

set -euo pipefail

BASE_URL="https://resultat.val.se/resultatfiler/val2026"
DEST="${1:-val2026}"
PARALLEL="${PARALLEL:-4}"

mkdir -p "$DEST"
cd "$DEST"

curl -fsSL "$BASE_URL/index.md5" -o index.md5

# Andra kolumnen är sökvägen (./p/kf/...). Ta bort CR och inledande "./".
awk '{ sub(/\r$/, ""); if ($2 != "") { sub(/^\.\//, "", $2); print $2 } }' index.md5 |
  xargs -P "$PARALLEL" -I {} \
    curl -fsSL --create-dirs --retry 3 -o "{}" "$BASE_URL/{}"

# Packa upp varje zip-fil i sin egen katalog och ta bort den efteråt, t ex
# s/rf/Val_2026_slutlig_01_RF.zip -> s/rf/Val_2026_slutlig_01_RF/.
# Egen katalog behövs: alla RF-zippar innehåller en Val_2026_slutlig_summering_RF.json,
# och packade i samma katalog skulle de skriva över varandra.
# Zip-filen tas bara bort om uppackningen lyckades.
find . -type f -iname '*.zip' -print0 |
  xargs -0 -P "$PARALLEL" -I {} \
    sh -c 'unzip -oq "$1" -d "${1%.*}" && rm -f "$1"' _ {}

echo "Klart: $(awk 'NF >= 2' index.md5 | wc -l) filer i $DEST"
