import os
import sys
import traceback

app_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, app_dir)
os.chdir(app_dir)

try:
    from a2wsgi import ASGIMiddleware
    from api_app import app
    application = ASGIMiddleware(app)
except Exception:
    err_msg = traceback.format_exc()
    try:
        with open(os.path.join(app_dir, "passenger_startup_error.log"), "w", encoding="utf-8") as f:
            f.write(err_msg)
    except Exception:
        pass

    def application(environ, start_response):
        start_response("500 Internal Server Error", [("Content-Type", "text/plain; charset=utf-8")])
        return [f"Passenger Startup Error:\n\n{err_msg}".encode("utf-8")]
