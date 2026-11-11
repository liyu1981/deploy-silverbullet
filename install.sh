#!/usr/bin/env bash
#
# Install / upgrade SilverBullet (https://silverbullet.md) as a *user* systemd
# service on Linux (x86_64 or aarch64).
#
#   binary : ~/.local/bin/silverbullet
#   data   : ~/.silverbullet
#   listen : 127.0.0.1:3234
#   unit   : ~/.config/systemd/user/silverbullet.service
#
# Re-running the script upgrades in place (or does nothing when already current).
# Run it as the user that should own the service -- *not* via sudo.
#
set -euo pipefail

REPO="silverbulletmd/silverbullet"
SERVICE="silverbullet.service"

BIN_DIR="${SB_BIN_DIR:-$HOME/.local/bin}"
BIN="$BIN_DIR/silverbullet"
DATA_DIR="${SB_DATA_DIR:-$HOME/.silverbullet}"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/$SERVICE"

HOSTNAME_ARG="${SB_HOSTNAME:-127.0.0.1}"
PORT="${SB_PORT:-3234}"
ADMIN="${SB_ADMIN:-}"          # optional user:pass -> runs `silverbullet setup` on a fresh data dir
DO_START=1
FORCE=0
# Single-space mode by default: no users.json, no built-in login page. Meant to
# run behind an authenticating proxy/tunnel (Cloudflare Access, oauth2-proxy,
# Authelia, ...) that is the only gatekeeper. Use --multi-space to keep
# SilverBullet's own accounts instead.
SPACE_MODE="${SB_SPACE_MODE:-single}"
case "$SPACE_MODE" in multi|accounts) SPACE_MODE=multi ;; *) SPACE_MODE=single ;; esac

# Service worker (offline cache, PWA, client-side encryption). On by default;
# behind an auth proxy it only works when the proxy lets /service_worker.js and
# /.client/* through unauthenticated.
WITH_SW="${SB_WITH_SERVICE_WORKER:-1}"
case "$WITH_SW" in 0|false|no) WITH_SW=0 ;; *) WITH_SW=1 ;; esac

# Optional URL prefix when the space is mounted below the domain root.
URL_PREFIX="${SB_URL_PREFIX:-}"

C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YEL=$'\033[33m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
[ -t 1 ] || { C_RED=; C_GRN=; C_YEL=; C_DIM=; C_OFF=; }

info() { printf '%s==>%s %s\n' "$C_GRN" "$C_OFF" "$*"; }
warn() { printf '%swarn:%s %s\n' "$C_YEL" "$C_OFF" "$*" >&2; }
die()  { printf '%serror:%s %s\n' "$C_RED" "$C_OFF" "$*" >&2; exit 1; }

