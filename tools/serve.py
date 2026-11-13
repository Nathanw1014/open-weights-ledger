#!/usr/bin/env python3
"""Serve docs/ on the LAN with caching off (a reload always shows the latest data) and gzip for the big data files."""
import gzip, http.server, os, sys
class H(http.server.SimpleHTTPRequestHandler):
    zcache = {}
    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, must-revalidate"); super().end_headers()
    def log_message(self, *a): pass
    def do_GET(self):
        path = self.translate_path(self.path)
        if path.endswith(".js") and os.path.isfile(path) and "gzip" in self.headers.get("Accept-Encoding", ""):
            st = os.stat(path); key = (path, st.st_mtime_ns, st.st_size)
            if key not in self.zcache:
                for k in [k for k in self.zcache if k[0] == path]: del self.zcache[k]
                self.zcache[key] = gzip.compress(open(path, "rb").read(), 6)
            body = self.zcache[key]
            self.send_response(200); self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Content-Encoding", "gzip"); self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body); return
        super().do_GET()
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs"))
http.server.ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1]) if len(sys.argv) > 1 else 8790), H).serve_forever()
