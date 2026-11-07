# Engine notes (OpenSupaplex)

Map of the engine for the Phase 1 patch. Line numbers are for submodule commit
`2ae9e58` (engine/). Paths are relative to `engine/src/`. Nearly all game logic
is one file, `supaplex.c` (~14k lines, a function-by-function port of the DOS
assembly; the `sub_XXXXX` / `loc_XXXXX` comments are the original labels).

## Build

```
brew install sdl2 sdl2_mixer
scripts/build_engine.sh          # -> build/engine/opensupaplex
cd build/engine && ./opensupaplex
```

- `scripts/build_engine.sh` compiles out of tree, so the submodule stays clean. It uses
  the same source list as `linux/Makefile`.
- One build issue: on macOS `sdl_common/audio.c:23-27` includes `<SDL2_mixer/SDL_mixer.h>`
  (framework layout) when `TARGET_OS_MAC` is set. Homebrew puts the header in `include/SDL2/`,
  so the script adds a shim include dir with a `SDL2_mixer -> include/SDL2` symlink. No engine
  change needed.
- The upstream Xcode project (`macos/OpenSupaplex`) is an alternative but needs SDL
  frameworks. We don't use it.
- Resources are read from the working directory (`file.c:189-192`; `FILE_BASE_PATH` is `""`
  on desktop), and config and saves are written there too. The build script copies
  `engine/resources/` into `build/engine/`. Always run from there.
- The two `asm/*.BAT` files show as modified straight after clone. That is git line-ending
  normalization in the upstream repo, not our change.

## Startup and the outer loop

- `main` (`supaplex.c:1514`): parses the CLI (`commandLineParser.c`), initializes
  video, audio and controllers, then `initializeGameStateData` (`:1775`), then
  **`generateRandomSeedFromClock()` (`:1545`)**, then `readEverything` (`:2517`, loads
  LEVELS.DAT, demos and graphics).
- Outer loop `do { ... } while (gShouldExitGame == 0)` at `:1603-1723`. Each pass:
  - `readLevels()` (`:1608`) loads the selected level.
  - `initializeGameInfo()` (`:3948`) resets per-level flags and timers.
  - `convertToEasyTiles()` and `resetNumberOfInfotrons()` (`:5979`) count infotrons.
  - `findMurphy()` (`:5806`) sets `gMurphyLocation`.
  - `runLevel()` (`:1632`) plays the level.
  - Then back to the main menu (`runMainMenu`, `:8545`) unless a level was forced.
- `-l N` / `--level N` (`commandLineParser.c:249-252`) sets `gIsForcedLevel`. That
  skips the menu: `startDirectlyFromLevel` (`:1838`) is called at `:1707-1711`.
- In fast mode (`gFastMode != FastModeTypeNone`) the outer loop exits after one
  `runLevel` (`:1640-1643`). It prints "Level(?) successful/failed" and returns
  exit code 0 or 1 (`:1727-1757`). Fast mode is only set when playing a demo file
  (`commandLineParser.c:298-401`), so a plain level can't use it yet.

## Main game loop (one tick)

`runLevel` (`supaplex.c:4024`). Each iteration of the `do { } while (1)` at
`:4068-4223` is one game tick, called an "iteration":

| Step | Line | What it does |
| --- | --- | --- |
| `handleGameIterationStarted()` | `:4070` / `:3983` | Records the start time for the speed limiter |
| `int9handler(0)` | `:4072` / `:1879` | `updateKeyboardState()`, speed keys, Alt+X quits |
| `handleGameUserInput()` | `:4099` / `:4603` | Reads input into `gCurrentUserInput`; pause, restart, cheat and debug keys |
| `updateMovingObjects()` | `:4140` / `:5355` | **The simulation step**: Murphy first, then every moving tile |
| `drawGameTime()` and panel | `:4147` | HUD only |
| `updatePlantedRedDisk()` | `:4156` / `:6428` | Red disk countdown |
| `updateExplosionTimers()` | `:4157` / `:6260` | Explosion propagation |
| `updateScrollOffset()` | `:4158` / `:5451` | Camera. **Consumes the RNG during explosions** (see RNG) |
| `drawCurrentLevelViewport()` and `videoLoop()` | `:4168-4172` | Render. Skipped in ultra fast mode |
| `handleGameIterationFinished()` | `:4173` / `:3988` | Sleeps to the target tick rate |
| Exit checks | `:4202-4222` | `gShouldExitGame`, `gShouldExitLevel`, then `gQuitLevelCountdown` counts down to 0 |

- Tick rate: the target is `(1000/35) * kSpeedTimeFactors[gGameSpeed]` ms
  (`:3990-3993`, table at `:184`). The default is `kDefaultGameSpeed = 10` (`globals.h:259`,
  factor 1/70). That makes the iteration limiter almost a no-op, so real-time pace comes
  from `videoLoop`'s frame limiter (`graphics.c:1310-1328`, skipped when `gFastMode != None`).
  Measured through the bridge with a window: **about 66 ticks/s** (Phase 1).
