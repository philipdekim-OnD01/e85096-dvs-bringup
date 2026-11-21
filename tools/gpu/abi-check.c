/* Compare the kbase-g52 drm-shim kbase ABI with the r38p1 kernel uapi (compile-time + print). */
#include <stdio.h>
#include <stdint.h>
#include <stddef.h>
#include <sys/ioctl.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32; typedef uint64_t u64;
typedef int32_t s32; typedef int64_t s64;
#include "mali-ioctl.h"  /* from src/panfrost/drm-shim */
#include "mali-ioctl-dvalin.h"
#define MALI_USE_CSF 0
#include <linux/types.h>
#include "mali_kbase_ioctl.h"
#include "mali_base_kernel.h"
#define EQ(a, b) do { int ok = (unsigned long)(a) == (unsigned long)(b); bad += !ok; \
    printf("%-4s %-40s 0x%08lx 0x%08lx\n", ok ? "ok" : "BAD", #a, (unsigned long)(a), (unsigned long)(b)); } while (0)
int main(void) {
    int bad = 0;
    EQ(MALI_IOCTL_GET_VERSION, KBASE_IOCTL_VERSION_CHECK);
    EQ(MALI_IOCTL_SET_FLAGS, KBASE_IOCTL_SET_FLAGS);
    EQ(MALI_IOCTL_JOB_SUBMIT, KBASE_IOCTL_JOB_SUBMIT);
    EQ(MALI_IOCTL_GET_GPUPROPS, KBASE_IOCTL_GET_GPUPROPS);
    EQ(MALI_IOCTL_MEM_ALLOC, KBASE_IOCTL_MEM_ALLOC);
    EQ(MALI_IOCTL_MEM_FREE, KBASE_IOCTL_MEM_FREE);
    EQ(MALI_IOCTL_SYNC, KBASE_IOCTL_MEM_SYNC);
    /* MEM_IMPORT differs (shim struct 40 bytes, kernel 24): unused by the shim, needed for dma-buf import later */
    EQ(sizeof(struct mali_jd_atom_v2), offsetof(struct base_jd_atom_v2, renderpass_id));
    EQ(offsetof(struct mali_jd_atom_v2, core_req), offsetof(struct base_jd_atom_v2, core_req));
    EQ(offsetof(struct mali_jd_atom_v2, atom_number), offsetof(struct base_jd_atom_v2, atom_number));
    EQ(sizeof(struct mali_jd_event_v2), sizeof(struct base_jd_event_v2));
    EQ(MALI_MEM_PROT_CPU_RD, BASE_MEM_PROT_CPU_RD);
    EQ(MALI_MEM_PROT_CPU_WR, BASE_MEM_PROT_CPU_WR);
    EQ(MALI_MEM_PROT_GPU_RD, BASE_MEM_PROT_GPU_RD);
    EQ(MALI_MEM_PROT_GPU_WR, BASE_MEM_PROT_GPU_WR);
    EQ(MALI_MEM_PROT_GPU_EX, BASE_MEM_PROT_GPU_EX);
    EQ(MALI_MEM_GROW_ON_GPF, BASE_MEM_GROW_ON_GPF);
    EQ(MALI_MEM_COHERENT_LOCAL, BASE_MEM_COHERENT_LOCAL);
    EQ(MALI_MEM_CACHED_CPU, BASE_MEM_CACHED_CPU);
    EQ(MALI_MEM_SAME_VA, BASE_MEM_SAME_VA);
    EQ(0x3000, BASE_MEM_MAP_TRACKING_HANDLE);
    EQ(1, BASE_JD_REQ_FS); EQ(2, BASE_JD_REQ_CS);
    EQ(1, BASE_JD_EVENT_DONE);
    printf("%d mismatches\n", bad);
    return bad != 0;
}
