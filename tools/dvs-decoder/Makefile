# SPDX-License-Identifier: GPL-2.0
# Native build on the board, or cross build from WSL2/Ubuntu:
#   make CC=aarch64-linux-gnu-gcc
# dvs-capture needs Linux headers; dvs-decode/dvs-synth build anywhere.
CC ?= cc
CFLAGS ?= -O2 -g
CFLAGS += -std=c11 -D_DEFAULT_SOURCE -Wall -Wextra -Werror
BUILD ?= build
UNAME := $(shell uname -s)

LIB := $(BUILD)/dvs_decode.o $(BUILD)/dvs_cap.o
PROGS := $(BUILD)/dvs-decode $(BUILD)/dvs-synth $(BUILD)/test_frame2b
ifeq ($(UNAME),Linux)
PROGS += $(BUILD)/dvs-capture
endif

.PHONY: all test clean
all: $(PROGS)

$(BUILD):
	mkdir -p $@

$(BUILD)/%.o: src/%.c src/*.h | $(BUILD)
	$(CC) $(CFLAGS) -c -o $@ $<

$(BUILD)/test_frame2b.o: tests/test_frame2b.c src/dvs_decode.h | $(BUILD)
	$(CC) $(CFLAGS) -c -o $@ $<

$(BUILD)/dvs-decode: $(BUILD)/dvs-decode.o $(LIB)
$(BUILD)/dvs-synth: $(BUILD)/dvs-synth.o $(LIB)
$(BUILD)/dvs-capture: $(BUILD)/dvs-capture.o $(BUILD)/dvs_cap.o
$(BUILD)/test_frame2b: $(BUILD)/test_frame2b.o $(BUILD)/dvs_decode.o

$(BUILD)/dvs-capture.o: ../../modules/csis/include/uapi/e850-csis-dvs.h

$(PROGS):
	$(CC) $(CFLAGS) -o $@ $^

test: all
	bash tests/run.sh $(BUILD)

clean:
	rm -rf $(BUILD)
