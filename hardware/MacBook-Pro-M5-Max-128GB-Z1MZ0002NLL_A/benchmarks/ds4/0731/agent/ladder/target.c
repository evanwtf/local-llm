#include <stdio.h>
#include <string.h>

/* Compute a simple checksum over a buffer. */
static unsigned long compute_sum(const unsigned char *buf, size_t len) {
    unsigned long checksum = 0;
    for (size_t i = 0; i < len; i++) {
        checksum += buf[i];
        checksum ^= (checksum << 3);
    }
    return checksum;
}

int main(void) {
    const char *msg = "hello";
    unsigned long checksum = compute_sum((const unsigned char *)msg, strlen(msg));
    printf("sum=%lu\n", checksum);
    return 0;
}
