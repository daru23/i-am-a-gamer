/*
 * AI bridge for OpenSupaplex: a Unix-socket JSON-lines control API.
 * Not part of upstream; compiled in by scripts/build_engine.sh.
 *
 * One client at a time. Each request is one JSON object per line, each reply
 * is one JSON object per line with "ok": true|false.
 *
 *   {"cmd": "load_level", "level": 1}             optional "seed" overrides --bridge-seed
 *   {"cmd": "get_state"}
 *   {"cmd": "step", "input": 4, "n_ticks": 8}     input is a UserInput value (0..9)
 *   {"cmd": "quit"}
 *
 * The JSON reader only handles the flat objects above: it is not a general parser.
 */

#include "bridge.h"

#include <errno.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include "globals.h"
#include "system.h"

#define kBridgeLineBufferSize 65536
#define kBridgeMaxTicksPerStep 100000

static char sSocketPath[sizeof(((struct sockaddr_un *)0)->sun_path)] = "";
static uint16_t sSeed = 0;
static uint8_t sIsHeadless = 0;

static uint8_t sIsLevelLoaded = 0;
static uint8_t sLevelNumber = 0;
static uint8_t sIsLevelOver = 0;
static uint8_t sHasMurphyDied = 0; // Latched: the death countdown resets to 0 when the level ends
static uint32_t sTick = 0;

void bridgeSetSocketPath(const char *path)
{
    if (strlen(path) >= sizeof(sSocketPath))
    {
        fprintf(stderr, "bridge: socket path longer than %zu bytes: %s\n", sizeof(sSocketPath) - 1, path);
        exit(1);
    }
    strcpy(sSocketPath, path);
}

void bridgeSetSeed(int seed)
{
    sSeed = (uint16_t)seed;
}

void bridgeSetHeadless(void)
{
    sIsHeadless = 1;
    // Ultra fast mode already skips audio, rendering to screen and all frame limiting
    gFastMode = FastModeTypeUltra;
    setenv("SDL_VIDEODRIVER", "dummy", 1);
    setenv("SDL_AUDIODRIVER", "dummy", 1);
    // SDL turns SIGTERM/SIGINT into quit events, which headless mode never polls
    setenv("SDL_NO_SIGNAL_HANDLERS", "1", 1);
}

uint8_t isBridgeEnabled(void)
{
    return sSocketPath[0] != '\0';
}

// --- Minimal JSON reading ---

static const char *findValue(const char *line, const char *key)
{
    char quotedKey[64];
    snprintf(quotedKey, sizeof(quotedKey), "\"%s\"", key);

    const char *cursor = strstr(line, quotedKey);
    if (cursor == NULL)
    {
        return NULL;
    }
    cursor += strlen(quotedKey);
    while (*cursor == ' ' || *cursor == '\t')
    {
        cursor++;
    }
    if (*cursor != ':')
    {
        return NULL;
    }
    cursor++;
    while (*cursor == ' ' || *cursor == '\t')
    {
        cursor++;
    }
    return cursor;
}

static uint8_t readString(const char *line, const char *key, char *out, size_t outSize)
{
    const char *value = findValue(line, key);
    if (value == NULL || *value != '"')
    {
        return 0;
    }
    value++;
    size_t length = 0;
    while (value[length] != '"' && value[length] != '\0' && length < outSize - 1)
    {
        out[length] = value[length];
        length++;
    }
    out[length] = '\0';
    return value[length] == '"';
}

static uint8_t readInt(const char *line, const char *key, long *out)
{
    const char *value = findValue(line, key);
    if (value == NULL)
    {
        return 0;
    }
    char *end = NULL;
    long result = strtol(value, &end, 10);
    if (end == value)
    {
        return 0;
    }
    *out = result;
    return 1;
}

// --- Replies ---

static void sendAll(int fd, const char *data, size_t length)
{
    while (length > 0)
    {
        ssize_t written = write(fd, data, length);
        if (written < 0)
        {
            if (errno == EINTR)
            {
                continue;
            }
            return; // Client went away; the read loop notices
        }
        data += written;
        length -= written;
    }
}

