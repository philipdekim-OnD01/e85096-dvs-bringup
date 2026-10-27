# SPDX-License-Identifier: GPL-2.0
.DEFAULT_GOAL := all
MODULE_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
PROJECT_DIR := $(abspath $(MODULE_DIR)/../..)
KDIR ?= /lib/modules/$(shell uname -r)/build
BUILD_DIR ?= $(MODULE_DIR)/build
ARCH ?= arm64
CROSS_COMPILE ?= aarch64-none-linux-gnu-
PYTHON ?= python3
KERNEL_SOURCE ?= $(if $(wildcard $(KDIR)/source),$(realpath $(KDIR)/source),$(KDIR))

KBUILD = $(MAKE) -C "$(KDIR)" M="$(MODULE_DIR)/src" MO="$(abspath $(BUILD_DIR))" ARCH=$(ARCH) CROSS_COMPILE="$(CROSS_COMPILE)"

.PHONY: all check-kernel modules clean modules_install
all: modules
check-kernel:
	@test -f "$(KDIR)/Makefile" || { echo "Kernel build Makefile not found: $(KDIR)/Makefile. Build the target kernel first or specify KDIR." >&2; exit 1; }
	@if test -L "$(KDIR)/source" && ! test -f "$(KDIR)/source/Makefile"; then \
		echo "Kernel source not found: $$(readlink '$(KDIR)/source'). Build outputs alone are insufficient; restore the matching kernel source." >&2; exit 1; \
	fi
	@test -f "$(KERNEL_SOURCE)/scripts/clang-tools/gen_compile_commands.py" || { echo "Invalid kernel source path: $(KERNEL_SOURCE). Check KERNEL_SOURCE." >&2; exit 1; }
modules: check-kernel
	+$(KBUILD) modules
	$(PYTHON) "$(KERNEL_SOURCE)/scripts/clang-tools/gen_compile_commands.py" -d "$(abspath $(BUILD_DIR))" -o "$(abspath $(BUILD_DIR))/compile_commands.json" "$(abspath $(BUILD_DIR))"
clean: check-kernel
	+$(KBUILD) clean
	$(RM) "$(abspath $(BUILD_DIR))/compile_commands.json"
modules_install: check-kernel
	@test -n "$(INSTALL_MOD_PATH)" || { echo "Specify INSTALL_MOD_PATH." >&2; exit 1; }
	+$(KBUILD) INSTALL_MOD_PATH="$(INSTALL_MOD_PATH)" modules_install