- For a step-N-ticks API, the natural cut point is the body of the `runLevel` loop.
  Factor `:4070-4222` into a `runLevelTick()` and call it N times per `step` command.
  Rendering and sleeping can be skipped in headless mode, like `FastModeTypeUltra`
  already does, but `updateScrollOffset` must still run, because it advances the RNG.

## Input

- Keyboard: `sdl2/keyboard.c:119` `updateKeyboardState()` calls `handleSystemEvents()`
  (`sdl_common/system.c:158`), then copies `SDL_GetKeyboardState` into globals like
  `gIsUpKeyPressed` and `gIsSpaceKeyPressed`.
- Logical buttons: `input.c:23-120` (`isUpButtonPressed`, `isActionButtonPressed`, ...)
  OR the keyboard globals with game controller state.
- Game input: `updateUserInput()` (`supaplex.c:5722`) turns the buttons into one
  `UserInput` value in `gCurrentUserInput` (`:79`). Later directions win
  (up, left, down, right). Space with a direction adds `kUserInputSpaceAndDirectionOffset`.
- `UserInput` enum (`supaplex.c:60-71`): `None=0, Up=1, Left=2, Down=3, Right=4,
  SpaceUp=5, SpaceLeft=6, SpaceDown=7, SpaceRight=8, SpaceOnly=9`. This maps 1:1 onto
  our `Move` type (`wait=0`, `snap_*` = Space+dir). `SpaceOnly` (hold space, used to
  drop a red disk) has no `Move` yet.
- `handleGameUserInput` (`:4603-4625`): if not playing a demo it calls `updateUserInput()`
  (and `saveInputForDemo()` when recording). If playing a demo, it calls
  `simulateDemoInput()` (`:4329`), which sets `gCurrentUserInput` from the demo
  stream (`:4353`).
- The only consumer that matters for the simulation is `updateMurphy` (`:10096`, reads
  `gCurrentUserInput` at `:10130`), then `handleMurphyDirection*` (`:10763`, `:11014`,
  `:11251`, `:11497`).
- **Injection point for the bridge:** set `gCurrentUserInput` directly, the same way
  `simulateDemoInput` does. Skip `updateUserInput()` in bridge mode so the keyboard
  can't interfere. The rest of `handleGameUserInput` reacts to raw keys
  (R = restart at `:4714-4717`, cheats at `:4690-4711`, pause and Esc). Gate that
  behind the same flag, or headless runs can diverge from the replay.
- Built-in precedent: demos (`demo.h`, `inputSteps` of `UserInput`, run-length encoded)
  are already a deterministic "seed + input log" replay. `saveInputForDemo` (`:4357`)
  and `recordDemo` (`:4490`) write that format. Phase 4 could reuse it, or log our own
  JSON lines.

## Level loading and state

- `readLevels()` (`supaplex.c:9699`): opens `LEVELS.DAT` (`gLevelsDatFilename`), seeks to
  `(gCurrentSelectedLevelIndex - 1) * 1536`, and parses with `mapLevelFileData`
  (`:9668`) into a `Level`. It sets gravity, frozen zonks, infotrons needed and
  special ports (`:9792-9797`).
- `fetchAndInitializeLevel()` (`:6068`) is the full reload used by restart:
  `readLevels`, `convertToEasyTiles`, `resetNumberOfInfotrons`, `initializeGameInfo`,
  `findMurphy`. `restartLevel()` (`:6012`) wraps it. **Use this for `load_level`**
  after setting `gCurrentSelectedLevelIndex`.
- `Level` struct: `globals.h:139-185`. 1536 bytes, `tiles[kLevelSize]` plus name,
  gravity, infotron count, special ports and `randomSeed`.
- Live state: `gCurrentLevelState` (`globals.h:331`) is an array of `StatefulLevelTile`
  (`globals.h:292-295`, `{uint8 tile; uint8 state;}`), 60x24 (`kLevelWidth`,
  `kLevelHeight`, `globals.h:77-79`). `tile` is a `LevelTileType` (`globals.h:31-70`).
  A nonzero `state` means the tile is mid-move or animating, for example a falling
  zonk. Some `tile` values above the enum are transient movement markers (for example
  `0xBB` for a moving snik snak, `removeTiles` `:5778-5786`; `0x88` at `:3945`).
  `get_state` should map unknown values to the nearest logical tile or "moving".
- `get_state` sources:
  - `gMurphyLocation` (`globals.h:359`): index, x = `% 60`, y = `/ 60`.
  - `gNumberOfRemainingInfotrons` (`globals.h:378`).
  - Alive: Murphy's tile exists and `gShouldKillMurphy == 0`. Death starts the
    explosion at `:5438-5446`, then `gQuitLevelCountdown = 0x40`.
  - Won: `byte_5A19B = 1` and `gQuitLevelCountdown = 0x40`, set in the four exit
    branches of `handleMurphyDirection*` (for example `:10892-10908`).
  - Tick: `gFrameCounter` (incremented at `:4206`, reset in `initializeGameInfo`).

## RNG

- One global 16-bit LCG: `generateRandomNumber()` (`supaplex.c:5712`), state
  `gRandomGeneratorSeed` (`:217`). `seed = seed*0x5E5 + 0x31; return seed/2`.
