#!/bin/sh
# Install the event units into systemd, resolving the shared sandbox links.
#
#   sudo ./install.sh system      Legion system units -> /etc/systemd/system
#   ./install.sh user             Cerberus drain      -> ~/.config/systemd/user
#   sudo ./install.sh citadel     Citadel remote replay -> /etc/systemd/system
#
# Add --dry-run to print what would be copied. Existing files are backed up to
# <name>.bak-<date> first. The script never enables, starts or restarts a unit;
# it ends with `daemon-reload` and prints the commands to run next.
set -eu

kind=${1:-}
dry=0
[ "${2:-}" = "--dry-run" ] && dry=1
here=$(cd "$(dirname "$0")" && pwd)

case "$kind" in
  system|citadel) dest=/etc/systemd/system; reload="systemctl daemon-reload" ;;
  user) dest=$HOME/.config/systemd/user; reload="systemctl --user daemon-reload" ;;
  *) echo "usage: $0 system|user|citadel [--dry-run]" >&2; exit 2 ;;
esac

stamp=$(date +%Y%m%d)
copy() {  # copy <source> <destination>; follows symlinks so linked drop-ins become real files
  src=$1; dst=$2
  if [ "$dry" = 1 ]; then echo "install $dst"; return; fi
  mkdir -p "$(dirname "$dst")"
  [ -L "$dst" ] && rm -f "$dst"   # an old link into the repo would be written through
  [ -e "$dst" ] && ! cmp -s "$src" "$dst" && cp -p "$dst" "$dst.bak-$stamp"
  cp -L "$src" "$dst"
  chmod 0644 "$dst"
}

for unit in "$here/$kind"/*.service "$here/$kind"/*.timer; do
  [ -e "$unit" ] || continue
  name=$(basename "$unit")
  copy "$unit" "$dest/$name"
  if [ -d "$unit.d" ]; then
    for conf in "$unit.d"/*.conf; do copy "$conf" "$dest/$name.d/$(basename "$conf")"; done
  fi
done

[ "$dry" = 1 ] || $reload
echo "Installed. Next: review with 'systemctl cat <unit>', then enable or restart as needed."
