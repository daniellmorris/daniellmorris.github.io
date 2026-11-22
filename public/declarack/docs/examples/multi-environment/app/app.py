"""Real sample workload with health and Prometheus metrics endpoints."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import socket
import time
from threading import Lock

started = time.monotonic()
lock = Lock()
requests = 0

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        global requests
        with lock:
            requests += 1
            count = requests
        if self.path == '/metrics':
            content = ('# TYPE lab_requests_total counter\nlab_requests_total ' + str(count) + '\n' +
                       '# TYPE lab_uptime_seconds gauge\nlab_uptime_seconds ' + str(time.monotonic() - started) + '\n').encode()
            kind = 'text/plain; version=0.0.4'
        else:
            content = json.dumps({'status':'healthy','swarm':os.environ.get('LAB_NAME','lab'),
                                  'role':os.environ.get('LAB_ROLE','web'),'container':socket.gethostname()}).encode()
            kind = 'application/json'
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

ThreadingHTTPServer(('0.0.0.0', 8000), Handler).serve_forever()
