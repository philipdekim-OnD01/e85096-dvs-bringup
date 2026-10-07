// SPDX-License-Identifier: GPL-2.0
#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/i2c.h>
#include <linux/jiffies.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/slab.h>
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

struct dvs_data {
  struct i2c_client *client;
  struct gpio_desc *reset_gpio;
  struct delayed_work check_work;

  struct mutex lock;
  unsigned int retry_delay_ms;
  bool stopping;
  // This represents software standby state
  bool ready;
};

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

/* Must match the compatible string in the Device Tree. */
static const struct of_device_id dvs_of_match[] = {{.compatible = "nrv,rc1s"},
                                                   {}};
MODULE_DEVICE_TABLE(of, dvs_of_match);

static struct i2c_driver dvs_driver = {
    .driver =
        {
            .name = "dvs",
            .of_match_table = dvs_of_match,
        },
    .probe = dvs_probe,
    .remove = dvs_remove,
    .shutdown = dvs_stop_check,
};

module_i2c_driver(dvs_driver);

MODULE_AUTHOR("Kim Minsu <happytanuki@nrv.com>");
MODULE_DESCRIPTION("RC1S DVS driver");
MODULE_LICENSE("GPL");
