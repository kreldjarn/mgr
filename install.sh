#!/bin/sh
# Install mgr (planning TUI) and its Claude Code skill. Re-run to upgrade.
#
#   curl -fsSL https://raw.githubusercontent.com/kreldjarn/mgr/main/install.sh | sh
#   ./install.sh                  # from a checkout: installs that checkout
#
# Environment:
#   MGR_REPO      git URL to install from (overrides the default below)
#   MGR_EDITABLE  =1 with a checkout: editable install, for hacking on mgr
#   MGR_DIR       plans folder the skill should point at (default ~/.local/share/mgr)
#
# POSIX sh, because `curl | sh` runs whatever /bin/sh is (dash on Debian).
set -eu

# Where teammates install from when piping this script from GitHub.
MGR_REPO="${MGR_REPO:-https://github.com/kreldjarn/mgr}"

say() { printf 'mgr-install: %s\n' "$*"; }
die() {
  say "error: $*" >&2
  exit 1
}

# --- 1. uv -------------------------------------------------------------------
# Check that uv *runs*, not just that it exists: an asdf/mise shim without a
# version pinned is on PATH but fails, and installing a second uv on top of it
# would only confuse things.
if ! uv --version >/dev/null 2>&1; then
  if command -v uv >/dev/null 2>&1; then
    die "'uv' is on PATH but fails to run (an asdf/mise shim with no version set?). Fix that and re-run."
  fi
  say "uv not found; installing it (https://docs.astral.sh/uv/)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # uv's installer can't modify this shell's PATH, so add its install dirs here.
  PATH="${XDG_BIN_HOME:-$HOME/.local/bin}:$HOME/.cargo/bin:$PATH"
  uv --version >/dev/null 2>&1 || die "uv was installed but isn't runnable; open a new shell and re-run"
fi

# --- 2. mgr ------------------------------------------------------------------
# Run as a file (./install.sh) next to a checkout → install that checkout.
# Piped through `curl | sh`, $0 is just "sh", so this never misfires on
# whatever project the user happens to be in.
src=""
case "$0" in
  *install.sh)
    dir=$(cd "$(dirname "$0")" && pwd)
    if grep -q '^name = "mgr"' "$dir/pyproject.toml" 2>/dev/null; then
      src="$dir"
    fi
    ;;
esac

if [ -n "$src" ]; then
  editable=""
  [ "${MGR_EDITABLE:-}" = "1" ] && editable="--editable"
  say "installing mgr from checkout $src"
  # --force replaces an existing install, which is what makes re-running upgrade.
  # $editable is deliberately unquoted so an empty value adds no argument.
  # shellcheck disable=SC2086
  uv tool install --force $editable "$src"
else
  [ -n "$MGR_REPO" ] || die "no repo configured: set MGR_REPO=<git url> or edit MGR_REPO in install.sh"
  say "installing mgr from $MGR_REPO"
  # --refresh so a re-run fetches the latest commit instead of a cached one.
  uv tool install --force --refresh "git+$MGR_REPO"
fi

# --- 3. Claude Code skill ----------------------------------------------------
# Call mgr by full path: its bin dir may not be on PATH yet in this shell, and
# mgr embeds its own path in the skill so Claude can run it from anywhere.
bin_dir=$(uv tool dir --bin)
"$bin_dir/mgr" skill --install
[ -d "$HOME/.claude" ] || say "note: ~/.claude didn't exist; the skill will be picked up once Claude Code is installed"

# --- 4. PATH -----------------------------------------------------------------
case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) say "add $bin_dir to your PATH to run 'mgr' (or run: uv tool update-shell)" ;;
esac

say "done. Run 'mgr' to start planning; in Claude Code, ask it to create the Linear tickets from your mgr plan."