static void sendError(int fd, const char *message)
{
    char escaped[256];
    size_t length = 0;
    for (const char *c = message; *c != '\0' && length < sizeof(escaped) - 2; ++c)
    {
        if (*c == '"' || *c == '\\')
        {
            escaped[length++] = '\\';
        }
        escaped[length++] = *c;
    }
    escaped[length] = '\0';

    char reply[512];
    int replyLength = snprintf(reply, sizeof(reply), "{\"ok\": false, \"error\": \"%s\"}\n", escaped);
    sendAll(fd, reply, replyLength);
}

static uint8_t isMurphyAlive(void)
{
    if (bridgeIsLevelCompleted())
    {
        return 1;
    }
    // Death sets gShouldKillMurphy or starts the quit countdown without completing the level
    return sHasMurphyDied == 0 && gShouldKillMurphy == 0 && gQuitLevelCountdown == 0;
}

static int appendStatus(char *out, size_t size)
{
    return snprintf(out, size,
                    "\"level\": %u, \"tick\": %u, \"alive\": %s, \"won\": %s, \"over\": %s, "
                    "\"infotrons_left\": %u, \"murphy\": {\"x\": %d, \"y\": %d}, \"rng\": %u",
                    sLevelNumber,
                    sTick,
                    isMurphyAlive() ? "true" : "false",
                    bridgeIsLevelCompleted() ? "true" : "false",
                    sIsLevelOver ? "true" : "false",
                    gNumberOfRemainingInfotrons,
                    gMurphyLocation % kLevelWidth,
                    gMurphyLocation / kLevelWidth,
                    bridgeGetRandomGeneratorSeed());
}

// --- Commands ---

static void handleLoadLevel(int fd, const char *line)
{
    long level = 0;
    if (readInt(line, "level", &level) == 0 || level < 1 || level > kNumberOfLevels)
    {
        sendError(fd, "load_level needs \"level\" in 1..111");
        return;
    }
    long seed = sSeed;
    readInt(line, "seed", &seed);

    bridgeLoadLevel((uint8_t)level, (uint16_t)seed);
    sIsLevelLoaded = 1;
    sLevelNumber = (uint8_t)level;
    sIsLevelOver = 0;
    sHasMurphyDied = 0;
    sTick = 0;

    char reply[512];
    int length = snprintf(reply, sizeof(reply), "{\"ok\": true, \"seed\": %u, ", (uint16_t)seed);
    length += appendStatus(reply + length, sizeof(reply) - length);
    length += snprintf(reply + length, sizeof(reply) - length, "}\n");
    sendAll(fd, reply, length);
}

static void handleGetState(int fd)
{
    if (sIsLevelLoaded == 0)
    {
        sendError(fd, "no level loaded");
        return;
    }

    // 2 arrays of 1440 numbers up to 3 digits plus separators
    static char reply[kLevelSize * 8 + 1024];
    int length = snprintf(reply, sizeof(reply), "{\"ok\": true, ");
    length += appendStatus(reply + length, sizeof(reply) - length);
    length += snprintf(reply + length, sizeof(reply) - length,
                       ", \"gravity\": %s, \"width\": %d, \"height\": %d, \"tiles\": [",
                       gIsGravityEnabled ? "true" : "false", kLevelWidth, kLevelHeight);
    for (int i = 0; i < kLevelSize; ++i)
    {
        length += snprintf(reply + length, sizeof(reply) - length, i ? ",%u" : "%u", gCurrentLevelState[i].tile);
    }
    length += snprintf(reply + length, sizeof(reply) - length, "], \"states\": [");
    for (int i = 0; i < kLevelSize; ++i)
    {
        length += snprintf(reply + length, sizeof(reply) - length, i ? ",%u" : "%u", gCurrentLevelState[i].state);
    }
    length += snprintf(reply + length, sizeof(reply) - length, "]}\n");
    sendAll(fd, reply, length);
}

static void handleStep(int fd, const char *line)
{
    if (sIsLevelLoaded == 0)
    {
        sendError(fd, "no level loaded");
        return;
    }
    long input = 0;
    long ticks = 0;
    if (readInt(line, "input", &input) == 0 || input < 0 || input > 9)
    {
        sendError(fd, "step needs \"input\" in 0..9");
        return;
    }
    if (readInt(line, "n_ticks", &ticks) == 0 || ticks < 1 || ticks > kBridgeMaxTicksPerStep)
    {
        sendError(fd, "step needs \"n_ticks\" in 1..100000");
        return;
    }

    long ticksRun = 0;
    while (ticksRun < ticks && sIsLevelOver == 0 && gShouldExitGame == 0)
    {
        bridgeSetUserInput((uint8_t)input);
        if (runLevelIteration())
        {
            sIsLevelOver = 1;
        }
        if (isMurphyAlive() == 0)
        {
            sHasMurphyDied = 1;
        }
        sTick++;
        ticksRun++;
    }

    char reply[512];
    int length = snprintf(reply, sizeof(reply), "{\"ok\": true, \"ticks_run\": %ld, ", ticksRun);
    length += appendStatus(reply + length, sizeof(reply) - length);
    length += snprintf(reply + length, sizeof(reply) - length, "}\n");
    sendAll(fd, reply, length);
}

