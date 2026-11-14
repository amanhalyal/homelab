#!/usr/bin/env python3
import csv
import io
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

QUERY = 'index,uuid,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu'


def metrics():
    try:
        raw = subprocess.check_output(
            ['nvidia-smi', f'--query-gpu={QUERY}', '--format=csv,noheader,nounits'],
            text=True, stderr=subprocess.DEVNULL, timeout=5)
    except Exception:
        return '# HELP nexus_gpu_exporter_up Whether nvidia-smi succeeded\n# TYPE nexus_gpu_exporter_up gauge\nnexus_gpu_exporter_up 0\n'
    out = ['# HELP nexus_gpu_exporter_up Whether nvidia-smi succeeded', '# TYPE nexus_gpu_exporter_up gauge', 'nexus_gpu_exporter_up 1']
    for row in csv.reader(io.StringIO(raw), skipinitialspace=True):
        if len(row) != 7: continue
        idx, uuid, util, memutil, used, total, temp = row
        labels = f'gpu="{idx}",uuid="{uuid}"'
        out += [
            f'nexus_gpu_utilization_percent{{{labels}}} {float(util)}',
            f'nexus_gpu_memory_utilization_percent{{{labels}}} {float(memutil)}',
            f'nexus_gpu_memory_used_bytes{{{labels}}} {float(used) * 1024 * 1024}',
            f'nexus_gpu_memory_total_bytes{{{labels}}} {float(total) * 1024 * 1024}',
            f'nexus_gpu_temperature_celsius{{{labels}}} {float(temp)}',
        ]
    return '\n'.join(out) + '\n'


class Handler(BaseHTTPRequestHandler):
    # A TCP probe without an HTTP request must not monopolize the exporter.
    timeout = 10

    def do_GET(self):
        if self.path != '/metrics':
            self.send_response(404); self.end_headers(); return
        body = metrics().encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; version=0.0.4')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def log_message(self, format, *args): pass

if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 9836), Handler).serve_forever()
