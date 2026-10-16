#!/usr/bin/env bash
# Lists which required GitHub Actions secrets are missing. Prints names only, never values.
set -euo pipefail

REQUIRED=(API_FOOTBALL_KEY FOOTBALL_DATA_ORG_KEY GUARDIAN_API_KEY)
OPTIONAL=(GROQ_API_KEY OPENROUTER_API_KEY HF_TOKEN)

if ! command -v gh >/dev/null 2>&1; then
  echo "gh (GitHub CLI) is not installed. Add these as repository secrets instead:"
  echo "  repo -> Settings -> Secrets and variables -> Actions -> New repository secret"
  printf '  %s (required)\n' "${REQUIRED[@]}"
  printf '  %s (optional)\n' "${OPTIONAL[@]}"
  exit 1
fi

present="$(gh secret list --json name --jq '.[].name' 2>/dev/null || true)"
if [ -z "$present" ] && ! gh secret list >/dev/null 2>&1; then
  echo "Could not list secrets. Run 'gh auth login' and make sure this directory is a GitHub repo." >&2
  exit 1
fi

missing=0
for name in "${REQUIRED[@]}"; do
  if grep -qx "$name" <<<"$present"; then echo "ok       $name"; else echo "MISSING  $name (required)"; missing=1; fi
done
for name in "${OPTIONAL[@]}"; do
  if grep -qx "$name" <<<"$present"; then echo "ok       $name"; else echo "missing  $name (optional)"; fi
done
exit $missing
