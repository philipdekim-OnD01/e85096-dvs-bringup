FROM debian:12
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential meson ninja-build pkg-config python3-mako bison flex git ca-certificates \
    libdrm-dev zlib1g-dev libexpat1-dev libzstd-dev \
    libx11-dev libxext-dev libxfixes-dev libx11-xcb-dev libxcb-glx0-dev libxcb-shm0-dev \
    libxcb-dri2-0-dev libxcb-dri3-dev libxcb-present-dev libxcb-randr0-dev libxcb-sync-dev \
    libxshmfence-dev libxxf86vm-dev libxrandr-dev libwayland-dev wayland-protocols libwayland-egl-backend-dev \
    libpng-dev libjpeg-dev libudev-dev && rm -rf /var/lib/apt/lists/*
