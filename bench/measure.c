#define _GNU_SOURCE
#include <sys/resource.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <fcntl.h>

/* A tiny parent avoids contaminating child peak RSS with a large Python harness. */
int main(int argc, char **argv) {
    if (argc < 2) return 2;
    struct timespec start, end;
    clock_gettime(CLOCK_MONOTONIC, &start);
    pid_t child = fork();
    if (child < 0) return 2;
    if (child == 0) {
        int fd = open("/dev/null", O_WRONLY);
        if (fd < 0 || dup2(fd, STDOUT_FILENO) < 0) _exit(126);
        close(fd);
        execvp(argv[1], argv + 1);
        _exit(127);
    }
    int status;
    struct rusage usage;
    if (wait4(child, &status, 0, &usage) < 0) return 2;
    clock_gettime(CLOCK_MONOTONIC, &end);
    double wall = end.tv_sec - start.tv_sec + (end.tv_nsec - start.tv_nsec) / 1e9;
    int code = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
    printf("{\"wall_seconds\":%.9f,\"peak_rss_kib\":%ld,\"exit_code\":%d}\n",
           wall, usage.ru_maxrss, code);
    return 0;
}
