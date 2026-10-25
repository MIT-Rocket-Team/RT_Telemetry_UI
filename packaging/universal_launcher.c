/* Select an intact native PyInstaller app, preserving its resources and runtime. */
#include <errno.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#if defined(__arm64__)
#define PAYLOAD_ARCH "arm64"
#elif defined(__x86_64__)
#define PAYLOAD_ARCH "x86_64"
#else
#error Unsupported macOS architecture
#endif

int main(int argc, char **argv) {
    (void)argc;
    uint32_t capacity = 0;
    _NSGetExecutablePath(NULL, &capacity);
    char *unresolved = malloc(capacity);
    if (unresolved == NULL || _NSGetExecutablePath(unresolved, &capacity) != 0) {
        fprintf(stderr, "Rocket GNC Monitor: cannot locate the application launcher.\n");
        free(unresolved);
        return 126;
    }
    char *executable = realpath(unresolved, NULL);
    free(unresolved);
    if (executable == NULL) {
        perror("Rocket GNC Monitor: cannot resolve the application path");
        return 126;
    }
    char *separator = strrchr(executable, '/');
    if (separator == NULL) {
        free(executable);
        return 126;
    }
    *separator = '\0';
    const char *relative = "/../Helpers/RocketGNCMonitor-" PAYLOAD_ARCH
        ".app/Contents/MacOS/RocketGNCMonitor-v0a";
    size_t length = strlen(executable) + strlen(relative) + 1;
    char *payload = malloc(length);
    if (payload == NULL) {
        free(executable);
        return 126;
    }
    snprintf(payload, length, "%s%s", executable, relative);
    free(executable);
    argv[0] = payload;
    /* No shell, child process, cwd change, or event loop between Finder and Qt. */
    execv(payload, argv);
    int launch_error = errno;
    fprintf(stderr, "Rocket GNC Monitor: cannot start the %s application: %s\n",
            PAYLOAD_ARCH, strerror(launch_error));
    free(payload);
    return launch_error == ENOENT ? 127 : 126;
}
