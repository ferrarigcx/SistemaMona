import os
import queue
import shutil
import subprocess
import sys
import time
from pathlib import Path
from threading import Event, Thread
from typing import Generator, Optional

import gradio as gr
import numpy as np
from PIL import Image

BASE_DIR = Path(__file__).resolve().parent
AUTOMATOS_SCRIPT = BASE_DIR / "automatos.py"
MOCK_SCRIPT = BASE_DIR / "mock.py"
STOP_EVENT = Event()
FRAME_RUNTIME_DIR = BASE_DIR / "_runtime_frames"


class FrameWatcher:
    def __init__(self, frame_path: Path):
        self.frame_path = frame_path
        self._last_mtime = 0.0

    def poll(self) -> Optional[Image.Image]:
        try:
            stat = self.frame_path.stat()
        except FileNotFoundError:
            return None
        except Exception:
            return None
        mtime = getattr(stat, "st_mtime", None)
        if mtime is None or mtime <= self._last_mtime:
            return None
        self._last_mtime = mtime
        try:
            with Image.open(self.frame_path) as img:
                frame = img.convert("RGB").copy()
        except Exception:
            return None
        return frame


def _prepare_frame_output() -> tuple[Path, FrameWatcher]:
    FRAME_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    frame_path = FRAME_RUNTIME_DIR / "stream.jpg"
    try:
        frame_path.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass
    watcher = FrameWatcher(frame_path)
    return frame_path, watcher


def _cleanup_frame_output(frame_watcher: Optional[FrameWatcher]) -> None:
    if not frame_watcher:
        return
    try:
        frame_watcher.frame_path.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _image_to_numpy(img: Image.Image) -> "np.ndarray":
    """Converte imagem PIL em array RGB (uint8) para o Gradio."""
    arr = np.array(img, copy=True)
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)
    return arr


def _save_upload(upload) -> Optional[Path]:
    if upload is None:
        return None
    upload_dir = BASE_DIR / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    original = Path(getattr(upload, "name", "upload.bin"))
    safe_stem = "".join(ch for ch in original.stem if ch.isalnum() or ch in ("-", "_")) or "upload"
    safe_name = f"{safe_stem}_{int(time.time())}{original.suffix or '.bin'}"
    dest = upload_dir / safe_name
    shutil.copy(original, dest)
    return dest


def _relative_to_base(path: Path) -> str:
    try:
        rel = path.relative_to(BASE_DIR)
    except ValueError:
        return str(path)
    return str(rel)


def _discover_model_choices() -> tuple[list[tuple[str, str]], str]:
    search_roots = [BASE_DIR, BASE_DIR.parent]
    seen: set[Path] = set()
    found: list[Path] = []
    for root in search_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.pt"):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(resolved)

    def _sort_key(path: Path) -> tuple[int, str]:
        preferred = 0 if path.name == "ModeloTreinadoV3.pt" else 1
        return (preferred, path.name.lower(), str(path).lower())

    found.sort(key=_sort_key)

    choices: list[tuple[str, str]] = []
    default_value = ""
    for path in found:
        try:
            rel_to_parent = path.relative_to(BASE_DIR.parent)
            label = str(rel_to_parent)
        except ValueError:
            label = path.name
        value = os.path.relpath(path, BASE_DIR)
        choices.append((label, value))
        if not default_value and path.name == "ModeloTreinadoV3.pt":
            default_value = value

    if not default_value and choices:
        default_value = choices[0][1]
    return choices, default_value


def _stream_process(
    cmd: list[str],
    env: dict,
    frame_watcher: Optional[FrameWatcher] = None,
) -> Generator[tuple[str, Optional[Image.Image]], None, None]:
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=True,
            encoding='utf-8',
            errors='replace',
            env=env,
        )
    except Exception as exc:
        yield f"Falha ao iniciar o processo: {exc}", None
        return

    if proc.stdout is None:
        yield "Processo iniciado sem stdout redirecionado.", None
        return

    buffer = ""
    current_image: Optional[Image.Image] = None
    sentinel = object()
    line_queue: "queue.Queue[object]" = queue.Queue()

    def _reader():
        try:
            for line in proc.stdout:  # type: ignore[arg-type]
                line_queue.put(line)
        finally:
            line_queue.put(sentinel)

    Thread(target=_reader, daemon=True).start()
    stop_requested = False

    while True:
        try:
            line = line_queue.get(timeout=0.2)
        except queue.Empty:
            line = None
        else:
            if line is sentinel:
                break
            line = str(line).rstrip("\r\n")
            buffer += line + "\n"

        if STOP_EVENT.is_set() and not stop_requested:
            proc.terminate()
            stop_requested = True
            buffer += "\nExecucao interrompida pelo usuario."

        new_image = frame_watcher.poll() if frame_watcher else None
        if new_image is not None:
            current_image = new_image

        if line is not None or new_image is not None or stop_requested:
            yield buffer, current_image

        if stop_requested:
            break

    if stop_requested:
        try:
            ret = proc.wait(timeout=5)
        except Exception:
            ret = 'desconhecido'
        buffer += f"\nProcesso encerrado (codigo {ret})."
        _cleanup_frame_output(frame_watcher)
        yield buffer, None
        return

    ret = proc.wait()
    buffer += f"\nProcesso finalizado com codigo {ret}."
    _cleanup_frame_output(frame_watcher)
    yield buffer, None

