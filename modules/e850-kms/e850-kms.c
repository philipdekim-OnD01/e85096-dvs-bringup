// SPDX-License-Identifier: GPL-2.0
/*
 * e850-kms: a minimal KMS device (/dev/dri/card0) on top of the vendor fbdev DECON driver (dpu.ko),
 * so Xorg modesetting/glamor and Mesa kmsro (with panfrost on the kbase shim) can scan out GPU
 * buffers (e850 11.85). One CRTC, one XRGB8888 primary plane, one HDMI connector with fb0's mode.
 *
 * Buffers: GEM CMA helpers on the DSIM device, the one device of the DPU with an IOMMU (the one
 * DECON maps its own buffers through, decon_map_ion_handle). dma_alloc_wc() there gives pages
 * joined by the IOMMU, so cma_obj->paddr is already the DPU address (no CMA memory is used).
 *
 * Scanout: decon_pan_display() with fb0's smem_start swapped for the buffer's DPU address
 * (11.83 way (가)), the same path fbcon uses (11.84: FBIOPAN_DISPLAY flips work). It programs the
 * default window's DPP and waits for the vsync that latches it (~22 ms), so the flip event is sent
 * when it returns. Userspace S3CFB_WIN_CONFIG is not used: it frees fb0's buffer (11.84).
 * When X drops master or the pipe is disabled, fb0's own buffer is shown again (text console).
 *
 * DRM name "exynos": Mesa's DRI megadriver already has a kmsro entry point for that name.
 */
#include <linux/fb.h>
#include <linux/iommu.h>
#include <linux/module.h>
#include <drm/drm_atomic_helper.h>
#include <drm/drm_drv.h>
#include <drm/drm_fb_cma_helper.h>
#include <drm/drm_fourcc.h>
#include <drm/drm_gem_cma_helper.h>
#include <drm/drm_gem_framebuffer_helper.h>
#include <drm/drm_modes.h>
#include <drm/drm_probe_helper.h>
#include <drm/drm_simple_kms_helper.h>
#include <drm/drm_vblank.h>
#include "decon.h"

static struct {
	struct drm_device *drm;
	struct drm_simple_display_pipe pipe;
	struct drm_connector conn;
	struct fb_info *info;			/* fb0 = DECON's default window */
	struct fb_var_screeninfo var0;		/* fb0's mode at load */
} e;

/* Show 'dva' (DPU address of the first pixel row of a pitch-byte, h-row buffer) from (x, y). */
static int e850_show(dma_addr_t dva, unsigned int pitch, unsigned int h, unsigned int x, unsigned int y)
{
	struct fb_info *info = e.info;
	struct fb_var_screeninfo var = e.var0, saved;
	unsigned long smem;
	int ret;

	var.xres_virtual = pitch / 4;
	var.yres_virtual = h;
	var.xoffset = x;
	var.yoffset = y;
	lock_fb_info(info);
	saved = info->var;
	smem = info->fix.smem_start;
	info->fix.smem_start = dva;
	ret = decon_pan_display(&var, info);	/* copies var into info->var */
	info->fix.smem_start = smem;
	info->var = saved;
	unlock_fb_info(info);
	if (ret)
		drm_err_ratelimited(e.drm, "decon_pan_display: %d\n", ret);
	return ret;
}

static void e850_show_fb0(void)
{
	e850_show(e.info->fix.smem_start, e.info->fix.line_length, e.var0.yres_virtual,
		  e.var0.xoffset, e.var0.yoffset);
}

static enum drm_mode_status e850_mode_valid(struct drm_simple_display_pipe *pipe,
					    const struct drm_display_mode *mode)
{
	return mode->hdisplay == e.var0.xres && mode->vdisplay == e.var0.yres ? MODE_OK : MODE_BAD;
}

static void e850_update(struct drm_simple_display_pipe *pipe, struct drm_plane_state *old)
{
	struct drm_plane_state *ps = pipe->plane.state;
	struct drm_crtc *crtc = &pipe->crtc;
	struct drm_pending_vblank_event *event = crtc->state->event;

	if (crtc->state->active && ps->fb) {
		struct drm_gem_cma_object *obj = drm_fb_cma_get_gem_obj(ps->fb, 0);

		e850_show(obj->paddr + ps->fb->offsets[0], ps->fb->pitches[0], ps->fb->height,
			  ps->src_x >> 16, ps->src_y >> 16);
	}
	/* shortcut: the event goes out only from plane updates; commits that touch the CRTC alone
	 * (none exist here: no gamma, no properties) would time out on flip_done */
	if (event) {
		crtc->state->event = NULL;
		spin_lock_irq(&crtc->dev->event_lock);
		drm_crtc_send_vblank_event(crtc, event);
		spin_unlock_irq(&crtc->dev->event_lock);
	}
}

static void e850_disable(struct drm_simple_display_pipe *pipe)
{
	e850_show_fb0();
}

static const struct drm_simple_display_pipe_funcs e850_pipe_funcs = {
	.mode_valid = e850_mode_valid,
	.update = e850_update,
	.disable = e850_disable,
	.prepare_fb = drm_gem_fb_simple_display_pipe_prepare_fb,
};

