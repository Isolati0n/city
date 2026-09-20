#!/bin/sh
# nsfs-hold.sh -- runs the experiment Grok named as the load-bearing bet in
# its ground-up design:
#
#   "I have not run the experiment 'kill every process in the mntns, setns
#    back in, write to the overlay.' If that experiment fails, the fallback
#    is one unprivileged sleeper per house whose only job is to hold the
#    namespace."
#
# The design's central claim is that a house is a durable set of kernel
# objects rather than a process. Restart is then setns + exec, with no
# remount and no privileged resident parent. That only works if a mount
# namespace pinned by an nsfs bind survives the death of every process in
# it, AND the overlay inside it is still usable afterwards.
#
# Method:
#   1. unshare a mount namespace, build an overlay inside it
#   2. bind /proc/<pid>/ns/mnt onto a file, the way `ip netns` pins netns
#   3. kill every process in that namespace
#   4. from outside, nsenter the pinned namespace and write to the overlay
set -u
R=/tmp/nsfs
rm -rf $R; mkdir -p $R/lower $R/upper $R/work $R/mnt $R/pin
echo "from the lower layer" > $R/lower/base
: > $R/pin/mntns

echo "=== 1. child unshares a mount namespace and mounts an overlay in it ==="
unshare --mount --propagation private sh -c '
  mount -t overlay overlay \
    -o lowerdir=/tmp/nsfs/lower,upperdir=/tmp/nsfs/upper,workdir=/tmp/nsfs/work \
    /tmp/nsfs/mnt || { echo "  mount FAILED"; exit 1; }
  echo "written while a process was alive" > /tmp/nsfs/mnt/alive
  echo "  overlay mounted; wrote /alive; pid=$$"
  echo $$ > /tmp/nsfs/childpid
  # hold until pinned
  while [ ! -f /tmp/nsfs/pinned ]; do sleep 0.2; done
' &
CH=$!
i=0; while [ ! -f $R/childpid ] && [ $i -lt 50 ]; do sleep 0.2; i=$((i+1)); done
sleep 0.5
INNER=$(cat $R/childpid 2>/dev/null)
echo "  inner pid: $INNER"

echo "=== 2. pin the namespace with an nsfs bind (what ip netns does) ==="
mount --bind /proc/$INNER/ns/mnt $R/pin/mntns && echo "  pinned /proc/$INNER/ns/mnt -> $R/pin/mntns"
readlink /proc/$INNER/ns/mnt
touch $R/pinned
sleep 1

echo "=== 3. kill every process in that namespace ==="
kill -9 $INNER 2>/dev/null; kill -9 $CH 2>/dev/null; wait 2>/dev/null
sleep 1
if [ -d /proc/$INNER ]; then echo "  WARNING: inner pid still present"; else echo "  inner process gone"; fi

echo "=== 4. does the pinned namespace still exist? ==="
readlink $R/pin/mntns 2>/dev/null || echo "  (readlink on the bind gives nothing; checking via nsenter)"

echo "=== 5. re-enter it and use the overlay ==="
nsenter --mount=$R/pin/mntns sh -c '
  echo "  --- inside the re-entered namespace ---"
  if mountpoint -q /tmp/nsfs/mnt; then echo "  overlay STILL MOUNTED"; else echo "  overlay NOT MOUNTED"; fi
  echo "  read /alive : $(cat /tmp/nsfs/mnt/alive 2>&1)"
  echo "  read /base  : $(cat /tmp/nsfs/mnt/base  2>&1)"
  if echo "written after every process died" > /tmp/nsfs/mnt/after 2>/tmp/nsfs/werr; then
      echo "  WRITE AFTER DEATH: ok -> $(cat /tmp/nsfs/mnt/after)"
  else
      echo "  WRITE AFTER DEATH: FAILED -> $(cat /tmp/nsfs/werr)"
  fi
' 2>&1 || echo "  nsenter FAILED (exit $?)"

echo "=== 6. is the upper layer visible from outside, i.e. did the write land? ==="
ls -la $R/upper 2>/dev/null | tail -3
umount $R/pin/mntns 2>/dev/null && echo "  unpinned"
