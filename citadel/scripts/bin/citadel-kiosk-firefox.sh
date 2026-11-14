#!/usr/bin/env bash
# Citadel Beacon kiosk. The local wallboard owns page rotation; the Kanban
# page itself polls the shared adapter-backed /data/tasks.json endpoint.
set -u

PROFILE=/home/kiosk/.mozilla/kiosk-profile
URL=http://127.0.0.1:8080
RUNTIME=/run/user/1000

stopping=0
child_pid=""

stop_child() {
    if [[ -n "$child_pid" ]] && kill -0 "$child_pid" 2>/dev/null; then
        kill -TERM "$child_pid" 2>/dev/null || true
    fi
}

trap 'stopping=1; stop_child' TERM INT

hypr_signature() {
    python3 -c 'import pathlib; print(next(p.name for p in pathlib.Path("/run/user/1000/hypr").iterdir() if p.is_dir()))'
}

hypr() {
    local sig
    sig="$(hypr_signature)"
    XDG_RUNTIME_DIR="$RUNTIME" HYPRLAND_INSTANCE_SIGNATURE="$sig" /usr/bin/hyprctl "$@"
}

place_window() {
    local pid="$1" addr=""
    for _ in {1..30}; do
        addr="$(hypr clients -j 2>/dev/null | python3 -c 'import json,sys; p=int(sys.argv[1]); d=json.load(sys.stdin); print(next((x["address"] for x in d if x.get("pid")==p), ""))' "$pid" 2>/dev/null || true)"
        if [[ -n "$addr" ]]; then
            hypr dispatch "hl.dsp.window.move({ workspace = \"4\", monitor = \"HDMI-A-1\", window = \"address:${addr}\" })" >/dev/null || true
            for _ in {1..5}; do
                hypr dispatch "hl.dsp.window.fullscreen({ window = \"address:${addr}\", mode = \"fullscreen\" })" >/dev/null || true
                fullscreen="$(hypr clients -j 2>/dev/null | python3 -c 'import json,sys; a=sys.argv[1]; d=json.load(sys.stdin); print(next((str(x.get("fullscreen",0)) for x in d if x.get("address")==a), "0"))' "$addr" 2>/dev/null || true)"
                [[ "$fullscreen" == "2" ]] && break
                sleep 1
            done
            return 0
        fi
        sleep 1
    done
    return 1
}

/usr/bin/firefox --name CitadelWallboard --no-remote --new-instance \
    --profile "$PROFILE" --kiosk "$URL" &
child_pid=$!
place_window "$child_pid" || true
wait "$child_pid"
