// SPDX-License-Identifier: GPL-2.0
#include <linux/delay.h>
#include <linux/firmware.h>
#include <linux/gpio/consumer.h>
#include <linux/i2c.h>
#include <linux/jiffies.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/property.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/sysfs.h>
#include <linux/workqueue.h>

#define DVS_RETRY_INITIAL_MS 2000U
#define DVS_RETRY_MAX_MS 30000U

// makes i2c address
#define MAKE_I2C_ADDR(addr) {(addr) >> 8, (addr) & 0xff}
#define I2C_READ(dev_addr, reg_addr, dst, count)                               \
  {.addr = (dev_addr), .flags = !I2C_M_RD, .len = 2, .buf = (reg_addr)}, {     \
    .addr = (dev_addr), .flags = I2C_M_RD, .len = (count), .buf = (dst)        \
  }
#define I2C_WRITE(dev_addr, reg_addr, ...)                                     \
  {.addr = (dev_addr),                                                         \
   .flags = !I2C_M_RD,                                                         \
   .len = sizeof((u8[]){(reg_addr)[0], (reg_addr)[1], __VA_ARGS__}),           \
   .buf = ((u8[]){(reg_addr)[0], (reg_addr)[1], __VA_ARGS__})}

#define DVS_ADDR_PAD_CONTROL 0x303B
#define DVS_ADDR_MODE_SELECT 0x0100
// OUTIF lane mode: [1] use_2_lane, [0] use_1_lane, neither = 4 lanes
#define DVS_ADDR_OUTIF_LANES 0x3915
#define DVS_OUTIF_LANE_MASK 0x03

struct dvs_data {
  struct i2c_client *client;
  struct gpio_desc *reset_gpio;
  struct delayed_work check_work;

  struct mutex lock;
  unsigned int retry_delay_ms;
  bool stopping;
  // This represents software standby state
  bool ready;
  // Register selected by the last write to the "reg" attribute
  u16 sel_reg;
  // MIPI data lanes wired to the SoC ("nrv,data-lanes": 1, 2 or 4)
  u32 data_lanes;
};

static u8 dvs_outif_lane_bits(u32 lanes) {
  return lanes == 1 ? 0x01 : lanes == 2 ? 0x02 : 0x00;
}

static void dvs_check_work(struct work_struct *work) {
  struct dvs_data *dvs =
      container_of(to_delayed_work(work), struct dvs_data, check_work);

  mutex_lock(&dvs->lock);

  if (dvs->stopping)
    goto out;

  // Drive RSTN high before asserting reset.
  gpiod_set_value_cansleep(dvs->reset_gpio, 0);
  // Assert reset by driving the active-low RSTN pin low.
  gpiod_set_value_cansleep(dvs->reset_gpio, 1);
  // Hold RESET for 100 us during this bring-up test.
  udelay(100);
  // Release reset by driving RSTN high to enter software standby.
  gpiod_set_value_cansleep(dvs->reset_gpio, 0);
  // Wait at least t2 (10 us) before the first I2C transaction.
  udelay(10);

  static u8 initial_value = 0xAA; /* 0b10101010 */
  u8 readback_value = -1;

  u8 address[] = MAKE_I2C_ADDR(DVS_ADDR_PAD_CONTROL);
  int ret = 0;
  struct i2c_msg write_reg_msg[] = {
      I2C_WRITE(dvs->client->addr, address, initial_value)};
  ret = i2c_transfer(dvs->client->adapter, write_reg_msg,
                     ARRAY_SIZE(write_reg_msg));
  if (ret != ARRAY_SIZE(write_reg_msg)) {
    dev_err(&dvs->client->dev, "Failed to send i2c\n");
    goto retry;
  }
  struct i2c_msg readback_reg_msg[] = {I2C_READ(
      dvs->client->addr, address, &readback_value, sizeof(readback_value))};
  ret = i2c_transfer(dvs->client->adapter, readback_reg_msg,
                     ARRAY_SIZE(readback_reg_msg));
  if (ret != ARRAY_SIZE(readback_reg_msg)) {
    dev_err(&dvs->client->dev, "Failed to recv i2c\n");
    goto retry;
  }

  dev_dbg(&dvs->client->dev, "I2C readback: wrote=0x%02x read=0x%02x\n",
          initial_value, readback_value);
  if (initial_value != readback_value) {
  retry:
    dev_dbg(&dvs->client->dev,
            "DVS not ready or register corrupted; retry in %u ms\n",
            dvs->retry_delay_ms);
    // Keep RSTN low until the next attempt.
    gpiod_set_value_cansleep(dvs->reset_gpio, 1);
    /* Retry after 2, 4, 8, 16, 30, 30... seconds; stop after success. */
    schedule_delayed_work(&dvs->check_work,
                          msecs_to_jiffies(dvs->retry_delay_ms));
    dvs->retry_delay_ms = min(dvs->retry_delay_ms * 2U, DVS_RETRY_MAX_MS);
    initial_value = ~initial_value;
  } else {
    dvs->ready = true;
    dvs->retry_delay_ms = DVS_RETRY_INITIAL_MS;
    dev_info(&dvs->client->dev,
             "DVS ready: I2C response verified (0x303B=0x%02x)\n",
             readback_value);
  }

out:
  mutex_unlock(&dvs->lock);
}

