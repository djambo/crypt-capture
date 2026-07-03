#!/usr/bin/env bash
# Launch the Kinect node with repo-stored per-device-class defaults.
#
# Called by the kinect-node systemd unit (ExecStart). The unit's
# EnvironmentFile (/etc/default/kinect-node) supplies the truly per-device
# settings (CENTRAL_HOST, SENSOR_ID, DISPLAY, EXTRA_ARGS overrides…); this
# script adds the DEVICE-CLASS defaults from deploy/profiles/<class>.env so
# new flags (e.g. the Orin pose model) roll out to every node via the
# service's auto-update instead of hand-editing /etc on each Jetson.
#
# Flag precedence (argparse last-wins):
#     built-ins  <  PROFILE_EXTRA_ARGS (repo)  <  EXTRA_ARGS (/etc, per-device)
#
# NODE_PROFILE (from /etc/default/kinect-node, default "auto"):
#     auto     detect from /etc/nv_tegra_release (L4T R34+ = orin-class GPU
#              node, R32/R28 = 1st-gen Nano-class), else "default"
#     orin | nano | default | <name>   force deploy/profiles/<name>.env
#
# Test headlessly (prints the command instead of running it):
#     DRY_RUN=1 SENSOR_ID=0 deploy/run-node.sh
#     DRY_RUN=1 TEGRA_RELEASE_FILE=/tmp/fake_r32 deploy/run-node.sh
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # <repo>/deploy
REPO_DIR="$(dirname "$SCRIPT_DIR")"

NODE_PROFILE="${NODE_PROFILE:-auto}"
TEGRA_RELEASE_FILE="${TEGRA_RELEASE_FILE:-/etc/nv_tegra_release}"

detect_profile() {
  # /etc/nv_tegra_release, first line, e.g.:
  #   # R36 (release), REVISION: 4.3, ...   (JetPack 6, Orin)
  #   # R32 (release), REVISION: 7.1, ...   (JetPack 4, 1st-gen Nano)
  if [ -r "$TEGRA_RELEASE_FILE" ]; then
    local rel
    rel="$(head -n1 "$TEGRA_RELEASE_FILE")"
    case "$rel" in
      *R3[4-9]*|*R[4-9][0-9]*) echo orin; return ;;   # L4T R34+ = JetPack 5/6+
      *R32*|*R28*)             echo nano; return ;;   # JetPack 4 era
    esac
  fi
  echo default
}

if [ "$NODE_PROFILE" = "auto" ]; then
  NODE_PROFILE="$(detect_profile)"
fi

PROFILE_FILE="$SCRIPT_DIR/profiles/$NODE_PROFILE.env"
PROFILE_EXTRA_ARGS=''
if [ -r "$PROFILE_FILE" ]; then
  # shellcheck source=/dev/null
  . "$PROFILE_FILE"
else
  echo "run-node: no profile '$NODE_PROFILE' ($PROFILE_FILE missing) — no profile flags" >&2
fi

echo "run-node: profile=$NODE_PROFILE  profile_args='$PROFILE_EXTRA_ARGS'  extra_args='${EXTRA_ARGS:-}'"

CMD=(/usr/bin/python3 -u -m node.kinect_node
     --host "${CENTRAL_HOST:-auto}" --port "${CENTRAL_PORT:-9000}"
     --sensor "${SENSOR_ID:-0}" --frames 0)
# Word-split the flag strings on purpose (they are whitespace-separated flags);
# disable globbing so a stray * can't expand. Device EXTRA_ARGS goes LAST so a
# per-device flag overrides the profile's (argparse takes the last occurrence).
set -f
# shellcheck disable=SC2206
CMD+=(${PROFILE_EXTRA_ARGS} ${EXTRA_ARGS:-})
set +f

if [ "${DRY_RUN:-0}" = "1" ]; then
  printf '%s\n' "${CMD[*]}"
  exit 0
fi

cd "$REPO_DIR"
exec "${CMD[@]}"
