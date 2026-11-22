#!/usr/bin/env bash
# Stand up the self-hosted Overpass mirror (internal design notes).
#
# Why this exists as a script: the original mirror was built by hand from shell
# history, with OVERPASS_META=no.
# That import cannot answer "has this element changed since we resolved it",
# because without meta there are no element versions to compare -- so drift on a
# pinned coordinate is undetectable (internal design notes). This re-imports with meta,
# and records how, so the next rebuild is not archaeology.
#
# Cost: ~1.5 GB download, several hours of import, ~30-40 GB on disk. It runs
# detached; the container does the work itself.
#
#   bash build/setup-overpass.sh            # start the import
#   bash build/setup-overpass.sh --status   # progress
#   bash build/setup-overpass.sh --verify   # once it is serving
set -euo pipefail

ROOT="${OVERPASS_ROOT:-$HOME/projects/overpass-api}"
DB_DIR="${OVERPASS_DB_DIR:-$ROOT/overpass-db-meta}"
NAME="${OVERPASS_CONTAINER:-overpass-india-meta}"
# Bound to localhost on purpose. The browser must never reach this directly --
# Django proxies and caches it (internal design notes).
PORT="${OVERPASS_PORT:-8003}"
IMAGE="wiktorn/overpass-api:latest"
# Geofabrik retired the .osm.bz2 extracts that the 2025 import used -- only PBF
# is published now, and the old URL 404s. The container's importer is
# `bunzip2 < planet.osm.bz2 | update_database`, so the file genuinely has to be
# bzip2ed XML; see OVERPASS_PLANET_PREPROCESS below.
PLANET_URL="https://download.geofabrik.de/asia/india-latest.osm.pbf"
DIFF_URL="https://download.geofabrik.de/asia/india-updates"

status() {
  if ! docker inspect "$NAME" >/dev/null 2>&1; then
    echo "No container named $NAME. Run without --status to create it."
    exit 1
  fi
  echo "container : $(docker inspect -f '{{.State.Status}}' "$NAME")"
  echo "db size   : $(du -sh "$DB_DIR" 2>/dev/null | cut -f1)"
  echo "--- last 15 log lines ---"
  docker logs --tail 15 "$NAME" 2>&1
}

verify() {
  echo "--- serving? ---"
  curl -sS -m 30 -G "http://127.0.0.1:${PORT}/api/interpreter" \
    --data-urlencode 'data=[out:json][timeout:25];node(1);out meta;' | head -c 400
  echo
  echo "--- meta present? (a version field must appear) ---"
  curl -sS -m 60 -G "http://127.0.0.1:${PORT}/api/interpreter" \
    --data-urlencode 'data=[out:json][timeout:50];nwr["historic"="temple"](19.7,85.7,19.9,85.9);out meta 1;' \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); e=(d.get("elements") or [{}])[0]; print("version:", e.get("version", "MISSING -- imported without OVERPASS_META=yes"))'
  echo "--- areas dispatcher up? ---"
  # Known behaviour: after the initial import the areas dispatcher comes up
  # without creating its socket, so every area query fails with
  # "open64: 2 No such file or directory /db/db//osm3s_areas" even though the
  # area data was generated correctly. A container restart fixes it.
  if [ -S "${DB_DIR}/db/osm3s_areas" ]; then
    echo "areas socket: present"
  else
    echo "areas socket: MISSING -- area queries will fail. Fix with:"
    echo "  docker restart ${NAME}"
  fi
  echo "--- areas generated? (district queries need them) ---"
  curl -sS -m 120 -G "http://127.0.0.1:${PORT}/api/interpreter" \
    --data-urlencode 'data=[out:json][timeout:100];area["name"="Puri"]["boundary"="administrative"];out ids 3;' \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); n=len(d.get("elements") or []); print(f"areas found: {n}" + ("" if n else "  -- rules load did not run; district queries will not work"))'
}

case "${1:-}" in
  --status) status; exit 0;;
  --verify) verify; exit 0;;
esac

if docker inspect "$NAME" >/dev/null 2>&1; then
  echo "$NAME already exists. Use --status, or remove it first:"
  echo "  docker rm -f $NAME"
  exit 1
fi

# The old META=no database is left alone. It is 15 GB of dead weight once this
# one works, but deleting someone else's data is not this script's call.
mkdir -p "$DB_DIR"
echo "Importing into $DB_DIR (several hours; container runs detached)."

docker run -d \
  --name "$NAME" \
  --restart unless-stopped \
  -p "127.0.0.1:${PORT}:80" \
  -e OVERPASS_META=yes \
  -e OVERPASS_MODE=init \
  -e OVERPASS_PLANET_URL="$PLANET_URL" \
  -e OVERPASS_DIFF_URL="$DIFF_URL" \
  `# The entrypoint saves the download as planet.osm.bz2 whatever it actually` \
  `# is, then hands it to bunzip2. Rename it back to .pbf and transcode.` \
  `# bzip2 is single-threaded and this is ~30 GB of XML, so use pbzip2 across` \
  `# all cores when it can be installed -- minutes instead of hours -- and fall` \
  `# back to plain osmium when it cannot. pbzip2 writes concatenated bz2` \
  `# streams, which bunzip2 reads without complaint.` \
  -e OVERPASS_PLANET_PREPROCESS='mv /db/planet.osm.bz2 /db/planet.osm.pbf && (apt-get update -qq && apt-get install -y -qq pbzip2 || true) && if command -v pbzip2 >/dev/null 2>&1; then osmium cat -f osm -o - /db/planet.osm.pbf | pbzip2 -c > /db/planet.osm.bz2; else osmium cat -o /db/planet.osm.bz2 /db/planet.osm.pbf; fi && rm -f /db/planet.osm.pbf' \
  `# Area generation (on by default, pinned here because the per-district` \
  `# seeding query in internal design notes does not work without it).` \
  -e OVERPASS_USE_AREAS=true \
  -e OVERPASS_UPDATE_SLEEP=86400 \
  -e OVERPASS_MAX_TIMEOUT=300 \
  -v "$DB_DIR:/db" \
  "$IMAGE"

echo
echo "Started. Watch it with:"
echo "  bash build/setup-overpass.sh --status"
echo "Then, once it is serving:"
echo "  bash build/setup-overpass.sh --verify"
echo "  OVERPASS_URL=http://127.0.0.1:${PORT}/api/interpreter"
