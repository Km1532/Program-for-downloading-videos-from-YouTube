from flask import Flask, render_template, request, jsonify, redirect, send_file, session
from flask_socketio import SocketIO
import os
import yt_dlp
import logging
import unicodedata
import ffmpeg
import time
import threading

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


@app.route("/")
def index():
    return render_template("index.html")
    
@app.route("/", methods=["POST"])
def download_video():
    url = request.form.get("url")
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    try:
        logger.debug(f"Received URL: {url}")

        def progress_hook(d):
            if d['status'] == 'downloading':
                progress = (d['downloaded_bytes'] / d['total_bytes']) * 100
                socketio.emit('progress', {'progress': progress, 'status': 'Завантаження відео...'}, to='/')
            elif d['status'] == 'finished':
                socketio.emit('progress', {'progress': 50, 'status': 'Завантаження завершено, обробка...'}, to='/')
                logger.debug(f"Download finished: {d['filename']}")

        def process_video(file_name):
            try:
                file_name = unicodedata.normalize('NFC', file_name)
                fixed_file_name = os.path.join(DOWNLOAD_FOLDER, f"fixed_{os.path.splitext(os.path.basename(file_name))[0]}.mp4")
                fixed_file_name = unicodedata.normalize('NFC', fixed_file_name)

                start_time = time.time()
                process = ffmpeg.input(file_name).output(fixed_file_name, 
                                                        r=30, vcodec='libx264', acodec='aac', strict='-2').run_async(pipe_stdout=True, pipe_stderr=True)

                while True:
                    if process.poll() is not None:
                        break
                    time.sleep(1)

                logger.debug(f"FFmpeg execution time: {time.time() - start_time:.2f} seconds")

                # Ensure the file is created before proceeding
                if os.path.exists(fixed_file_name):
                    logger.debug(f"Processed file created: {fixed_file_name}")
                    socketio.emit('progress', {'progress': 100, 'status': 'Файл готовий!'}, to='/')
                    session['fixed_file'] = fixed_file_name  # Save file path to session
                    return fixed_file_name
                else:
                    logger.error(f"Processed file not found: {fixed_file_name}")
                    return None
            except Exception as e:
                logger.error(f"Error during video processing: {e}")
                return None

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

            # Download video again after removing if needed
            if os.path.exists(file_name):
                os.remove(file_name)

            ydl.download([url])

            # Process the video directly in the main thread
            fixed_file = process_video(file_name)

            # Check if the fixed file was created successfully
            if fixed_file:
                return redirect('/download')
            else:
                return jsonify({"error": "Error during video processing"}), 500

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"yt-dlp error occurred: {str(e)}")
        return jsonify({"error": f"Download error: {str(e)}"}), 500

    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500

@app.route("/download")
def download_ready():
    fixed_file = session.get('fixed_file')
    logger.debug(f"Fixed file from session: {fixed_file}")
    
    if not fixed_file or not os.path.exists(fixed_file):
        logger.error(f"File not found: {fixed_file}")
        return jsonify({"error": "File not found"}), 404
    
    return send_file(fixed_file, as_attachment=True)
if __name__ == "__main__":
    socketio.run(app, debug=True)
