// SPDX-License-Identifier: GPL-2.0
/*
 * Count what the CSIS link ISR sees, without touching CSIS registers: a kretprobe on
 * csi_hw_g_irq_src() (fimc-is.ko) tallies the decoded irq_src. Load before streaming,
 * rmmod after: the totals go to dmesg.
 *   insmod csis-irqstat.ko; <capture>; rmmod csis-irqstat
 * The running totals (VC0 bits) are also read-only parameters, for the live page's MIPI graphs (11.51):
 *   /sys/module/csis_irqstat/parameters/{calls,fs,fe,line,err,err_bits}
 * err_bits: per bit of err_id[0..3] (enum csis_hw_err_id: 1 CRC, 4 OVERFLOW, 5/6 LOST_FE/FS, 9 DMAFIFO_FULL ...),
 * decoded from INT_SRC0 only. err (err_flag) also counts INT_SRC1, which fires once a DVS frame: the per-VC
 * VRESOL status (11.22, 11.51), so err is ~fs and err_bits are the link errors.
 */
#include <linux/module.h>
#include <linux/kprobes.h>
#include <linux/atomic.h>

/* Copy of struct csis_irq_src (camera/include/is-hw.h). */
struct csis_irq_src {
	u32 otf_start, otf_end, dma_start, dma_end, line_end, dma_abort, dma_qreqn;
	bool err_flag;
	u32 err_id[4];
};

static atomic_t calls, fs, fe, line, err;
static int err_bits[32];			/* atomic_t is { int }: atomic_inc() on these, ints for the param */

static int entry(struct kretprobe_instance *ri, struct pt_regs *regs)
{
	*(struct csis_irq_src **)ri->data = (struct csis_irq_src *)regs->regs[1];
	return 0;
}

static int ret(struct kretprobe_instance *ri, struct pt_regs *regs)
{
	struct csis_irq_src *s = *(struct csis_irq_src **)ri->data;
	unsigned long e = s->err_id[0] | s->err_id[1] | s->err_id[2] | s->err_id[3];
	int b;

	atomic_inc(&calls);
	if (s->otf_start & 1)
		atomic_inc(&fs);
	if (s->otf_end & 1)
		atomic_inc(&fe);
	if (s->line_end & 1)
		atomic_inc(&line);
	if (s->err_flag)
		atomic_inc(&err);
	for_each_set_bit(b, &e, 32)
		atomic_inc((atomic_t *)&err_bits[b]);
	return 0;
}

module_param_named(calls, calls.counter, int, 0444);
module_param_named(fs, fs.counter, int, 0444);
module_param_named(fe, fe.counter, int, 0444);
module_param_named(line, line.counter, int, 0444);
module_param_named(err, err.counter, int, 0444);
module_param_array(err_bits, int, NULL, 0444);

static struct kretprobe krp = {
	.kp.symbol_name = "csi_hw_g_irq_src",
	.entry_handler = entry,
	.handler = ret,
	.data_size = sizeof(void *),
	.maxactive = 16,
};

static int __init irqstat_init(void)
{
	return register_kretprobe(&krp);
}

static void __exit irqstat_exit(void)
{
	int b;

	unregister_kretprobe(&krp);
	pr_info("csis-irqstat: calls %d fs %d fe %d line %d err %d missed %d\n",
		atomic_read(&calls), atomic_read(&fs), atomic_read(&fe), atomic_read(&line),
		atomic_read(&err), krp.nmissed);
	for (b = 0; b < 32; b++)
		if (READ_ONCE(err_bits[b]))
			pr_info("csis-irqstat: err_id bit %d: %d\n", b, READ_ONCE(err_bits[b]));
}

module_init(irqstat_init);
module_exit(irqstat_exit);
MODULE_LICENSE("GPL");
