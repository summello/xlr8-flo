#!/bin/sh
# Sourced by begin.sh, author.sh, review.sh and finish.sh.
# A cache variable that points at an unmounted or unwritable path (for example an external drive
# that is not attached) makes uv, gh, opencode and the codex sandbox die with EACCES before any
# work happens. Fall back to ~/.cache for exactly that case; a mounted, writable path is kept.
_flo_usable() {
  d="$1"
  while [ ! -d "$d" ] && [ "$d" != "/" ]; do d="$(dirname "$d")"; done
  [ -w "$d" ]
}
if [ -n "${UV_CACHE_DIR:-}" ] && ! _flo_usable "$UV_CACHE_DIR"; then UV_CACHE_DIR="$HOME/.cache/uv"; fi
if [ -n "${XDG_CACHE_HOME:-}" ] && ! _flo_usable "$XDG_CACHE_HOME"; then XDG_CACHE_HOME="$HOME/.cache"; fi
export UV_CACHE_DIR XDG_CACHE_HOME
unset -f _flo_usable 2>/dev/null || true

# The manual runners (story-run, review-run, ship, codex-run) call this first. While the fleet
# supervisor (~/.claude/fleet, agents/fleet.toml) is installed and not paused it owns every step,
# and a second driver would race it on the same story.
flo_manual_guard() {
  if [ -e "$HOME/Library/LaunchAgents/com.fleet.xlr8flo.plist" ] && [ ! -e "$HOME/.claude/fleet/state/xlr8flo/paused" ]; then
    echo "the fleet supervisor is active; pause it first: python3 ~/.claude/fleet/fleet.py pause --config agents/fleet.toml" >&2
    exit 3
  fi
  # Omnissiah's supervisor (bin/flo-fleet, state in ~/.claude/omnissiah) owns the steps the same way.
  if [ -e "$HOME/Library/LaunchAgents/com.omnissiah.xlr8flo.plist" ] && [ ! -e "$HOME/.claude/omnissiah/state/xlr8flo/paused" ]; then
    echo "the Omnissiah supervisor is active; pause it first: python3 ~/Workbench/omnissiah/bin/flo-fleet pause --config .flo/fleet.toml" >&2
    exit 3
  fi
}
