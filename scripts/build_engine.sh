#!/usr/bin/env bash
# Build OpenSupaplex (engine/ submodule) out of tree into build/engine/.
# The submodule stays clean: the source is copied to build/engine/src, patched
# with engine_patch/opensupaplex.patch, and compiled together with
# engine_patch/bridge.c. Objects, binary and game resources live in build/engine/.
# Run the game from that directory, since the engine reads resources and writes
# saves relative to the working directory.
#
# Needs: brew install sdl2 sdl2_mixer
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/build/engine"
SRC="$OUT/src"
PATCH_DIR="$ROOT/engine_patch"
OBJ="$OUT/obj"

command -v sdl2-config >/dev/null || { echo "sdl2-config not found: brew install sdl2 sdl2_mixer" >&2; exit 1; }
PREFIX="$(brew --prefix 2>/dev/null || echo /usr/local)"

mkdir -p "$OBJ" "$OUT/shim"

# Fresh copy of upstream source (rsync keeps mtimes, so unpatched files don't rebuild)
rsync -a --delete "$ROOT/engine/src/" "$SRC/"
patch -s -p1 -d "$OUT" < "$PATCH_DIR/opensupaplex.patch"

# On macOS audio.c includes <SDL2_mixer/SDL_mixer.h> (framework layout).
# Homebrew installs the header under include/SDL2, so map one onto the other.
ln -sfn "$PREFIX/include/SDL2" "$OUT/shim/SDL2_mixer"

CFLAGS="${CFLAGS:--O2} -DHAVE_SDL2 -I$OUT/shim -I$PATCH_DIR -I$SRC $(sdl2-config --cflags)"
LDFLAGS="-L$PREFIX/lib -lSDL2_mixer $(sdl2-config --libs) -lm"

SOURCES=(
  "$SRC"/*.c
  "$SRC"/sdl2/*.c
  "$SRC"/sdl_common/*.c
  "$SRC"/lib/ini/ini.c
  "$SRC"/null/virtualKeyboard.c
  "$PATCH_DIR"/bridge.c
)

objs=()
for src in "${SOURCES[@]}"; do
  rel="${src#"$SRC"/}"
  rel="${rel#"$ROOT"/}"
  obj="$OBJ/${rel%.c}.o"
  objs+=("$obj")
  if [[ ! -f "$obj" || "$src" -nt "$obj" || "$PATCH_DIR/bridge.h" -nt "$obj" ]]; then
    mkdir -p "$(dirname "$obj")"
    echo "cc $rel"
    # shellcheck disable=SC2086
    cc $CFLAGS -c "$src" -o "$obj" &
  fi
done
wait

# shellcheck disable=SC2086
cc -o "$OUT/opensupaplex" "${objs[@]}" $LDFLAGS

# Copy resources without clobbering saves/config the game wrote here.
rsync -a --ignore-existing "$ROOT/engine/resources/" "$OUT/"

echo "built $OUT/opensupaplex (run from $OUT)"
