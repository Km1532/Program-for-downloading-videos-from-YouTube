from flask import Flask, render_template, request, jsonify, send_file
from flask_socketio import SocketIO, emit
import os
import yt_dlp
import logging
import unicodedata
import ffmpeg
import time
from threading import Lock

app = Flask(__name__)
app.secret_key = 'your_secret_key'
socketio = SocketIO(app, async_mode='threading')

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# Logging setup
logger = logging.getLogger("YouTubeDownloader")
logger.setLevel(logging.DEBUG)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# Lock for synchronization
download_lock = Lock()

@app.route("/")
def index():
    return render_template("index.html")

@socketio.on('start_download')
def start_download(data):
    url = data.get('url')
    if not url:
        emit('error', {'message': 'URL не вказано'})
        return

    socketio.start_background_task(target=download_and_process, url=url)

def progress_hook(d):
    logger.debug(f"Progress hook received data: {d}")
    
    if d['status'] == 'downloading':
        # Оновлення прогресу від 0% до 100%
        progress = (d.get('downloaded_bytes', 0) / d.get('total_bytes', 1)) * 100
        speed = d.get('speed', 0)
        eta = d.get('eta', 0)
        
        socketio.emit('progress', {
            'progress': progress,
            'status': 'Завантаження...',
            'speed': f"{speed / 1024:.2f} KB/s" if speed else 'N/A',
            'eta': f"{eta} сек." if eta else 'N/A'
        })
    elif d['status'] == 'finished':
        socketio.emit('progress', {'progress': 100, 'status': 'Завантаження завершено, обробка...'})
        time.sleep(2)  # Маленька затримка перед відправленням готового файлу

processed_files = {}

def process_video(file_name, task_id):
    try:
        logger.debug(f"Processing video: {file_name}")
        file_name = unicodedata.normalize('NFC', file_name)
        fixed_file_name = os.path.join(DOWNLOAD_FOLDER, f"fixed_{os.path.splitext(os.path.basename(file_name))[0]}.mp4")
        fixed_file_name = unicodedata.normalize('NFC', fixed_file_name)

        process = (
            ffmpeg
            .input(file_name)
            .output(fixed_file_name, r=30, vcodec='libx264', acodec='aac', strict='-2')
            .run_async(pipe_stdout=True, pipe_stderr=True)
        )

        while True:
            output = process.stderr.readline().decode('utf-8', errors='ignore')
            if output:
                logger.debug(f"FFmpeg: {output.strip()}")
            if process.poll() is not None:
                break
            time.sleep(1)

        if os.path.exists(fixed_file_name):
            processed_files[task_id] = fixed_file_name
            socketio.emit('progress', {'progress': 100, 'status': 'Файл готовий!'})
            socketio.emit('file_ready', {'task_id': task_id})
            return fixed_file_name
        else:
            socketio.emit('error', {'message': 'Файл не знайдено після обробки.'})
            return None
    except Exception as e:
        socketio.emit('error', {'message': 'Помилка обробки відео.'})
        return None

def download_and_process(url):
    task_id = str(time.time())
    with download_lock:
        try:
            # Спочатку надсилаємо 0% прогрес
            socketio.emit('progress', {'progress': 0, 'status': 'Завантаження...'})
            
            ydl_opts = {
                'outtmpl': f'{DOWNLOAD_FOLDER}/%(title)s.%(ext)s',
                'format': 'bestaudio/best',
                'logger': logger,
                'progress_hooks': [progress_hook],
                'headers': {
                    'User-Agent': 'Mozilla/5.0'
                },
                'geo_bypass': True,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(url, download=False)
                title = info_dict.get('title')

                socketio.emit('video_info', {'title': title})

                # Завантаження відео
                info_dict = ydl.extract_info(url, download=True)
                file_name = ydl.prepare_filename(info_dict)

                fixed_file = process_video(file_name, task_id)
                if fixed_file:
                    logger.debug(f"Processed file: {fixed_file}")
        except yt_dlp.utils.DownloadError as e:
            socketio.emit('error', {'message': 'Помилка завантаження відео.'})
        except Exception as e:
            socketio.emit('error', {'message': 'Непередбачена помилка.'})

    return task_id
@app.route("/download")
def download_ready():
    task_id = request.args.get('task_id')
    fixed_file = processed_files.get(task_id)

    if not fixed_file or not os.path.exists(fixed_file):
        return jsonify({"error": "File not found"}), 404

    return send_file(fixed_file, as_attachment=True, mimetype='video/mp4')

if __name__ == "__main__":
    socketio.run(app, debug=True)
