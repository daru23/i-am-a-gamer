/*
 * AI bridge for OpenSupaplex: a Unix-socket JSON-lines control API.
 * Not part of upstream; compiled in by scripts/build_engine.sh.
 */

#ifndef bridge_h
#define bridge_h

#include <stdint.h>

// Called by the command line parser
void bridgeSetSocketPath(const char *path);
void bridgeSetSeed(int seed);
void bridgeSetHeadless(void);

uint8_t isBridgeEnabled(void);

/// Serves clients until "quit" or the window is closed. Returns the process exit code.
int runBridge(void);

// Implemented in supaplex.c by engine_patch/opensupaplex.patch
void bridgeLoadLevel(uint8_t levelNumber, uint16_t seed);
void bridgeSetUserInput(uint8_t input);
uint16_t bridgeGetRandomGeneratorSeed(void);
uint8_t bridgeIsLevelCompleted(void);
uint8_t runLevelIteration(void);

#endif /* bridge_h */
