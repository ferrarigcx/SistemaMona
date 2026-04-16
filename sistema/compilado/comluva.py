from ultralytics import YOLO
import cv2
import os
from datetime import datetime
from pathlib import Path

# Configurações
MODEL_PATH = 'sistema\\modelo_treinadov2.1 (1).pt'
VIDEO_FOLDER = 'sistema\\videos'  # Pasta com vídeos para processar
SAVE_FOLDER = 'trechos_conformes'
CONFORME_CLASS = 'com luva'
TRECHO_DURACAO = 5  # segundos
FPS_PAD = 5  # margem de segurança antes/depois do trecho

# Carrega o modelo YOLOv8
model = YOLO(MODEL_PATH)
class_names = model.names  # dicionário: {0: 'com_luva', 1: 'sem_luva', ...}

# Cria pasta de saída se não existir
Path(SAVE_FOLDER).mkdir(parents=True, exist_ok=True)

def salvar_trecho(video_path, start_frame, end_frame, fps, index):
    cap = cv2.VideoCapture(video_path)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    start_frame = max(start_frame - FPS_PAD, 0)
    end_frame = min(end_frame + FPS_PAD, total_frames - 1)

    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    save_path = os.path.join(SAVE_FOLDER, f'trecho_nc_{index}_{timestamp}.mp4')

    out = cv2.VideoWriter(save_path, cv2.VideoWriter_fourcc(*'mp4v'), fps, (width, height))

    for _ in range(start_frame, end_frame + 1):
        ret, frame = cap.read()
        if not ret:
            break
        out.write(frame)

    out.release()
    cap.release()
    print(f'[✔] Trecho salvo: {save_path}')


# Processa os vídeos da pasta
for filename in os.listdir(VIDEO_FOLDER):
    if not filename.lower().endswith(('.mp4', '.avi', '.mov')):
        continue

    video_path = os.path.join(VIDEO_FOLDER, filename)
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    trecho_frames = int(TRECHO_DURACAO * fps)

    frame_index = 0
    trechos_salvos = []

    print(f'[INFO] Processando: {filename}')

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # Detecção YOLOv8
        results = model(frame, verbose=False)[0]

        for box in results.boxes:
            class_id = int(box.cls[0])
            class_name = class_names[class_id]

            if class_name == CONFORME_CLASS:
                start = max(0, frame_index - int(fps * 2))
                end = frame_index + int(fps * 3)

                # Evitar salvar trechos muito próximos
                ja_salvo = any(abs(start - salvo[0]) < fps for salvo in trechos_salvos)
                if not ja_salvo:
                    salvar_trecho(video_path, start, end, fps, len(trechos_salvos))
                    trechos_salvos.append((start, end))
                break

        frame_index += 1

    cap.release()
