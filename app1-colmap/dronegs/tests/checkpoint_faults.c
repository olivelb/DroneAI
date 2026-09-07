// SPDX-License-Identifier: MIT
// Qualification-only LD_PRELOAD fault injection; never linked into DroneGS.
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <stdio.h>

int rename(const char *from, const char *to) {
    const char *fault=getenv("DRONEGS_TEST_IO_FAULT");
    const size_t n=strlen(from);
    if(fault && !strcmp(fault,"rename") && strstr(from,"fault-") && n>=4 && !strcmp(from+n-4,".tmp")) {
        errno=EIO;return -1;
    }
    int (*real_rename)(const char*,const char*)=dlsym(RTLD_NEXT,"rename");
    if(!real_rename){errno=EIO;return -1;}
    return real_rename(from,to);
}
int fsync(int fd) {
    const char *fault=getenv("DRONEGS_TEST_IO_FAULT");
    if(fault && !strcmp(fault,"fsync")){errno=EIO;return -1;}
    int (*real_fsync)(int)=dlsym(RTLD_NEXT,"fsync");
    if(!real_fsync){errno=EIO;return -1;}
    return real_fsync(fd);
}
