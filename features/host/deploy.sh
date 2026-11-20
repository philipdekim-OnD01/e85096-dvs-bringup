#!/bin/sh
# deploy.sh HOST [feature...]  (Mac): send out/e850-features.tgz to a board over ssh and install the features.
# HOST is the ssh target, e.g. philip@192.168.0.244 (sudo must work there). No feature = only copy and list.
# Boards share one host key: ssh -o HostKeyAlias=192.168.0.236 (CLAUDE.md), set here.
set -e
R=$(cd "$(dirname "$0")/../.." && pwd)
H=$1; shift || { sed -n 2,4p "$0"; exit 1; }
T=${TGZ:-$R/out/e850-features.tgz}
SSH="ssh -o HostKeyAlias=192.168.0.236"
scp -q -o HostKeyAlias=192.168.0.236 "$T" "$H:/var/tmp/e850-features.tgz"
$SSH "$H" "rm -rf /var/tmp/e850-features && tar xzf /var/tmp/e850-features.tgz -C /var/tmp &&
    if [ $# -gt 0 ]; then sudo sh /var/tmp/e850-features/e850-feature install $*; else sh /var/tmp/e850-features/e850-feature list; fi"
