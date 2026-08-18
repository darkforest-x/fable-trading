"""Static file server with wide-open CORS, for feeding images to Label Studio.

Label Studio runs on one port and the images on another, so the browser treats
the image fetch as cross-origin and the stock http.server gets rejected. This is
the same server plus the three headers Label Studio's own troubleshooting page
asks for.
"""
import argparse, functools, http.server, socketserver

class CORS(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204); self.end_headers()

    def log_message(self, *a):
        pass

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--port", type=int, default=8792)
    a = ap.parse_args()
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", a.port),
                                functools.partial(CORS, directory=a.dir)) as s:
        print(f"serving {a.dir} on 127.0.0.1:{a.port} with CORS *", flush=True)
        s.serve_forever()