def executar(
    mode: str,
    runner: str,
    source_text: str,
    upload,
    model_path: str,
    iou_q3: float,
) -> Generator[tuple[str, Optional[Image.Image]], None, None]:
    STOP_EVENT.clear()
    runner = (runner or "automatos").strip().lower()
    mode = (mode or "treinamento").strip().lower()
    script = MOCK_SCRIPT if runner == "mock" else AUTOMATOS_SCRIPT
    if not script.exists():
        yield f"Arquivo {script.name} não encontrado em {script}.", None
        return

    src = (source_text or "").strip()
    if runner != "mock":
        if upload is not None:
            saved = _save_upload(upload)
            if saved:
                src = _relative_to_base(saved)
        if not src:
            src = "0"

    cmd = [sys.executable, "-X", "utf8", "-u", str(script)]
    if runner != "mock":
        cmd += ["--source", src]
        if model_path.strip():
            cmd += ["--model", model_path.strip()]

    env = os.environ.copy()
    env.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "MODO": mode,
        }
    )
    frame_watcher: Optional[FrameWatcher] = None
    if runner != "mock":
        env["IOU_THR_Q3"] = str(iou_q3 or 0.02)
        frame_path, frame_watcher = _prepare_frame_output()
        env["AUTOMATOS_FRAME_PATH"] = str(frame_path)

    last_frame = None
    for logs, pil_image in _stream_process(cmd, env, frame_watcher):
        if pil_image is not None:
            last_frame = _image_to_numpy(pil_image)
        yield logs, last_frame


def parar(log_atual: str) -> str:
    STOP_EVENT.set()
    prefix = log_atual or ""
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    return prefix + "[usuario] parada solicitada."


with gr.Blocks(title="Automatos - Controle") as demo:
    model_choices, default_model = _discover_model_choices()
    gr.Markdown(
        "## Automatos (Gradio)\n"
        "Execute o pipeline `automatos.py` ou `mock.py`, enviando vídeos/imagens "
        "ou usando webcam/local diretamente."
    )
    with gr.Row():
        modo = gr.Radio(
            ["treinamento", "auditoria"],
            value="treinamento",
            label="Modo (variável MODO)",
        )
        processo = gr.Radio(
            ["automatos", "mock"],
            value="automatos",
            label="Processo",
        )
        iou_slider = gr.Slider(
            minimum=0.0,
            maximum=1.0,
            value=0.02,
            step=0.01,
            label="IoU q3",
            info="Limiar enviado via IOU_THR_Q3",
        )
    source_box = gr.Textbox(
        value="0",
        label="Fonte (--source)",
        placeholder="Índice da webcam ou caminho",
    )
    upload_box = gr.File(
        label="Upload (alternativa ao --source)",
        file_count="single",
        file_types=["video", "image"],
    )
    model_box = gr.Dropdown(
        choices=model_choices,
        value=default_model,
        label="Modelo (.pt)",
        info="Escolha um modelo encontrado no projeto ou digite um caminho customizado",
        allow_custom_value=True,
    )
    executar_btn = gr.Button("Executar")
    parar_btn = gr.Button("Parar", variant="stop")
    with gr.Row():
        log_box = gr.Textbox(
            label="Logs em tempo real",
            lines=20,
            interactive=False,
        )
        video_box = gr.Image(
            label="Visualização YOLO",
            interactive=False,
            height=360,
            type="numpy",
        )

    executar_btn.click(
        fn=executar,
        inputs=[modo, processo, source_box, upload_box, model_box, iou_slider],
        outputs=[log_box, video_box],
    )
    parar_btn.click(parar, inputs=log_box, outputs=log_box)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    demo.queue().launch(server_name="0.0.0.0", server_port=port, show_error=True)
