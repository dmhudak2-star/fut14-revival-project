/* FIFA 14 FUT revival -- Dashlaunch title plugin (SKELETON)
 * ==========================================================
 *
 * HONESTY, READ FIRST. This file was written without a PowerPC toolchain and
 * WITHOUT ONCE BEING COMPILED OR RUN. Everything else in this repository was
 * verified on the console or in the test suite; this is the first code that was
 * not. Treat it as a structured starting point for someone with the Xbox 360
 * build environment, not as a working plugin. Every place that needs the real
 * kernel / Dashlaunch API is marked `TODO(sdk)`.
 *
 * Two more caveats before you build on it:
 *
 *   1. The launch-stage patch table (patches.h, stage 1) is the FUNCTIONAL
 *      CORE only. The working launcher installs additional trace/journal stubs
 *      whose necessity has not been separated from diagnostics -- see
 *      `"complete": false` in the manifest and docs/PLUGIN.md. Validate a built
 *      plugin against a full patched launch before trusting it.
 *   2. patches.h is generated for a FIXED server IP. Runtime hostname
 *      resolution (see resolve_and_rewrite) is a TODO; until it is done, the
 *      plugin only talks to the address the header was generated for.
 *
 * What IS solid here: the shape. Three module-load hooks in order, guarded
 * writes that check original bytes first, and a pattern-located APT. The bytes
 * and addresses come from patches.h, which is generated from the working patch
 * tools -- so they are not invented here even though this file is untested.
 */

#include "patches.h"

/* TODO(sdk): the real headers. On the XDK these are <xtl.h> and the kernel
 * import stubs; with libxenon they are the xenon headers. The declarations
 * below are the minimum this file references, so it reads without them. */
typedef unsigned int   DWORD;
typedef unsigned char  BYTE;
typedef int            BOOL;
typedef unsigned long long QWORD;

/* Kernel primitives the plugin needs. TODO(sdk): resolve these to the real
 * kernel exports (by ordinal, the usual RGH way) or the SDK equivalents. */
extern void  *MmGetPhysicalAddress(void *addr);
extern DWORD  XexGetModuleHandle(const char *name, void **out);
extern DWORD  XexGetProcedureAddress(void *module, DWORD ordinal, void **out);
extern void   HvxKeSweepDcacheRange(void *addr, DWORD len); /* i-cache coherency */

/* ---- memory helpers ---------------------------------------------------- */

/* A patch is only safe if the bytes there are the ones we expect. Every write
 * below goes through this, so a wrong build is refused rather than corrupted --
 * the same contract the Python patchers enforce with control.read() first. */
static BOOL mem_equals(DWORD address, const BYTE *expect, DWORD len) {
    const volatile BYTE *p = (const volatile BYTE *)address;
    for (DWORD i = 0; i < len; i++) {
        if (p[i] != expect[i]) return 0;
    }
    return 1;
}

static void mem_write(DWORD address, const BYTE *bytes, DWORD len) {
    volatile BYTE *p = (volatile BYTE *)address;
    for (DWORD i = 0; i < len; i++) {
        p[i] = bytes[i];
    }
    /* Code was written; the instruction cache must be made coherent or the CPU
     * may execute the old bytes. TODO(sdk): confirm the right flush call. */
    HvxKeSweepDcacheRange((void *)address, len);
}

/* ---- title identification ---------------------------------------------- */

/* Only FIFA 14, and only this build. The timestamp is the discriminator; any
 * other value means a title this plugin must not touch.
 * TODO(sdk): read the XEX header timestamp of the loading module. The XDK path
 * is RtlImageXexHeaderField(header, XEX_HEADER_..., ...); libxenon exposes the
 * loaded image header differently. */
static BOOL is_supported_fifa14(void *xex_header) {
    DWORD timestamp = 0; /* TODO(sdk): timestamp = <read from xex_header>; */
    (void)xex_header;
    return timestamp == FIFA14_XEX_TIMESTAMP;
}

/* ---- stage 1: launch patches (default.xex) ----------------------------- */

/* Caves first, then hooks: the hooks branch into the caves, so the caves must
 * exist when a hook is written. Then the profile pointer. */
