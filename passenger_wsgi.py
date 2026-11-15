import logging
import os
import sys

app_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, app_dir)
os.chdir(app_dir)

try:
    from a2wsgi import ASGIMiddleware
    from api_app import app
    application = ASGIMiddleware(app)
except Exception:
    # Full diagnostics go to Passenger stderr, never to an HTTP response or
    # a file underneath a potentially public application directory.
    logging.getLogger("nokatv.startup").exception("API startup failed")

    def application(environ, start_response):
        body = b'{"detail":"Service temporarily unavailable"}'
        start_response("503 Service Unavailable", [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("X-Robots-Tag", "noindex, nofollow"),
            ("X-Content-Type-Options", "nosniff"),
        ])
        return [body]
