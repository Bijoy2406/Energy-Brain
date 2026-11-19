#!/usr/bin/env bash
# Export working-tree build inputs, including uncommitted changes, to a separate Space repo.
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
cd -- "$repo_root"
destination=${1:-./build/hf-space}
if [[ -e "$destination" || -L "$destination" ]]; then
    printf 'Refusing to overwrite an existing destination. Choose a new, empty path.\n' >&2
    exit 1
fi

# Validate the complete list before creating or copying anything. git lists names only;
# cp reads current working-tree contents, so no staging or commit is needed.
files=(Dockerfile .dockerignore pyproject.toml
    reports/backtest.json reports/explainer_demo_answers.json reports/forecast_metrics.json)
while IFS= read -r -d '' file; do
    files+=("$file")
done < <(git ls-files --cached --others --exclude-standard -z -- src/ config/ prompts/ web/)
if [[ -f LICENSE ]]; then files+=(LICENSE); fi
files+=(deploy/hf-space/README.md)

for file in "${files[@]}"; do
    case "/$file/" in
        */.env/*|*/data/processed/*|*/models/*)
            printf 'Refusing a forbidden deployment input.\n' >&2; exit 1 ;;
    esac
    case "${file##*/}" in
        .env.*) [[ "${file##*/}" == .env.example ]] || {
            printf 'Refusing a local environment file.\n' >&2; exit 1;
        } ;;
    esac
    # Reject symlinked files and parent directories rather than following them outside the allowlist.
    parent=$file
    while [[ "$parent" != . ]]; do
        if [[ -L "$parent" ]]; then
            printf 'Refusing a symlinked deployment input.\n' >&2; exit 1
        fi
        parent=$(dirname -- "$parent")
    done
    [[ -f "$file" ]] || { printf 'Missing a required deployment input: %s\n' "$file" >&2; exit 1; }
done

mkdir -p -- "$destination"
destination=$(cd -- "$destination" && pwd -P)
for file in "${files[@]}"; do
    target=$file
    if [[ "$file" == deploy/hf-space/README.md ]]; then target=README.md; fi
    mkdir -p -- "$destination/$(dirname -- "$target")"
    cp -- "$file" "$destination/$target"
done

printf 'Prepared Space folder: %s\n' "$destination"
du -sh -- "$destination"
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
