#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
registry=acrrvfdbmigprodneu
subscription=a4c6a1f0-df64-40b6-972f-c4c79d6f15c9
source_sha=$(git rev-parse HEAD)
[[ $(git branch --show-current) == codex/azure-0.9.0 ]] || { echo 'Use the reviewed release branch.' >&2; exit 1; }
[[ -z $(git status --porcelain --untracked-files=normal) ]] || { echo 'Require a clean source checkout.' >&2; exit 1; }
# Build from committed files only: ignored local secrets must never reach ACR.
build_context=$(mktemp -d)
trap 'rm -rf "$build_context"' EXIT
git archive HEAD backend frontend | tar -x -C "$build_context"
# Sequential remote builds. No production deployment or migrations here.
az acr build --subscription "$subscription" --registry "$registry" --platform linux/amd64 --timeout 1800 \
  --image "open-wearables/backend:$source_sha" --build-arg "GIT_SHA=$source_sha" "$build_context/backend"
az acr build --subscription "$subscription" --registry "$registry" --platform linux/amd64 --timeout 1800 \
  --image "open-wearables/frontend:$source_sha" --build-arg VITE_API_URL=https://wearables.herofitness.es "$build_context/frontend"
login_server=$(az acr show --subscription "$subscription" --name "$registry" --query loginServer -o tsv)
backend_digest=$(az acr repository show --subscription "$subscription" --name "$registry" --image "open-wearables/backend:$source_sha" --query digest -o tsv)
frontend_digest=$(az acr repository show --subscription "$subscription" --name "$registry" --image "open-wearables/frontend:$source_sha" --query digest -o tsv)
[[ $backend_digest =~ ^sha256:[a-f0-9]{64}$ && $frontend_digest =~ ^sha256:[a-f0-9]{64}$ ]] || { echo 'Missing image digests.' >&2; exit 1; }
umask 077
printf 'BACKEND_IMAGE=%s/open-wearables/backend@%s\nFRONTEND_IMAGE=%s/open-wearables/frontend@%s\n' \
  "$login_server" "$backend_digest" "$login_server" "$frontend_digest" > deploy/azure-vm/release-images.env
echo 'Image digests written to deploy/azure-vm/release-images.env; copy into the VM .env.'
