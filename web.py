from flask import Flask
import os
import sys

app = Flask(__name__)

@app.route('/')
def health():
    return "OK", 200

if __name__ == '__main__':
    port = int(os.getenv('PORT', 10000))
    try:
        app.run(host='0.0.0.0', port=port)
    except OSError as e:
        if "Address already in use" in str(e):
            print(f"⚠️ Порт {port} уже используется, пробуем другой...")
            # Пробуем следующий порт
            for new_port in range(port + 1, port + 10):
                try:
                    app.run(host='0.0.0.0', port=new_port)
                    break
                except:
                    continue
        else:
            raise
