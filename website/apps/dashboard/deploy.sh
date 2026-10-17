#!/bin/bash
# Deploy Tortoise Dashboard to Cloudflare Pages (app.premiselabs.co)
set -e
cd "$(dirname "$0")"
# #3768: node_modules is no longer version-controlled, so a clean checkout must
# install first; without this the build fails instead of silently using a stale
# tracked tree.
if [ ! -d node_modules ]; then npm ci; fi
npm run build
# #8816: the version has ONE home. Read it before the `cd` below matters — this
# script runs from website/apps/dashboard, so the path climbs to the repo root —
# and ASSERT what npm resolved: a declared "4" resolves to 4.151.1 and fails,
# where a bare `npx wrangler` would silently deploy whatever is latest.
WRANGLER_VERSION="$(cat "$(dirname "$0")/../../../config/wrangler-version")"
: "${WRANGLER_VERSION:?config/wrangler-version is empty}"
got="$(npx --yes "wrangler@${WRANGLER_VERSION:?}" --version | tr -d '[:space:]')"
if [ "$got" != "$WRANGLER_VERSION" ]; then
  echo "::error::resolved wrangler ${got} != declared ${WRANGLER_VERSION}"
  exit 1
fi
npx --yes "wrangler@${WRANGLER_VERSION:?}" pages deploy dist --project-name=tortoise-dashboard --branch=main
