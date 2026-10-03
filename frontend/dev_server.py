from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
from urllib.parse import urlsplit


FRONTEND_DIR = Path(__file__).resolve().parent
IMAGE_PATH = FRONTEND_DIR / "public" / "aetherai-login.png"


class FrontendHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)

    def translate_path(self, path):
        if urlsplit(path).path == "/aetherai-login.png":
            return str(IMAGE_PATH)
        return super().translate_path(path)


class FrontendServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        super().server_bind()


if __name__ == "__main__":
    server = FrontendServer(("::1", 8080), FrontendHandler)
    print("AetherAI frontend available at http://localhost:8080")
    server.serve_forever()