- Seeding:
  - `generateRandomSeedFromClock()` (`:5695`) is called once at startup (`:1545`), in
    `loadGameSnapshot` (`:5117`) and in `handleDemoOptionClick` (`:7203`).
    **This is the source of nondeterminism.**
  - Demos only: `readLevels` sets `gRandomGeneratorSeed = level.randomSeed` (`:9815-9817`),
    as do `playDemo` (`:7246`) and `:7231`.
  - For normal play the seed is never reset per level. The RNG carries on from
    the clock-seeded value across levels and restarts.
- Consumers during play:
  - `updateScrollOffset` (`:5465`), every tick while `gIsExplosionStarted == 1`.
  - `updateBugTiles` (`:5639`), bug sparking.
  - `updateTerminalTiles` (`:5686`), terminal animation.
  - Menu-only use at `:7204`.
- **Fixed-seed patch:** with a `--seed` flag, set `gRandomGeneratorSeed = seed` inside
  `load_level`, right after `fetchAndInitializeLevel()`, and stop the clock reseeds
  at `:1545`/`:5117`. Then RNG state depends only on (seed, level, input sequence).
- Other hidden state to watch for determinism:
  - `initializeGameStateData` (`:1775`) fills fixed padding before the level. It
    is deterministic.
  - `gFrameCounter` starts at `0xF000` and `initializeGameInfo` resets it to 0.
  - Wall-clock time only feeds the speed limiter (`:3985-4021`), frame limiting
    (`graphics.c:1320-1366`) and the HUD clock. None of these touch game state.

## Headless considerations

- `initializeVideo` (`sdl2/video.c:52`) always creates an SDL window, even in ultra
  fast mode. For headless runs, set `SDL_VIDEODRIVER=dummy` (no engine change) or skip
  video init behind the headless flag.
- `FastModeTypeUltra` already skips audio init (`:1528-1532`), title screens, fades,
  `videoLoop` and event polling (`system.c:160`). A `--headless` flag can reuse those
  branches.
- `handleSystemEvents` is skipped in ultra mode, so the window won't respond. That's
  fine headless, but the windowed replay must keep polling events.

## Phase 1 bridge (implemented)

Layout:
- `engine_patch/opensupaplex.patch` holds the hooks in `supaplex.c` and
  `commandLineParser.c`. Most of its lines are the `runLevel` loop body moving into
  `runLevelIteration()`.
- `engine_patch/bridge.c` and `bridge.h` hold the socket server.
- `scripts/build_engine.sh` copies `engine/src` to `build/engine/src`, applies the patch
  and compiles. The submodule is never modified.
- To change the hooks: copy `engine/src` to two folders, edit one, then
  `diff -ruN orig/src new/src`. Rewrite the `orig/` and `new/` prefixes to `a/` and `b/`.

Flags:
- `--bridge-socket PATH` turns on bridge mode: no menu, no title screen.
  macOS caps the path at 104 bytes.
- `--bridge-seed N` is the RNG seed applied on every `load_level`. The default is 0.
- `--headless` uses ultra fast mode plus the SDL dummy video and audio drivers. It runs
  with no window and no frame limiting.

What the hooks change:
- `main` calls `runBridge()` instead of the menu loop.
- `runLevelIteration()` is one tick. `runLevel` loops over it, so normal play is unchanged.
- In bridge mode the tick skips `handleGameUserInput()`, so keys and cheats can't
  affect a run. The bridge sets `gCurrentUserInput` before every tick.
- `bridgeLoadLevel()` runs `fetchAndInitializeLevel()` plus the per-run setup from the
  top of `runLevel`. It marks the run as "cheated" so no player or hall-of-fame files
  are written, then sets `gRandomGeneratorSeed`.

API: one JSON object per line, each way. Every reply has `"ok"`, plus `"error"` on failure.

| Request | Reply adds |
| --- | --- |
| `{"cmd":"load_level","level":N[,"seed":S]}` | status |
| `{"cmd":"get_state"}` | status + `gravity`, `width`, `height`, `tiles[1440]`, `states[1440]` |
| `{"cmd":"step","input":0..9,"n_ticks":N}` | status + `ticks_run` (stops early when the level ends) |
| `{"cmd":"quit"}` | nothing |

Status fields: `level`, `tick`, `alive`, `won`, `over`, `infotrons_left`,
`murphy{x,y}` and `rng`, the current RNG state, which makes drift easy to spot.
`alive` stays false once Murphy has died, until the next `load_level`.

Python client: `agent/bridge.py` (`Bridge.launch` / `Bridge.from_config`).
Determinism tests: `tests/test_determinism.py`. They cover:
- two processes with the same seed and inputs, on levels with and without deaths and RNG use
- reloading a level in the same process
- seed handling and error replies

Not covered yet:
- No test reaches `won`, because random moves don't solve levels. Add one from a
  recorded solution in Phase 3.
- Windowed and headless runs matched on one level 6 run, checked by hand. That
  check isn't automated yet (Phase 4).
