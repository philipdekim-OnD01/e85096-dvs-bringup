#!/bin/sh
# test-dvs.sh: the restart loop of `dvs` against a stub dvslive.sh. Only in a throwaway container (it writes
# /usr/local/bin):  docker run --rm -v "$PWD/features/dvs:/d:ro" debian:12 sh /d/test-dvs.sh
set -u
export T=$(mktemp -d)
mkdir -p /usr/local/bin
cat > /usr/local/bin/dvslive.sh <<'X'
#!/bin/sh
n=$(($(cat "$T/n") + 1)); echo $n > "$T/n"
exit $(sed -n "${n}p" "$T/codes")
X
chmod 755 /usr/local/bin/dvslive.sh
fail=0
# exit codes of successive captures -> how many runs, the exit code of dvs
check() {
    printf '%s\n' $1 > "$T/codes"; echo 0 > "$T/n"
    DISPLAY=:9 sh "$(dirname "$0")/dvs" ${4:-} > "$T/out" 2>&1
    st=$?; n=$(cat "$T/n")
    if [ "$n $st" = "$2 $3" ]; then echo "ok   [$1]: $n runs, exit $st"
    else echo "FAIL [$1]: $n runs, exit $st (want $2 runs, exit $3)"; cat "$T/out"; fail=1; fi
}
check "0 0 130" 3 0           # a setting from the page / a dead stream, again, then q in the window
check "1 1 1" 3 1             # fails at once three times: give up
check "1 0 1 1 130" 5 0       # a capture that ran resets the count
check "3" 1 0 --auto          # desktop start on a board without the DVS: nothing
check "0 3" 2 0               # (no other exit code 3 than no sensor; it ends quietly)
exit $fail