/// @return 1 if the bridge should shut down.
static uint8_t handleLine(int fd, const char *line)
{
    char command[32];
    if (readString(line, "cmd", command, sizeof(command)) == 0)
    {
        sendError(fd, "missing \"cmd\"");
        return 0;
    }

    if (strcmp(command, "load_level") == 0)
    {
        handleLoadLevel(fd, line);
    }
    else if (strcmp(command, "get_state") == 0)
    {
        handleGetState(fd);
    }
    else if (strcmp(command, "step") == 0)
    {
        handleStep(fd, line);
    }
    else if (strcmp(command, "quit") == 0)
    {
        sendAll(fd, "{\"ok\": true}\n", 13);
        return 1;
    }
    else
    {
        sendError(fd, "unknown cmd");
    }
    return gShouldExitGame != 0;
}

// --- Socket loop ---

/// Waits until fd is readable. Keeps the window responsive when not headless.
/// @return 1 when readable, 0 when the game was asked to quit.
static uint8_t waitForReadable(int fd)
{
    struct pollfd pfd = { fd, POLLIN, 0 };
    while (1)
    {
        if (sIsHeadless == 0)
        {
            handleSystemEvents();
        }
        if (gShouldExitGame != 0)
        {
            return 0;
        }
        int result = poll(&pfd, 1, sIsHeadless ? -1 : 50);
        if (result > 0)
        {
            return 1;
        }
        if (result < 0 && errno != EINTR)
        {
            return 0;
        }
    }
}

/// @return 1 if the bridge should shut down.
static uint8_t serveClient(int clientFd)
{
    static char buffer[kBridgeLineBufferSize];
    size_t used = 0;

    while (waitForReadable(clientFd))
    {
        ssize_t received = read(clientFd, buffer + used, sizeof(buffer) - 1 - used);
        if (received < 0 && errno == EINTR)
        {
            continue;
        }
        if (received <= 0)
        {
            return 0; // Client disconnected; wait for the next one
        }
        used += received;
        buffer[used] = '\0';

        char *lineStart = buffer;
        char *newline = NULL;
        while ((newline = strchr(lineStart, '\n')) != NULL)
        {
            *newline = '\0';
            if (newline > lineStart && handleLine(clientFd, lineStart))
            {
                return 1;
            }
            lineStart = newline + 1;
        }

        used = strlen(lineStart);
        memmove(buffer, lineStart, used);
        if (used == sizeof(buffer) - 1)
        {
            sendError(clientFd, "line too long");
            used = 0;
        }
    }
    return 1; // Window closed
}

int runBridge(void)
{
    signal(SIGPIPE, SIG_IGN);

    int serverFd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (serverFd < 0)
    {
        perror("bridge: socket");
        return 1;
    }

    struct sockaddr_un address;
    memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    strncpy(address.sun_path, sSocketPath, sizeof(address.sun_path) - 1);
    unlink(sSocketPath);

    if (bind(serverFd, (struct sockaddr *)&address, sizeof(address)) < 0 || listen(serverFd, 1) < 0)
    {
        perror("bridge: bind/listen");
        close(serverFd);
        return 1;
    }

    fprintf(stderr, "bridge: listening on %s (seed %u%s)\n", sSocketPath, sSeed, sIsHeadless ? ", headless" : "");

    uint8_t shouldQuit = 0;
    while (shouldQuit == 0 && waitForReadable(serverFd))
    {
        int clientFd = accept(serverFd, NULL, NULL);
        if (clientFd < 0)
        {
            if (errno == EINTR)
            {
                continue;
            }
            perror("bridge: accept");
            break;
        }
        shouldQuit = serveClient(clientFd);
        close(clientFd);
    }

    close(serverFd);
    unlink(sSocketPath);
    return 0;
}
