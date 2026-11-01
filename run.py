"""Single-user local Windows/macOS/Linux launcher."""
import threading
import webbrowser
from app import create_app

if __name__ == '__main__':
    app = create_app()
    threading.Timer(1.5, lambda: webbrowser.open('http://127.0.0.1:5000')).start()
    app.run(host='127.0.0.1', port=5000, debug=False, threaded=False)
