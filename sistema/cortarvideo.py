import os
import subprocess

# ===== CONFIG =====
video_entrada = "video.mp4"  # nome do seu vídeo
pasta_saida = "videos_cortados"
duracao_segmento = 20  # segundos

# ===== CRIA PASTA =====
os.makedirs(pasta_saida, exist_ok=True)

# ===== COMANDO FFMPEG =====
comando = [
    "ffmpeg",
    "-i", video_entrada,
    "-c", "copy",               # não re-encode (rápido)
    "-map", "0",
    "-segment_time", str(duracao_segmento),
    "-f", "segment",
    "-reset_timestamps", "1",
    os.path.join(pasta_saida, "video_%03d.mp4")
]

# ===== EXECUTA =====
subprocess.run(comando)

print("✅ Vídeos cortados com sucesso!")