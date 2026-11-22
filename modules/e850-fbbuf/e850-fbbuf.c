// SPDX-License-Identifier: GPL-2.0
/*
 * /dev/e850-fbbuf: hand out fb0's buffer (a dma-heap "system-uncached" dma-buf owned by the vendor
 * DECON driver) as a dma-buf fd, so the GPU (kbase UMM import) can render straight into the
 * framebuffer and FBIOPAN_DISPLAY can show it (e850 11.84). Nothing in DECON changes: the fd only
 * adds a reference. Userspace S3CFB_WIN_CONFIG is not usable for this: its first call releases the
 * buffers of the previous frame, and before any win_config that is fb0's own buffer (11.84).
 */
#include <linux/dma-buf.h>
#include <linux/fb.h>
#include <linux/fcntl.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include "decon.h"

#define E850_FBBUF_GET_FD _IO('F', 0xe8)

static long fbbuf_ioctl(struct file *file, unsigned int cmd, unsigned long arg)
{
	/* shortcut: registered_fb[0] without get_fb_info(); dpu.ko is never unloaded on this board */
	struct fb_info *info = registered_fb[0];
	struct decon_win *win;
	struct dma_buf *buf;
	int fd;

	if (cmd != E850_FBBUF_GET_FD)
		return -ENOTTY;
	/* fb0 is DECON's default window (its par points back at it). Not fb_mmap == decon_mmap:
	 * with CFI_CLANG the two modules see different jump-table addresses for the function. */
	if (!info)
		return -ENODEV;
	win = info->par;
	if (!win || win->fbinfo != info)
		return -ENODEV;
	buf = win->dma_buf_data[0].dma_buf;
	if (!buf || buf->size < info->fix.smem_len)
		return -ENODEV;
	get_dma_buf(buf);
	fd = dma_buf_fd(buf, O_CLOEXEC);
	if (fd < 0)
		dma_buf_put(buf);
	return fd;
}

static const struct file_operations fbbuf_fops = {
	.owner = THIS_MODULE,
	.unlocked_ioctl = fbbuf_ioctl,
};

static struct miscdevice fbbuf_dev = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "e850-fbbuf",
	.fops = &fbbuf_fops,
	.mode = 0600, /* load.sh gives it fb0's group */
};

module_misc_device(fbbuf_dev);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("E850-96: fb0 buffer as a dma-buf fd for GPU rendering");
