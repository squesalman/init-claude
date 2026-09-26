#!/usr/bin/env sh
# Tailwind standalone CLI (ADR-0002: no node_modules). Usage: scripts/tailwind.sh [build|watch]
# Downloads the pinned binary into bin/ (gitignored) on first run and checks its sha256.
# Output static/css/app.css is committed, so the app runs without this binary.
set -eu
cd "$(dirname "$0")/.."
VERSION=v3.4.17
# ponytail: Linux only (downloads the linux binary, verifies with sha256sum); add a
# macos branch when someone develops on a Mac.
[ "$(uname -s)" = Linux ] || { echo "scripts/tailwind.sh supports Linux only (got $(uname -s))" >&2; exit 1; }
case "$(uname -m)" in
  aarch64|arm64) ARCH=arm64; SHA=69b1378b8133192d7d2feb12a116fa12d035594f58db3eff215879e4ad8cf39b ;;
  x86_64|amd64)  ARCH=x64;   SHA=7d24f7fa191d2193b78cd5f5a42a6093e14409521908529f42d80b11fde1f1d4 ;;
  *) echo "unsupported arch: $(uname -m)" >&2; exit 1 ;;
esac
BIN=bin/tailwindcss-$VERSION-linux-$ARCH
if [ ! -x "$BIN" ]; then
  mkdir -p bin
  curl -fsSL -o "$BIN.tmp" "https://github.com/tailwindlabs/tailwindcss/releases/download/$VERSION/tailwindcss-linux-$ARCH"
  echo "$SHA  $BIN.tmp" | sha256sum -c - >/dev/null
  chmod +x "$BIN.tmp" && mv "$BIN.tmp" "$BIN"
fi
case "${1:-build}" in
  build) exec "$BIN" -i static/src/input.css -o static/css/app.css --minify ;;
  watch) exec "$BIN" -i static/src/input.css -o static/css/app.css --watch ;;
  *) echo "usage: $0 [build|watch]" >&2; exit 1 ;;
esac