static int dvs_probe(struct i2c_client *client) {
  struct dvs_data *dvs;

  if (!i2c_check_functionality(client->adapter, I2C_FUNC_I2C))
    return dev_err_probe(&client->dev, -EOPNOTSUPP,
                         "Adapter does not support plain I2C transfers\n");
  dvs = devm_kzalloc(&client->dev, sizeof(*dvs), GFP_KERNEL);
  if (!dvs) {
    return -ENOMEM;
  }

  dvs->client = client;
  mutex_init(&dvs->lock);
  // This would set DVS_Reset pin to HIGH (which is inactive, To reset DVS
  // Device, RSTN is required to go LOW for 0.1us)
  dvs->reset_gpio = devm_gpiod_get(&client->dev, "reset", GPIOD_OUT_LOW);

  if (IS_ERR(dvs->reset_gpio)) {
    return dev_err_probe(&client->dev, PTR_ERR(dvs->reset_gpio),
                         "Failed to acquire reset GPIO\n");
  }
  dvs->data_lanes = 4;
  device_property_read_u32(&client->dev, "nrv,data-lanes", &dvs->data_lanes);
  if (dvs->data_lanes != 1 && dvs->data_lanes != 2 && dvs->data_lanes != 4)
    return dev_err_probe(&client->dev, -EINVAL,
                         "nrv,data-lanes must be 1, 2 or 4, not %u\n",
                         dvs->data_lanes);
  INIT_DELAYED_WORK(&dvs->check_work, dvs_check_work);
  /* Associate the driver state with this I2C client. */
  i2c_set_clientdata(client, dvs);
  dvs->retry_delay_ms = DVS_RETRY_INITIAL_MS;

  mutex_lock(&dvs->lock);
  dvs->stopping = false;
  dvs->ready = false;

  dev_info(&client->dev, "DVS driver loaded\n");

  // Check the device immediately, then retry with exponential backoff.
  schedule_delayed_work(&dvs->check_work, 0);
  mutex_unlock(&dvs->lock);
  return 0;
}

static void dvs_stop_check(struct i2c_client *client) {
  struct dvs_data *dvs = i2c_get_clientdata(client);

  mutex_lock(&dvs->lock);
  dvs->stopping = true;
  dvs->ready = false;
  mutex_unlock(&dvs->lock);

  /* Never wait for the worker while holding its mutex. */
  cancel_delayed_work_sync(&dvs->check_work);
}

static void dvs_remove(struct i2c_client *client) {
  struct dvs_data *dvs = i2c_get_clientdata(client);
  dvs_stop_check(client);

  /* Assert RESET after stopping the I2C worker. */
  gpiod_set_value_cansleep(dvs->reset_gpio, 1);

  dev_info(&client->dev, "DVS driver removed\n");
}

