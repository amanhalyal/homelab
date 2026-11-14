#!/bin/bash
# Publish the current native wallboard and Home Assistant API health.
# PUT replaces this Pushgateway group, removing retired Telegram series.
check() {
    case "$1" in
      wallboard-server.service|wallboard-rotation.service|wallboard-kiosk.service)
        XDG_RUNTIME_DIR=/run/user/1000 systemctl --user is-active --quiet "$1" && echo 1 || echo 0
        ;;
      *) systemctl is-active --quiet "$1" && echo 1 || echo 0 ;;
    esac
}
wallboard_up=$(check wallboard-server.service)
rotation_up=$(check wallboard-rotation.service)
kiosk_up=$(check wallboard-kiosk.service)
control_up=$(check heimdall-control.service)
server_up=$(curl -sf --max-time 5 -o /dev/null http://localhost:8080/current && echo 1 || echo 0)
control_http_up=$(curl -sf --max-time 5 -o /dev/null http://100.64.0.2:8766/health && echo 1 || echo 0)
cat << METRICS | timeout 20 ssh -o BatchMode=yes -o ConnectTimeout=8 -o IPQoS=none legion "curl -fsS -X PUT --data-binary @- http://127.0.0.1:9091/metrics/job/heimdall/instance/citadel"
# TYPE heimdall_service_up gauge
heimdall_service_up{service="wallboard"} $wallboard_up
heimdall_service_up{service="wallboard-rotation"} $rotation_up
heimdall_service_up{service="wallboard-kiosk"} $kiosk_up
heimdall_service_up{service="heimdall-control"} $control_up
heimdall_service_up{service="control-http"} $control_http_up
heimdall_service_up{service="server-http"} $server_up
# TYPE heimdall_healthcheck_last_run_timestamp gauge
heimdall_healthcheck_last_run_timestamp $(date +%s)
METRICS