usage() {
  cat <<EOF
Usage: ${0##*/} [options]

Installs the latest SilverBullet release into $BIN_DIR and (re)starts it as the
systemd user unit "$SERVICE". Safe to run again to upgrade.

Options:
  --version <tag>    Install a specific release tag (e.g. 2.12.1) instead of latest
  --force            Reinstall even if the latest version is already installed
  --no-start         Install files only; do not enable/start the service
  --hostname <addr>  Listen address (default: 127.0.0.1)
  --port <port>      Listen port (default: 3234)
  --data <dir>       Data folder (default: ~/.silverbullet)
  --bin-dir <dir>    Install folder (default: ~/.local/bin)
  --single           Single-space mode, no built-in login (default). Use when
                     an auth proxy / tunnel does the Google/SSO login.
  --multi-space      Multi-space mode with SilverBullet's own accounts
                     (spaces.json + users.json, set up via the /.setup wizard)
  --admin <u:p>      Only with --multi-space: provision the admin account
  --url-prefix <p>   Mount the space below the domain root, e.g. /notes
                     (default: root)
  --no-service-worker
                     Disable the client service worker (offline/PWA/encryption).
                     Only useful when an auth proxy cannot be told to let
                     /service_worker.js and /.client/* through.
  --with-service-worker
                     Enable the service worker (default)
  -h, --help         This help

Environment: SB_VERSION, SB_PORT, SB_HOSTNAME, SB_DATA_DIR, SB_BIN_DIR, SB_ADMIN,
             SB_SPACE_MODE=single|multi, SB_URL_PREFIX, SB_WITH_SERVICE_WORKER=0|1
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --version)   SB_VERSION="${2:?}"; export SB_VERSION; shift 2 ;;
    --force)     FORCE=1; shift ;;
    --no-start)  DO_START=0; shift ;;
    --hostname)  HOSTNAME_ARG="${2:?}"; shift 2 ;;
    --port)      PORT="${2:?}"; shift 2 ;;
    --data)      DATA_DIR="${2:?}"; shift 2 ;;
    --bin-dir)   BIN_DIR="${2:?}"; BIN="$BIN_DIR/silverbullet"; shift 2 ;;
    --admin)     ADMIN="${2:?}"; shift 2 ;;
    --single)    SPACE_MODE=single; shift ;;
    --multi-space|--multi) SPACE_MODE=multi; shift ;;
    --url-prefix) URL_PREFIX="${2:?}"; shift 2 ;;
    --with-service-worker|--service-worker) WITH_SW=1; shift ;;
    --no-service-worker) WITH_SW=0; shift ;;
    -h|--help)   usage; exit 0 ;;
    *)           die "unknown option: $1 (try --help)" ;;
  esac
done

# --------------------------------------------------------------------------- #
# Pre-flight
# --------------------------------------------------------------------------- #
[ "$(uname -s)" = "Linux" ] || die "this script targets Linux (found: $(uname -s))"
if [ "$(id -u)" -eq 0 ]; then
  warn "running as root: the service would be installed for $HOME (root)."
  warn "run this script as the unprivileged user that should own SilverBullet."
fi

case "$(uname -m)" in
  x86_64|amd64)          ARCH="x86_64" ;;
  aarch64|arm64)         ARCH="aarch64" ;;
  *) die "unsupported CPU architecture '$(uname -m)' (x86_64 and aarch64 are supported)" ;;
esac

command -v curl >/dev/null 2>&1 || die "curl is required (install: sudo apt install curl / dnf install curl)"
HAS_UNZIP=0; command -v unzip >/dev/null 2>&1 && HAS_UNZIP=1
HAS_PY=0;    command -v python3 >/dev/null 2>&1 && HAS_PY=1
[ "$HAS_UNZIP" = 1 ] || [ "$HAS_PY" = 1 ] || die "unzip or python3 is required to extract the release archive"

if [ "$SPACE_MODE" = "single" ] && [ -f "$DATA_DIR/spaces.json" ]; then
  die "single-space mode cannot run on a multi-space data folder:
  found $DATA_DIR/spaces.json
  Either start over (rm -rf \"$DATA_DIR\"/* -- back up your notes first) or
  run this script with --multi-space."
fi

have_systemd_user() {
  command -v systemctl >/dev/null 2>&1 || return 1
  systemctl --user show-environment >/dev/null 2>&1
}

# --------------------------------------------------------------------------- #
# Resolve release
# --------------------------------------------------------------------------- #
latest_tag() {
  local tag
  tag=$(curl -fsSL --retry 3 --retry-delay 1 \
          "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
        | sed -n 's/^[[:space:]]*"tag_name":[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1 || true)
  if [ -z "$tag" ]; then
    # Fallback when the API is rate limited: follow the /releases/latest redirect.
    tag=$(curl -fsSIL --retry 3 -o /dev/null -w '%{url_effective}' \
            "https://github.com/$REPO/releases/latest" 2>/dev/null \
          | sed 's#.*/tag/##' | tr -d '\r\n' || true)
  fi
  [ -n "$tag" ] || die "could not determine the latest SilverBullet release tag"
  printf '%s\n' "$tag"
}

installed_version() {
  [ -x "$BIN" ] || { echo ""; return; }
  "$BIN" version 2>/dev/null | sed -n 's/^\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' | head -n1
}

WANT_TAG="${SB_VERSION:-}"
[ -n "$WANT_TAG" ] || WANT_TAG=$(latest_tag)
WANT_VER=$(printf '%s' "$WANT_TAG" | sed -n 's/^\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\).*/\1/p')
[ -n "$WANT_VER" ] || WANT_VER="$WANT_TAG"

CUR_VER=$(installed_version)

NEED_INSTALL=1
if [ "$FORCE" = 0 ] && [ -n "$CUR_VER" ] && [ "$CUR_VER" = "$WANT_VER" ]; then
  NEED_INSTALL=0
fi

info "target version : $WANT_TAG"
info "installed      : ${CUR_VER:-none}"
info "platform       : linux-$ARCH"

# --------------------------------------------------------------------------- #
# Render systemd unit
# --------------------------------------------------------------------------- #
# systemd treats '%' as a specifier prefix: escape it everywhere.
esc() { printf '%s' "$1" | sed 's/%/%%/g'; }
# ExecStart is tokenized, so paths with spaces must be quoted there.
# WorkingDirectory= on the other hand must NOT be quoted (systemd rejects it).
qd() { printf '"%s"' "$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/%/%%/g')"; }

render_unit() {
  cat <<EOF
[Unit]
Description=SilverBullet wiki server
Documentation=https://silverbullet.md
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
WorkingDirectory=$(esc "$DATA_DIR")
ExecStart=$(qd "$BIN") --hostname $(qd "$HOSTNAME_ARG") --port $(qd "$PORT")$(if [ "$SPACE_MODE" = "single" ]; then printf ' --single'; fi) $(qd "$DATA_DIR")
Restart=on-failure
RestartSec=2
TimeoutStopSec=20
SyslogIdentifier=silverbullet
$(if [ "$WITH_SW" = 0 ]; then printf 'Environment=SB_DISABLE_SERVICE_WORKER=1\n'; fi)
$(if [ -n "$URL_PREFIX" ]; then printf 'Environment=SB_URL_PREFIX=%s\n' "$URL_PREFIX"; fi)
# Moderate hardening: SilverBullet only ever needs its data folder.
# NOTE: do NOT add ProtectKernelModules=/ProtectKernelLogs=/ProtectClock= here.
# They clear bits from the capability bounding set, and the *user* manager
# cannot drop capabilities (no CAP_SETPCAP), so the unit dies at exec with
# "Failed to drop capabilities: Operation not permitted" (218/CAPABILITIES).
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes

[Install]
WantedBy=default.target
EOF
}

mkdir -p "$BIN_DIR" "$DATA_DIR" "$UNIT_DIR"

UNIT_TMP=$(mktemp)
cleanup() { rm -f "$UNIT_TMP"; if [ -n "${TMPDIR_SB:-}" ]; then rm -rf "$TMPDIR_SB"; fi; }
trap cleanup EXIT
render_unit > "$UNIT_TMP"
UNIT_CHANGED=0
if ! cmp -s "$UNIT_TMP" "$UNIT" 2>/dev/null; then
  UNIT_CHANGED=1
  install -m 644 "$UNIT_TMP" "$UNIT"
  info "wrote $UNIT"
else
  info "unit already up to date: $UNIT"
fi

# --------------------------------------------------------------------------- #
# Download + install binary
# --------------------------------------------------------------------------- #
if [ "$NEED_INSTALL" = 1 ]; then
  TMPDIR_SB=$(mktemp -d)
  URL="https://github.com/$REPO/releases/download/$WANT_TAG/silverbullet-server-linux-$ARCH.zip"
  info "downloading $URL"
  curl -fL --retry 3 --retry-delay 2 --connect-timeout 15 -o "$TMPDIR_SB/sb.zip" "$URL" \
    || die "download failed (is tag '$WANT_TAG' valid?)"

  info "extracting"
  if [ "$HAS_UNZIP" = 1 ]; then
    unzip -q -o "$TMPDIR_SB/sb.zip" -d "$TMPDIR_SB/x"
  else
    python3 - "$TMPDIR_SB/sb.zip" "$TMPDIR_SB/x" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
    z.extractall(sys.argv[2])
PY
  fi

  NEWBIN=$(find "$TMPDIR_SB/x" -type f -name 'silverbullet*' -perm -u+x | head -n1)
  [ -n "$NEWBIN" ] || NEWBIN=$(find "$TMPDIR_SB/x" -type f -name 'silverbullet*' | head -n1)
  [ -n "$NEWBIN" ] || die "no 'silverbullet' executable found in the release archive"

  chmod 755 "$NEWBIN"
  GOT_VER=$("$NEWBIN" version 2>/dev/null | sed -n 's/^\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' | head -n1)
  [ -n "$GOT_VER" ] || die "downloaded binary does not run on this machine (bad archive or missing libc?)"
  [ "$GOT_VER" = "$WANT_VER" ] || die "version mismatch: expected $WANT_VER, archive contains $GOT_VER"

  # Atomic replace: never leave a half-written binary behind.
  install -m 755 "$NEWBIN" "$BIN_DIR/.silverbullet.new.$$"
  mv -f "$BIN_DIR/.silverbullet.new.$$" "$BIN"
  info "installed $BIN ($GOT_VER)"
else
  info "already at $CUR_VER -- skipping download (use --force to reinstall)"
fi

# Initialize a brand-new data folder when asked to.
if [ -n "$ADMIN" ] && [ "$SPACE_MODE" = "single" ]; then
  warn "--admin only applies to --multi-space; ignoring it in single-space mode"
elif [ -n "$ADMIN" ] && [ ! -f "$DATA_DIR/.silverbullet-setup-done" ] && [ -z "$(ls -A "$DATA_DIR" 2>/dev/null)" ]; then
  info "provisioning fresh data folder with admin '$ADMIN'"
  "$BIN" setup --admin "$ADMIN" "$DATA_DIR" || warn "setup failed; open /.setup in the browser instead"
  : > "$DATA_DIR/.silverbullet-setup-done" 2>/dev/null || true
fi

# --------------------------------------------------------------------------- #
# Service management
# --------------------------------------------------------------------------- #
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR is not on your PATH (add: export PATH=\"\$HOME/.local/bin:\$PATH\")" ;;
esac

if [ "$DO_START" = 0 ]; then
  info "skipping service activation (--no-start)"
  exit 0
fi

if ! have_systemd_user; then
  warn "systemd user session is not available in this shell."
  warn "the binary and unit are installed; activate later with:"
  printf '  systemctl --user daemon-reload\n  systemctl --user enable --now %s\n' "$SERVICE" >&2
  exit 1
fi

if [ "$UNIT_CHANGED" = 1 ]; then
  systemctl --user daemon-reload
fi

systemctl --user enable "$SERVICE" >/dev/null 2>&1 || warn "could not enable $SERVICE"
# Clear any start-limit/failed state left over from an earlier broken start.
systemctl --user reset-failed "$SERVICE" >/dev/null 2>&1 || true
systemctl --user restart "$SERVICE" || die "failed to start $SERVICE (see: journalctl --user -u $SERVICE -n 50)"

# Keep the service running after logout / without an active login session.
if command -v loginctl >/dev/null 2>&1; then
  if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || echo no)" != "yes" ]; then
    loginctl enable-linger "$USER" >/dev/null 2>&1 \
      || warn "enable boot-start without login with: sudo loginctl enable-linger $USER"
  fi
fi

# Give it a moment, then report health.
sleep 1
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS -o /dev/null --max-time 2 "http://$HOSTNAME_ARG:$PORT/" 2>/dev/null; then
    break
  fi
  sleep 1
done

if systemctl --user is-active --quiet "$SERVICE"; then
  info "service is active: $SERVICE"
else
  warn "service is not active; inspect logs: journalctl --user -u $SERVICE -n 50 --no-pager"
fi

cat <<EOF

${C_DIM}--------------------------------------------------------------${C_OFF}
 URL       : http://$HOSTNAME_ARG:$PORT$URL_PREFIX/   ($(if [ "$SPACE_MODE" = "single" ]; then echo "no built-in login; put your auth proxy in front"; else echo "fresh data dir: open /.setup"; fi))
 auth      : $(if [ "$SPACE_MODE" = "single" ]; then echo "single-space, no built-in login (expects a proxy/tunnel in front)"; else echo "multi-space, SilverBullet accounts (spaces.json)"; fi)
 sw        : $(if [ "$WITH_SW" = 1 ]; then echo "enabled"; else echo "disabled (--no-service-worker)"; fi)
 binary    : $BIN
 data      : $DATA_DIR
 unit      : $UNIT   (edit, then: systemctl --user daemon-reload)
 logs      : journalctl --user -u $SERVICE -f
 status    : systemctl --user status $SERVICE
 upgrade   : re-run ${0##*/} (or: ${0##*/} --force)
${C_DIM}--------------------------------------------------------------${C_OFF}
EOF