static int dvs_write_reg(struct dvs_data *dvs, u16 reg, u8 val) {
  u8 buf[3] = {reg >> 8, reg & 0xff, val};
  struct i2c_msg msg = {
      .addr = dvs->client->addr, .flags = 0, .len = sizeof(buf), .buf = buf};
  int ret = i2c_transfer(dvs->client->adapter, &msg, 1);

  return ret == 1 ? 0 : (ret < 0 ? ret : -EIO);
}

static int dvs_read_reg(struct dvs_data *dvs, u16 reg, u8 *val) {
  u8 address[] = MAKE_I2C_ADDR(reg);
  struct i2c_msg msgs[] = {I2C_READ(dvs->client->addr, address, val, 1)};
  int ret = i2c_transfer(dvs->client->adapter, msgs, ARRAY_SIZE(msgs));

  return ret == ARRAY_SIZE(msgs) ? 0 : (ret < 0 ? ret : -EIO);
}

/*
 * Apply an NRV DELTA SDK sensor setting file (e.g. Delta_01_1000FPS.txt)
 * from the firmware search path. Each line is "AA:RRRR=VV" with an 8-bit
 * I2C address, 16-bit register and 8-bit value; "//" starts a comment.
 */
static int dvs_apply_settings(struct dvs_data *dvs, const char *name) {
  struct device *dev = &dvs->client->dev;
  const struct firmware *fw;
  char *text, *cursor, *line;
  unsigned int lineno = 0, count = 0;
  int ret;

  ret = request_firmware(&fw, name, dev);
  if (ret)
    return ret;
  text = kmemdup_nul(fw->data, fw->size, GFP_KERNEL);
  release_firmware(fw);
  if (!text)
    return -ENOMEM;

  cursor = text;
  while ((line = strsep(&cursor, "\n"))) {
    char *comment = strstr(line, "//");
    unsigned int addr8, reg, val;

    lineno++;
    if (comment)
      *comment = '\0';
    line = strim(line);
    if (!*line)
      continue;
    if (sscanf(line, "%x:%x=%x", &addr8, &reg, &val) != 3 || addr8 > 0xff ||
        reg > 0xffff || val > 0xff) {
      dev_err(dev, "%s:%u: cannot parse \"%s\"\n", name, lineno, line);
      ret = -EINVAL;
      break;
    }
    if ((addr8 >> 1) != dvs->client->addr) {
      dev_err(dev, "%s:%u: I2C address 0x%02x is not this sensor\n", name,
              lineno, addr8);
      ret = -EINVAL;
      break;
    }
    if (reg == DVS_ADDR_OUTIF_LANES) {
      unsigned int fixed = (val & ~DVS_OUTIF_LANE_MASK) |
                           dvs_outif_lane_bits(dvs->data_lanes);

      // NRV recipes target the 4-lane carrier; follow the board wiring.
      if (fixed != val)
        dev_info(dev, "%s:%u: 0x%04x=0x%02x -> 0x%02x for %u lane(s)\n", name,
                 lineno, reg, val, fixed, dvs->data_lanes);
      val = fixed;
    }
    ret = dvs_write_reg(dvs, reg, val);
    if (ret) {
      dev_err(dev, "%s:%u: write 0x%04x=0x%02x failed: %d\n", name, lineno,
              reg, val, ret);
      break;
    }
    count++;
  }
  kfree(text);
  if (!ret)
    dev_info(dev, "applied %u register writes from %s\n", count, name);
  return ret;
}

static ssize_t ready_show(struct device *dev, struct device_attribute *attr,
                          char *buf) {
  struct dvs_data *dvs = dev_get_drvdata(dev);

  return sysfs_emit(buf, "%d\n", READ_ONCE(dvs->ready));
}
static DEVICE_ATTR_RO(ready);

