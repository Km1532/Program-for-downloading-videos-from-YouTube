import yt_dlp

def download_video(url, save_path):
    try:
        ydl_opts = {
            'outtmpl': f'{save_path}/%(title)s.%(ext)s',
            'format': 'bestvideo+bestaudio/best',
            'merge_output_format': 'mp4',  # Формат об'єднання
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print(f"Завантаження відео з {url}...")
            ydl.download([url])
        print(f"Відео успішно завантажено до {save_path}")
    except Exception as e:
        print(f"Виникла помилка: {e}")

if __name__ == "__main__":
    video_url = input("Введіть посилання на YouTube відео: ").strip()
    save_directory = input("Вкажіть шлях для збереження (наприклад, ./downloads): ").strip()
    download_video(video_url, save_directory)
