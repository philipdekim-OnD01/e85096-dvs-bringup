# common.sh: helpers for e850-feature and features/*/feature.sh. The sourcing script sets ROOT = bundle dir.
DEST=/opt/e850-features
STATE=/var/lib/e850-features
KVER=$(uname -r)
MODDIR=/lib/modules/$KVER/extra
UNITDIR=/etc/systemd/system

log() { echo "$*"; }
die() { echo "e850-feature: $*" >&2; exit 1; }
need_root() { [ "$(id -u)" = 0 ] || die "run as root (sudo)"; }

# modules from the bundle's ko/ go to extra/, which depmod searches before the flat vendor tree
ko_install() {
    [ "$(cat "$ROOT/KVER")" = "$KVER" ] || die "bundle built for $(cat "$ROOT/KVER"), board runs $KVER"
    mkdir -p "$MODDIR" /etc/depmod.d
    echo 'search extra built-in' > /etc/depmod.d/e850-features.conf
    for k; do install -m 644 "$ROOT/ko/$k.ko" "$MODDIR/$k.ko"; done
    depmod -a
}
ko_remove() { for k; do rm -f "$MODDIR/$k.ko"; done; depmod -a; }

unit_install() {           # unit_install FILE [enable]  -- FILE may hold @USER@
    sed "s|@DEST@|$DEST|g; s|@USER@|${DESKTOP_USER:-root}|g" "$1" > "$UNITDIR/$(basename "$1")"
    systemctl daemon-reload
    [ "$2" = enable ] && systemctl enable -q "$(basename "$1")"
    return 0
}
unit_remove() { for u; do systemctl disable -q --now "$u" 2>/dev/null || true; rm -f "$UNITDIR/$u"; done; systemctl daemon-reload; }

apt_need() {               # install the packages that are missing (needs the network)
    miss=
    for p; do dpkg-query -W -f '${Status}' "$p" 2>/dev/null | grep -q 'ok installed' || miss="$miss $p"; done
    [ -z "$miss" ] && return 0
    log "apt-get install$miss"
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $miss
}

# regular users (uid 1000-59999) into a device group, e.g. audio for /dev/snd over ssh (X gets seat ACLs anyway)
users_to_group() {
    for u in $(getent passwd | awk -F: '$3 >= 1000 && $3 < 60000 {print $1}'); do usermod -aG "$1" "$u"; done
}
