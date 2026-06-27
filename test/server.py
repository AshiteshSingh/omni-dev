import http.server
import socketserver
import os

PORT = 8000
DIRECTORY = "test"

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print(f"Serving Todo App at http://localhost:{PORT}")
    print("Press Ctrl+C to stop the server")
    httpd.serve_forever()