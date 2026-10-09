#!/usr/bin/env bash
# Make the repository's labels match the list below, which is the source of truth.
#
#   .github/sync-labels.sh            create or update every listed label
#   .github/sync-labels.sh --prune    also delete labels that are not listed
#
# Labels describe state or what an issue waits on. What an issue is (bug, feature) is
# its issue type, which the issue forms set, and when it ships is its milestone.
# Without --prune an unlisted label is only reported: deleting one strips it from every
# issue that carries it.

set -euo pipefail

REPO="${REPO:-crosswatch-app/kodi-addon}"

# name|colour|description
LABELS="$(cat <<'EOF'
needs-crosswatch|5319e7|Waiting on CrossWatch: the contract, a server change or a release
needs-info|fbca04|Waiting on the reporter for details
bug|d73a4a|Something isn't working
enhancement|a2eeef|New feature or request
documentation|0075ca|Improvements or additions to documentation
accessibility|f143ab|Barrier affecting people with disabilities
question|d876e3|Further information is requested
duplicate|cfd3d7|This issue or pull request already exists
invalid|e4e669|This doesn't seem right
wontfix|ffffff|This will not be worked on
good first issue|7057ff|Good for newcomers
help wanted|008672|Extra attention is needed
EOF
)"

prune=false
case "${1:-}" in
    "") ;;
    --prune) prune=true ;;
    *) echo "usage: sync-labels.sh [--prune]" >&2; exit 2 ;;
esac

while IFS='|' read -r name color description; do
    gh label create "$name" --repo "$REPO" --color "$color" --description "$description" --force >/dev/null
    echo "ok       $name"
done <<< "$LABELS"

listed="$(cut -d'|' -f1 <<< "$LABELS")"
gh label list --repo "$REPO" --limit 200 --json name --jq '.[].name' | while IFS= read -r name; do
    grep -qxF "$name" <<< "$listed" && continue
    if $prune; then
        gh label delete "$name" --repo "$REPO" --yes >/dev/null
        echo "deleted  $name"
    else
        echo "unlisted $name (kept; --prune deletes it)"
    fi
done