/* echo NAME > load_settings: NAME is relative to /lib/firmware. */
static ssize_t load_settings_store(struct device *dev,
                                   struct device_attribute *attr,
                                   const char *buf, size_t count) {
  struct dvs_data *dvs = dev_get_drvdata(dev);
  char *name = kstrndup(buf, count, GFP_KERNEL);
  int ret;

  if (!name)
    return -ENOMEM;
  mutex_lock(&dvs->lock);
  ret = dvs->ready ? dvs_apply_settings(dvs, strim(name)) : -EAGAIN;
  mutex_unlock(&dvs->lock);
  kfree(name);
  return ret ? ret : count;
}
static DEVICE_ATTR_WO(load_settings);

/* echo 1 > stream: MODE_SELECT 0x0100 = 1 starts MIPI output. */
static ssize_t stream_show(struct device *dev, struct device_attribute *attr,
                           char *buf) {
  struct dvs_data *dvs = dev_get_drvdata(dev);
  u8 val = 0;
  int ret;

  mutex_lock(&dvs->lock);
  ret = dvs_read_reg(dvs, DVS_ADDR_MODE_SELECT, &val);
  mutex_unlock(&dvs->lock);
  return ret ? ret : sysfs_emit(buf, "%u\n", val);
}

static ssize_t stream_store(struct device *dev, struct device_attribute *attr,
                            const char *buf, size_t count) {
  struct dvs_data *dvs = dev_get_drvdata(dev);
  bool on;
  int ret = kstrtobool(buf, &on);

  if (ret)
    return ret;
  mutex_lock(&dvs->lock);
  ret = dvs->ready ? dvs_write_reg(dvs, DVS_ADDR_MODE_SELECT, on) : -EAGAIN;
  mutex_unlock(&dvs->lock);
  return ret ? ret : count;
}
static DEVICE_ATTR_RW(stream);

/*
 * echo 0x3901 > reg; cat reg         -> read one register
 * echo "0x3901 0x2a" > reg           -> write one register
 */
static ssize_t reg_show(struct device *dev, struct device_attribute *attr,
                        char *buf) {
  struct dvs_data *dvs = dev_get_drvdata(dev);
  u8 val = 0;
  u16 reg;
  int ret;

  mutex_lock(&dvs->lock);
  reg = dvs->sel_reg;
  ret = dvs_read_reg(dvs, reg, &val);
  mutex_unlock(&dvs->lock);
  return ret ? ret : sysfs_emit(buf, "0x%04x=0x%02x\n", reg, val);
}

static ssize_t reg_store(struct device *dev, struct device_attribute *attr,
                         const char *buf, size_t count) {
  struct dvs_data *dvs = dev_get_drvdata(dev);
  unsigned int reg, val;
  int n = sscanf(buf, "%x %x", &reg, &val);
  int ret = 0;

  if (n < 1 || reg > 0xffff || (n == 2 && val > 0xff))
    return -EINVAL;
  mutex_lock(&dvs->lock);
  dvs->sel_reg = reg;
  if (n == 2)
    ret = dvs_write_reg(dvs, reg, val);
  mutex_unlock(&dvs->lock);
  return ret ? ret : count;
}
static DEVICE_ATTR_RW(reg);

static struct attribute *dvs_attrs[] = {
    &dev_attr_ready.attr, &dev_attr_load_settings.attr, &dev_attr_stream.attr,
    &dev_attr_reg.attr, NULL};
ATTRIBUTE_GROUPS(dvs);

/* Must match the compatible string in the Device Tree. */
static const struct of_device_id dvs_of_match[] = {{.compatible = "nrv,rc1s"},
                                                   {}};
MODULE_DEVICE_TABLE(of, dvs_of_match);

static struct i2c_driver dvs_driver = {
    .driver =
        {
            .name = "dvs",
            .of_match_table = dvs_of_match,
            .dev_groups = dvs_groups,
        },
    .probe = dvs_probe,
    .remove = dvs_remove,
    .shutdown = dvs_stop_check,
};

module_i2c_driver(dvs_driver);

MODULE_AUTHOR("Kim Minsu <happytanuki@nrv.com>");
MODULE_DESCRIPTION("RC1S DVS driver");
MODULE_LICENSE("GPL");
