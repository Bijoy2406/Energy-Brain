#!/usr/bin/env bash
# Export working-tree build inputs, including uncommitted changes, to a separate Space repo.
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
cd -- "$repo_root"
regenerate=false
destination=
while [[ $# -gt 0 ]]; do
    case "$1" in
        --regenerate) regenerate=true ;;
        --help|-h)
            printf 'Usage: bash scripts/make_hf_space.sh [--regenerate] [destination]\n'
            exit 0 ;;
        --*) printf 'Unknown option: %s\n' "$1" >&2; exit 1 ;;
        *)
            [[ -z "$destination" ]] || { printf 'Specify only one destination.\n' >&2; exit 1; }
            destination=$1 ;;
    esac
    shift
done
destination=${destination:-./build/hf-space}
if [[ -e "$destination" || -L "$destination" ]]; then
    printf 'Refusing to overwrite an existing destination. Choose a new, empty path.\n' >&2
    exit 1
fi

# Only these working-tree artifacts may enter the Space; never export whole directories.
ALLOWLIST=(
    data/processed/weather.parquet
    data/processed/pv.parquet
    data/processed/load.parquet
    models/solar_p10.txt
    models/solar_p50.txt
    models/solar_p90.txt
    models/load_p10.txt
    models/load_p50.txt
    models/load_p90.txt
)

# Reject symlinked files and parents rather than following them outside the allowlist.
validate_input() {
    local file=$1 parent=$1
    while [[ "$parent" != . ]]; do
        if [[ -L "$parent" ]]; then
            printf 'Refusing a symlinked deployment input: %s\n' "$file" >&2; exit 1
        fi
        parent=$(dirname -- "$parent")
    done
    [[ -f "$file" ]] || { printf 'Missing a required deployment input: %s\n' "$file" >&2; exit 1; }
}

if [[ "$regenerate" == false ]]; then
    missing=false
    for file in "${ALLOWLIST[@]}"; do
        if [[ ! -f "$file" ]]; then
            printf 'Missing local artifact: %s\n' "$file" >&2
            missing=true
        fi
    done
    if [[ "$missing" == true ]]; then
        cat >&2 <<'COMMANDS'
Build the data locally first (see docs/data-sources.md), then train both models:
python -m energy_brain.data.download_weather
python -m energy_brain.data.simulate_pv
python -m energy_brain.data.synthetic_load
python -m energy_brain.forecast.solar --train
python -m energy_brain.forecast.load --train
Refresh the reports after any data change or retrain before exporting:
python -m energy_brain.evaluator
python -m energy_brain.api record-demo-answers
Or use --regenerate to ship no artifacts and regenerate during the Docker build.
COMMANDS
        exit 1
    fi
    for file in "${ALLOWLIST[@]}"; do validate_input "$file"; done
fi

# Validate the complete list before creating or copying anything. git lists names only;
# cp reads current working-tree contents, so no staging or commit is needed.
files=(Dockerfile .dockerignore pyproject.toml
    reports/backtest.json reports/explainer_demo_answers.json reports/forecast_metrics.json)
while IFS= read -r -d '' file; do
    files+=("$file")
done < <(git ls-files --cached --others --exclude-standard -z -- src/ config/ prompts/ web/)
if [[ -f LICENSE ]]; then files+=(LICENSE); fi
files+=(deploy/hf-space/README.md deploy/artifacts/README.md)

for file in "${files[@]}"; do
    case "/$file/" in
        */.env/*|*/data/raw/*|*/data/processed/*|*/models/*)
            printf 'Refusing a forbidden deployment input.\n' >&2; exit 1 ;;
    esac
    case "${file##*/}" in
        .env.*) [[ "${file##*/}" == .env.example ]] || {
            printf 'Refusing a local environment file.\n' >&2; exit 1;
        } ;;
    esac
    validate_input "$file"
done

mkdir -p -- "$destination"
destination=$(cd -- "$destination" && pwd -P)
for file in "${files[@]}"; do
    target=$file
    if [[ "$file" == deploy/hf-space/README.md ]]; then target=README.md; fi
    mkdir -p -- "$destination/$(dirname -- "$target")"
    cp -- "$file" "$destination/$target"
done

artifact_bytes=0
if [[ "$regenerate" == false ]]; then
    printf 'Shipping local artifacts (bytes):\n'
    for file in "${ALLOWLIST[@]}"; do
        target="$destination/deploy/artifacts/$file"
        mkdir -p -- "$(dirname -- "$target")"
        cp -- "$file" "$target"
        size=$(wc -c < "$target")
        artifact_bytes=$((artifact_bytes + size))
        printf '  %s  %s\n' "$size" "$file"
    done
    (cd -- "$destination/deploy/artifacts" && sha256sum -- "${ALLOWLIST[@]}" > MANIFEST.sha256)
fi
artifact_mb=$(awk -v bytes="$artifact_bytes" 'BEGIN { printf "%.2f", bytes / 1000000 }')

printf 'Prepared Space folder: %s\n' "$destination"
du -sh -- "$destination"
if [[ "$regenerate" == false ]]; then
    printf 'Shipped 9 artifacts: weather/PV/load data and 6 trained models (%s MB), with SHA-256 manifest.\n' "$artifact_mb"
    printf 'The Docker build verifies these files and skips data generation and training.\n'
else
    printf 'Shipped no artifacts (0 MB; placeholder README only). Docker will regenerate data and models.\n'
    printf 'Stored demo answers may stop matching the regenerated plan.\n'
fi
printf '\nNext commands (replace <user> and <space>; run these yourself):\n'
printf 'cd %q\n' "$destination"
cat <<'COMMANDS'
git init -b main
git remote add origin https://huggingface.co/spaces/<user>/<space>
git fetch origin main
git reset --mixed origin/main
git add --all
git commit -m "Deploy Energy Brain"
git push -u origin main
COMMANDS
printf '\nUse your Hugging Face username and write token at the Git credential prompt.\n'