static int e850_get_modes(struct drm_connector *conn)
{
	struct drm_display_mode *m = drm_cvt_mode(conn->dev, e.var0.xres, e.var0.yres, 60,
						  false, false, false);

	if (!m)
		return 0;
	m->type |= DRM_MODE_TYPE_DRIVER | DRM_MODE_TYPE_PREFERRED;
	drm_mode_probed_add(conn, m);
	return 1;
}

static const struct drm_connector_helper_funcs e850_conn_helper = {
	.get_modes = e850_get_modes,
};

static const struct drm_connector_funcs e850_conn_funcs = {
	.fill_modes = drm_helper_probe_single_connector_modes,
	.destroy = drm_connector_cleanup,
	.reset = drm_atomic_helper_connector_reset,
	.atomic_duplicate_state = drm_atomic_helper_connector_duplicate_state,
	.atomic_destroy_state = drm_atomic_helper_connector_destroy_state,
};

static const struct drm_mode_config_funcs e850_mode_config_funcs = {
	.fb_create = drm_gem_fb_create,
	.atomic_check = drm_atomic_helper_check,
	.atomic_commit = drm_atomic_helper_commit,
};

/* X left the VT (or exited): give the screen back to the text console in fb0 */
static void e850_master_drop(struct drm_device *dev, struct drm_file *file)
{
	e850_show_fb0();
}

DEFINE_DRM_GEM_CMA_FOPS(e850_fops);

static struct drm_driver e850_driver = {
	.driver_features = DRIVER_MODESET | DRIVER_GEM | DRIVER_ATOMIC,
	.master_drop = e850_master_drop,
	.fops = &e850_fops,
	DRM_GEM_CMA_DRIVER_OPS,
	.name = "exynos",
	.desc = "E850-96 DECON through fbdev pan (e850-kms)",
	.date = "20261122",
	.major = 1,
	.minor = 0,
};

static const u32 e850_formats[] = { DRM_FORMAT_XRGB8888 };

static int __init e850_kms_init(void)
{
	struct decon_device *decon = decon_drvdata[0];
	struct dsim_device *dsim;
	struct iommu_domain *domain;
	struct drm_device *drm;
	int ret;

	if (!decon || decon->dt.out_type != DECON_OUT_DSI || !decon->out_sd[0])
		return -ENODEV;
	dsim = v4l2_get_subdevdata(decon->out_sd[0]);
	e.info = decon->win[decon->dt.dft_win]->fbinfo;
	if (!dsim || !dsim->dev || !e.info)
		return -ENODEV;
	/* paddr is a DPU address only if dma_alloc goes through the DSIM IOMMU (iommu-dma) */
	domain = iommu_get_domain_for_dev(dsim->dev);
	if (!domain || domain->type != IOMMU_DOMAIN_DMA) {
		pr_err("e850-kms: %s has no DMA IOMMU domain\n", dev_name(dsim->dev));
		return -ENODEV;
	}
	e.var0 = e.info->var;
	if (e.var0.bits_per_pixel != 32)
		return -ENODEV;

	drm = drm_dev_alloc(&e850_driver, dsim->dev);
	if (IS_ERR(drm))
		return PTR_ERR(drm);
	e.drm = drm;
	ret = drmm_mode_config_init(drm);
	if (ret)
		goto err;
	drm->mode_config.min_width = e.var0.xres;
	drm->mode_config.min_height = e.var0.yres;
	drm->mode_config.max_width = 4096;
	drm->mode_config.max_height = 4096;
	drm->mode_config.preferred_depth = 24;
	drm->mode_config.prefer_shadow = 1;	/* scanout memory is write-combined: CPU reads are slow */
	drm->mode_config.funcs = &e850_mode_config_funcs;

	drm_connector_helper_add(&e.conn, &e850_conn_helper);
	ret = drm_connector_init(drm, &e.conn, &e850_conn_funcs, DRM_MODE_CONNECTOR_HDMIA);
	if (ret)
		goto err;
	ret = drm_simple_display_pipe_init(drm, &e.pipe, &e850_pipe_funcs, e850_formats,
					   ARRAY_SIZE(e850_formats), NULL, &e.conn);
	if (ret)
		goto err;
	drm_mode_config_reset(drm);
	ret = drm_dev_register(drm, 0);
	if (ret)
		goto err;
	drm_info(drm, "%ux%u on DECON window %d, buffers via %s\n", e.var0.xres, e.var0.yres,
		 decon->dt.dft_win, dev_name(dsim->dev));
	return 0;
err:
	drm_dev_put(drm);
	return ret;
}

static void __exit e850_kms_exit(void)
{
	drm_dev_unregister(e.drm);
	drm_atomic_helper_shutdown(e.drm);
	e850_show_fb0();
	drm_dev_put(e.drm);
}

module_init(e850_kms_init);
module_exit(e850_kms_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("E850-96: KMS device on the vendor fbdev DECON (pan to DRM buffers)");
