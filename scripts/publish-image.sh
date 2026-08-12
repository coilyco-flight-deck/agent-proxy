#!/usr/bin/env bash
# Build and publish the immutable agent-proxy image (agent-proxy#97).
# Tag is the full commit sha, which is what coilyco-bridge/deploy resolves and
# pulls. See docs/image-publishing.md.
set -euo pipefail

registry="forgejo.coilysiren.me"
image_name="coilyco-flight-deck/agent-proxy"

sha="${GITHUB_SHA:-$(git rev-parse HEAD)}"
case "${sha}" in
  *[!0-9a-f]*|"")
    echo "agent-proxy source sha is not a lowercase hexadecimal commit id." >&2
    exit 1
    ;;
esac
if [ "${#sha}" -ne 40 ]; then
  echo "agent-proxy source sha must be a full 40-character commit id." >&2
  exit 1
fi

if [ -z "${REGISTRY_TOKEN:-}" ]; then
  echo "REGISTRY_TOKEN is required for the trusted image-publish lane." >&2
  exit 1
fi

image="${registry}/${image_name}:${sha}"

docker_config="$(mktemp -d)"
trap 'rm -rf "${docker_config}"' EXIT
chmod 700 "${docker_config}"
export DOCKER_CONFIG="${docker_config}"

printf '%s' "${REGISTRY_TOKEN}" \
  | docker login "${registry}" --username coilyco-ops --password-stdin

echo "==> building ${image_name}:${sha}"
docker build --pull -t "${image}" .

echo "==> publishing ${image_name}:${sha}"
docker push "${image}"

echo "published ${image_name}:${sha}"
