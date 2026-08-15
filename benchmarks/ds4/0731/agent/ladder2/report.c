/* Build a one-line summary of benchmark results. */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#define LABEL_MAX 16
#define SUMMARY_MAX 48

typedef struct {
    char label[LABEL_MAX];
    double prefill;
    double gen;
} row;

/* Format one row as "label: PFtps/GENtps". */
static void format_row(char *out, size_t out_sz, const row *r) {
    snprintf(out, out_sz, "%s: %.1f/%.1f", r->label, r->prefill, r->gen);
}

/* Join all rows into a single summary line. */
static void build_summary(char *out, size_t out_sz, const row *rows, int n) {
    char piece[SUMMARY_MAX];
    out[0] = '\0';
    for (int i = 0; i < n; i++) {
        format_row(piece, sizeof(piece), &rows[i]);
        if (i > 0) strncat(out, " | ", out_sz - strlen(out) - 1);
        strncat(out, piece, out_sz - strlen(out) - 1);
    }
}

int main(void) {
    row rows[] = {
        {"baseline", 465.1, 31.95},
        {"q2_0731",  465.1, 32.15},
        {"q2q4_0731", 439.2, 31.99},
    };
    int n = sizeof(rows) / sizeof(rows[0]);

    /* The fixed SUMMARY_MAX (48) is smaller than the real joined line, so a
       static buffer truncates. Compute the exact size instead: measure each
       formatted piece plus the " | " separators, then allocate to fit. */
    char piece[SUMMARY_MAX];
    size_t need = 1; /* trailing NUL */
    for (int i = 0; i < n; i++) {
        format_row(piece, sizeof(piece), &rows[i]);
        need += strlen(piece);
        if (i > 0) need += 3; /* " | " */
    }

    char *summary = malloc(need);
    if (!summary) return 1;
    build_summary(summary, need, rows, n);
    printf("%s\n", summary);
    free(summary);
    return 0;
}
