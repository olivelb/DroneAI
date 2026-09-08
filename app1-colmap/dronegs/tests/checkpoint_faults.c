// SPDX-License-Identifier: MIT
// Qualification-only LD_PRELOAD fault injection; never linked into DroneGS.
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <stdio.h>
#include <fcntl.h>
#include <stdarg.h>
#include <sys/stat.h>

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
    struct stat info;
    if(fault && (!strcmp(fault,"fsync") ||
       (!strcmp(fault,"directory-fsync") && !fstat(fd,&info) && S_ISDIR(info.st_mode)))) {
        errno=EIO;return -1;
    }
    int (*real_fsync)(int)=dlsym(RTLD_NEXT,"fsync");
    if(!real_fsync){errno=EIO;return -1;}
    return real_fsync(fd);
}

int open(const char *path, int flags, ...) {
    const char *fault=getenv("DRONEGS_TEST_IO_FAULT");
    if(fault && !strcmp(fault,"directory-open") && (flags & O_DIRECTORY)) {
        errno=EACCES;return -1;
    }
    int (*real_open)(const char*,int,...)=dlsym(RTLD_NEXT,"open");
    if(!real_open){errno=EIO;return -1;}
    if((flags & O_CREAT) || (flags & O_TMPFILE)==O_TMPFILE) {
        va_list args;va_start(args,flags);
        mode_t mode=va_arg(args,mode_t);va_end(args);
        return real_open(path,flags,mode);
    }
    return real_open(path,flags);
}
