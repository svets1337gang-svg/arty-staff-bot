from flask import Flask
import threading
import os
import subprocess

app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Бот ArtyStaff работает!"

@app.route('/health')
def health():
    return "OK", 200

def run_bot():
    os.system("python bot.py")

if __name__ == '__main__':
    thread = threading.Thread(target=run_bot, daemon=True)
    thread.start()
    app.run(host='0.0.0.0', port=7860)