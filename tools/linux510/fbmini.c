/*
 * fbmini - minimal fastboot client for the E850-96 Android 10 LK.
 *
 * The stock `fastboot` tool sends extra queries (getvar product, has-slot,
 * is-logical, ...) before flashing. This LK stops answering after some of
 * them, so fbmini only sends what it is told to:
 *
 *   fbmini getvar max-download-size
 *   fbmini flash boot boot.img        (download:<size> + data, then flash:boot)
 *   fbmini flash userdata rootfs.simg (sparse images are passed through as-is)
 *   fbmini reboot
 *   fbmini cmd <raw command>          (e.g. "oem ..."; use with care)
 *
 * Build (macOS, Homebrew libusb):
 *   clang -O2 -o fbmini fbmini.c -I/opt/homebrew/include/libusb-1.0 -L/opt/homebrew/lib -lusb-1.0
 */
#include <libusb.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define FB_CLASS 0xff
#define FB_SUBCLASS 0x42
#define FB_PROTO 0x03
#define CHUNK (1024 * 1024)

static libusb_device_handle *h;
static unsigned char ep_in, ep_out;
static int iface;

static int open_fastboot(void)
{
	libusb_device **list;
	ssize_t n = libusb_get_device_list(NULL, &list);
	for (ssize_t i = 0; i < n && !h; i++) {
		struct libusb_config_descriptor *cfg;
		if (libusb_get_active_config_descriptor(list[i], &cfg))
			continue;
		for (int j = 0; j < cfg->bNumInterfaces && !h; j++) {
			const struct libusb_interface_descriptor *d = &cfg->interface[j].altsetting[0];
			if (d->bInterfaceClass != FB_CLASS || d->bInterfaceSubClass != FB_SUBCLASS ||
			    d->bInterfaceProtocol != FB_PROTO)
				continue;
			for (int k = 0; k < d->bNumEndpoints; k++) {
				unsigned char a = d->endpoint[k].bEndpointAddress;
				if ((d->endpoint[k].bmAttributes & 3) != LIBUSB_TRANSFER_TYPE_BULK)
					continue;
				if (a & LIBUSB_ENDPOINT_IN)
					ep_in = a;
				else
					ep_out = a;
			}
			if (libusb_open(list[i], &h) == 0 && libusb_claim_interface(h, j) != 0) {
				libusb_close(h);
				h = NULL;
			}
			iface = j;
		}
		libusb_free_config_descriptor(cfg);
	}
	libusb_free_device_list(list, 1);
	return h ? 0 : -1;
}

static int xfer(unsigned char ep, void *buf, int len, int *got, unsigned int timeout_ms)
{
	return libusb_bulk_transfer(h, ep, buf, len, got, timeout_ms);
}

/* Read replies; print INFO lines; return 0 on OKAY, 1 on DATA (size in *data), -1 on FAIL. */
static int reply(unsigned int *data, unsigned int timeout_ms)
{
	char r[257];
	int n, rc;

	for (;;) {
		if ((rc = xfer(ep_in, r, 256, &n, timeout_ms))) {
			fprintf(stderr, "no reply: %s\n", libusb_error_name(rc));
			return -1;
		}
		r[n] = 0;
		printf("< %s\n", r);
		if (!strncmp(r, "INFO", 4) || !strncmp(r, "TEXT", 4))
			continue;
		if (!strncmp(r, "OKAY", 4))
			return 0;
		if (!strncmp(r, "DATA", 4)) {
			*data = strtoul(r + 4, NULL, 16);
			return 1;
		}
		return -1;
	}
}

static int command(const char *cmd, unsigned int *data, unsigned int timeout_ms)
{
	int n, rc;

	printf("> %s\n", cmd);
	if ((rc = xfer(ep_out, (void *)cmd, strlen(cmd), &n, 5000))) {
		fprintf(stderr, "send failed: %s\n", libusb_error_name(rc));
		return -1;
	}
	return reply(data, timeout_ms);
}

static int download(const char *path)
{
	FILE *f = fopen(path, "rb");
	unsigned char *buf;
	unsigned int size, want;
	char cmd[64];

	if (!f) {
		perror(path);
		return -1;
	}
	fseek(f, 0, SEEK_END);
	size = ftell(f);
	rewind(f);
	buf = malloc(CHUNK);
	snprintf(cmd, sizeof(cmd), "download:%08x", size);
	if (command(cmd, &want, 5000) != 1 || want != size) {
		fprintf(stderr, "download not accepted\n");
		return -1;
	}
	for (unsigned int done = 0; done < size;) {
		int len = fread(buf, 1, size - done < CHUNK ? size - done : CHUNK, f), n, rc;
		if ((rc = xfer(ep_out, buf, len, &n, 10000)) || n != len) {
			fprintf(stderr, "data failed at %u: %s\n", done, libusb_error_name(rc));
			return -1;
		}
		done += len;
		fprintf(stderr, "\r%u / %u", done, size);
	}
	fprintf(stderr, "\n");
	fclose(f);
	free(buf);
	return reply(&want, 30000) == 0 ? 0 : -1;	/* the OKAY that ends the data phase */
}

int main(int argc, char **argv)
{
	unsigned int d;
	char cmd[256];
	int rc = -1;

	if (argc < 2) {
		fprintf(stderr, "usage: fbmini getvar VAR | flash PART FILE | reboot | cmd RAW\n");
		return 2;
	}
	libusb_init(NULL);
	if (open_fastboot()) {
		fprintf(stderr, "no fastboot device\n");
		return 1;
	}
	if (!strcmp(argv[1], "getvar") && argc == 3) {
		snprintf(cmd, sizeof(cmd), "getvar:%s", argv[2]);
		rc = command(cmd, &d, 5000);
	} else if (!strcmp(argv[1], "flash") && argc == 4) {
		snprintf(cmd, sizeof(cmd), "flash:%s", argv[2]);
		rc = download(argv[3]);
		if (!rc)
			rc = command(cmd, &d, 600000);	/* writing a 3 GB sparse image takes a while */
	} else if (!strcmp(argv[1], "reboot")) {
		rc = command("reboot", &d, 5000);
	} else if (!strcmp(argv[1], "cmd") && argc == 3) {
		rc = command(argv[2], &d, 60000);
	}
	libusb_release_interface(h, iface);
	libusb_close(h);
	libusb_exit(NULL);
	return rc ? 1 : 0;
}
