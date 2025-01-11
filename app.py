from flask import Flask, render_template, request, jsonify, send_file
from flask_socketio import SocketIO, emit
import os
import yt_dlp
import logging
import unicodedata
import ffmpeg
import time
from threading import Lock
import psutil

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
        progress = (d.get('downloaded_bytes', 0) / d.get('total_bytes', 1)) * 100
        logger.debug(f"Progress: {progress:.2f}%")
        socketio.emit('progress', {'progress': progress, 'status': 'Завантаження відео...'})
    elif d['status'] == 'finished':
        logger.debug("Download finished. Moving to processing stage.")
        socketio.emit('progress', {'progress': 50, 'status': 'Завантаження завершено, обробка...'})

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

        # Лог прогресу FFmpeg
        while True:
            output = process.stderr.readline().decode('utf-8', errors='ignore')
            if output:
                logger.debug(f"FFmpeg: {output.strip()}")
            if process.poll() is not None:
                break
            time.sleep(1)

        if os.path.exists(fixed_file_name):
            logger.debug(f"Processed file created: {fixed_file_name}")
            processed_files[task_id] = fixed_file_name  # Збереження шляху до обробленого файлу
            socketio.emit('progress', {'progress': 100, 'status': 'Файл готовий!'})
            socketio.emit('file_ready', {'task_id': task_id})  # Додано для сповіщення клієнта
            return fixed_file_name
        else:
            logger.error(f"Processed file not found: {fixed_file_name}")
            socketio.emit('error', {'message': 'Файл не знайдено після обробки.'})
            return None
    except Exception as e:
        logger.error(f"Error during video processing: {e}")
        socketio.emit('error', {'message': 'Помилка обробки відео.'})
        return None

def download_and_process(url):
    task_id = str(time.time())  # Унікальний ідентифікатор завдання
    with download_lock:
        try:
            logger.debug(f"Starting download for URL: {url}")

            ydl_opts = {
                'outtmpl': f'{DOWNLOAD_FOLDER}/%(title)s.%(ext)s',
                'noplaylist': True,
                'format': 'bestaudio/best',
                'logger': logger,
                'progress_hooks': [progress_hook],
                'headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36',
                },
                'geo_bypass': True,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(url, download=True)
                file_name = ydl.prepare_filename(info_dict)

                logger.debug(f"Video downloaded: {file_name}")

                # Processing video after download
                fixed_file = process_video(file_name, task_id)

                if fixed_file:
                    logger.debug(f"Processed file: {fixed_file}")
                else:
                    logger.error("Video processing failed.")
                    socketio.emit('error', {'message': 'Помилка обробки відео.'})

        except yt_dlp.utils.DownloadError as e:
            logger.error(f"yt-dlp error occurred: {str(e)}")
            socketio.emit('error', {'message': 'Помилка завантаження відео.'})
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            socketio.emit('error', {'message': 'Непередбачена помилка.'})

    return task_id

def kill_process_holding_file(file_path):
    """Завершує процес, що тримає файл."""
    for proc in psutil.process_iter(['pid', 'name', 'open_files']):
        try:
            for file in proc.info['open_files'] or []:
                if file.path == file_path:
                    logger.debug(f"Знайдено процес {proc.info['name']} (PID: {proc.info['pid']}) що тримає файл {file_path}")
                    proc.terminate()  # Спроба завершити процес
                    proc.wait()  # Чекати завершення процесу
                    logger.debug(f"Завершено процес {proc.info['name']} (PID: {proc.info['pid']}) що тримав файл {file_path}")
                    return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return False

def safe_remove(file_path, retries=3, delay=1):
    """Спроба видалити файл з кількома спробами."""
    for attempt in range(retries):
        try:
            os.remove(file_path)
            logger.debug(f"Файл {file_path} успішно видалено.")
            return True
        except PermissionError as e:
            logger.error(f"Спроба {attempt + 1} видалити {file_path} не вдалася: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                logger.error(f"Не вдалося видалити {file_path} після {retries} спроб.")
                return False
    return False
def wait_for_file(file_path, timeout=30, delay=1):
    """Чекає на наявність файлу перед його відправкою."""
    start_time = time.time()
    while not os.path.exists(file_path):
        if time.time() - start_time > timeout:
            logger.error(f"File {file_path} not found after waiting for {timeout} seconds.")
            return False
        time.sleep(delay)
    return True
@app.route("/download")
def download_ready():
    task_id = request.args.get('task_id')
    logger.debug(f"Received task_id: {task_id}")

    fixed_file = processed_files.get(task_id)

    if not fixed_file:
        logger.error(f"File not found for task_id: {task_id}")
        return jsonify({"error": "File not found"}), 404

    # Перевірка, чи файл готовий
    if not os.path.exists(fixed_file):
        logger.error(f"File does not exist or cannot be accessed: {fixed_file}")
        return jsonify({"error": "File not found"}), 404

    logger.debug(f"File found: {fixed_file}")

    return send_file(fixed_file, as_attachment=True, mimetype='video/mp4')

if __name__ == "__main__":
    socketio.run(app, debug=True)