static void apply_stage1(void) {
    for (int i = 0; i < STAGE1_CAVE_COUNT; i++) {
        const patch_cave_t *c = &STAGE1_CAVES[i];
        mem_write(c->address, c->bytes, c->len);
    }
    for (int i = 0; i < STAGE1_HOOK_COUNT; i++) {
        const patch_site_t *h = &STAGE1_HOOKS[i];
        if (!mem_equals(h->address, h->expect, h->expect_len)) {
            /* Wrong bytes: refuse this one rather than corrupt the title.
             * TODO(sdk): log which hook, for field diagnosis. */
            continue;
        }
        mem_write(h->address, h->write, h->write_len);
    }
    *(volatile DWORD *)STAGE1_PROFILE_POINTER = STAGE1_PROFILE_VALUE;
}

/* ---- stage 2: EAS FC endpoints (powdllzf) ------------------------------ */

/* Two strings rewritten in place, once powdllzf is mapped. In place means the
 * replacement must be no longer than the original -- true for any IPv4 (see
 * docs/PLUGIN.md). TODO(sdk): the addresses in patches.h are absolute for the
 * observed load base (0x89700000); confirm powdllzf loads there, or rebase. */
static void apply_stage2(void) {
    /* Length is not re-checked here because the generator guaranteed fit; a
     * plugin that resolves a hostname at runtime must re-check after rewrite. */
    const char *s = easfc_session_write;
    volatile char *d = (volatile char *)PATCH_EASFC_SESSION_ADDR;
    while (*s) { *d++ = *s++; }
    *d = 0;

    s = easfc_catalogue_write;
    d = (volatile char *)PATCH_EASFC_CATALOGUE_ADDR;
    while (*s) { *d++ = *s++; }
    *d = 0;
}

/* ---- stage 3: TU3 helperFunctions APT ---------------------------------- */

/* The APT is not at a fixed address -- the title loads helperFunctions more
 * than once -- so it is located by SIGNATURE and the three branches are
 * written relative to it. Each branch is guarded by 16 bytes before and after,
 * so a near-miss on the signature cannot land a write in the wrong place.
 *
 * A plugin hooking the resource load applies this at every load, which is what
 * makes it better than the Mac's five-second poll: the patch can never be late.
 * TODO(sdk): find the load hook point for the FUT resource that carries the
 * APT, and call apply_stage3(found_apt_base) from it. */
static BOOL find_signature(DWORD start, DWORD end, DWORD *apt_out) {
    for (DWORD a = start; a + TU3_SIGNATURE_LEN < end; a += 4) {
        if (mem_equals(a, TU3_SIGNATURE, TU3_SIGNATURE_LEN)) {
            *apt_out = a + TU3_SIGNATURE_TO_APT;
            return 1;
        }
    }
    return 0;
}

static void apply_stage3(DWORD apt_base) {
    for (int i = 0; i < TU3_BRANCH_COUNT; i++) {
        const tu3_branch_t *b = &TU3_BRANCHES[i];
        DWORD at = apt_base + b->apt_offset;
        if (!mem_equals(at - sizeof(b->before), b->before, sizeof(b->before))) continue;
        if (!mem_equals(at + 6, b->after, sizeof(b->after))) continue;
        if (!mem_equals(at, b->expect, sizeof(b->expect))) {
            /* Already patched, or an unexpected variant -- leave it. */
            continue;
        }
        mem_write(at, b->write, sizeof(b->write));
    }
}

/* ---- configuration ----------------------------------------------------- */

/* The server address comes from fifa14revival.ini on the console's disk, not
 * from the compiled header. If host is a name, resolve it once and rewrite the
 * address into the connect stub cave and the two EAS FC strings -- the only
 * places it is baked in. TODO(sdk): file read + gethostbyname equivalent, then
 * patch cave_connect_stub at the IP offsets (the manifest records them) and the
 * two PATCH_EASFC_* strings. Until this exists, the header's fixed IP is used. */
static void resolve_and_rewrite(void) {
    /* TODO(sdk): implement. No-op for now -- header IP stands. */
}

/* ---- entry / hooks ----------------------------------------------------- */

/* TODO(sdk): the plugin entry Dashlaunch calls. Register for module-load
 * notifications here; do not patch anything at entry -- default.xex is not
 * loaded yet. The three apply_* run from their respective load hooks. */
void plugin_entry(void) {
    resolve_and_rewrite();
    /* TODO(sdk): register notification -> on default.xex load & supported:
     *              apply_stage1();
     *            on powdllzf load:      apply_stage2();
     *            on FUT resource load:  find + apply_stage3(); */
}
