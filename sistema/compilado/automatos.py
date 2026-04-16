import hashlib
import os
import re
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Optional

from PIL import Image

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
SHOW_GUI = os.getenv("AUTOMATOS_SHOW_GUI", "0").strip().lower() in {"1", "true", "yes", "on"}

BASE_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = BASE_DIR.parent / "besu" / "smart_contracts" / "scripts" / "public"
HRE_HASH_JS = SCRIPTS_DIR / "hre_hash.js"
HRE_FOUND_JS = SCRIPTS_DIR / "hre_found.js"
PADRAO_ENDERECO = re.compile(r"^0x[0-9a-fA-F]{40}$")
FRAME_OUTPUT_PATH_STR = os.getenv("AUTOMATOS_FRAME_PATH", "").strip()
FRAME_OUTPUT_PATH: Optional[Path] = None
TRECHOS_DIR = BASE_DIR / "trechos_nao_conformes"
PRE_EVENT_SECONDS = float(os.getenv("NC_PRE_SECONDS", "3"))
POST_EVENT_SECONDS = float(os.getenv("NC_POST_SECONDS", "1"))
MAX_EVENT_SECONDS = float(os.getenv("NC_MAX_SECONDS", "20"))
DISPLAY_FPS = float(os.getenv("AUTOMATOS_DISPLAY_FPS", "12"))
ANALYSIS_FPS = float(os.getenv("AUTOMATOS_ANALYSIS_FPS", "8"))
MEASUREMENT_SECONDS = float(os.getenv("AUTOMATOS_MEASUREMENT_SECONDS", "20"))
if FRAME_OUTPUT_PATH_STR:
    try:
        FRAME_OUTPUT_PATH = Path(FRAME_OUTPUT_PATH_STR)
        FRAME_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        FRAME_OUTPUT_PATH = None


def centro(box):
    x1, y1, x2, y2 = box
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def esta_dentro(centro, box):
    x1, y1, x2, y2 = box
    cx, cy = centro
    return x1 <= cx <= x2 and y1 <= cy <= y2

# Limiar IoU para considerar "amostra dentro da cesta" em q3.
# Pode ser ajustado via variável de ambiente IOU_THR_Q3.
IOU_THR_Q3 = float(os.getenv("IOU_THR_Q3", "0.02"))
FRAC_THR_Q3 = float(os.getenv("FRAC_THR_Q3", "0.30"))  # fração mínima de cobertura da amostra

def iou_xyxy(a, b) -> float:
    try:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
    except Exception:
        return 0.0
    inter_w = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    inter_h = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = inter_w * inter_h
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return inter / union

def inter_area(a, b) -> float:
    try:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
    except Exception:
        return 0.0
    inter_w = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    inter_h = max(0.0, min(ay2, by2) - max(ay1, by1))
    return inter_w * inter_h

def area(box) -> float:
    try:
        x1, y1, x2, y2 = box
    except Exception:
        return 0.0
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def _normalizar_label(label) -> str:
    texto = str(label or "").strip().lower()
    texto = texto.replace("_", " ")
    texto = " ".join(texto.split())
    if texto == "com luva":
        return "mao com luva"
    if texto == "sem luva":
        return "mao sem luva"
    return texto


def _normalizar_objetos(objetos) -> list[str]:
    return [_normalizar_label(obj) for obj in (objetos or [])]


def detectar_nao_conformidade_critica(objetos) -> Optional[str]:
    objetos_norm = _normalizar_objetos(objetos)
    tem_sem_luva = "mao sem luva" in objetos_norm
    tem_ativimetro = "ativimetro" in objetos_norm
    if tem_sem_luva and not tem_ativimetro:
        return "mao sem luva e ativimetro ausente"
    if tem_sem_luva:
        return "mao sem luva"
    if not tem_ativimetro:
        return "ativimetro ausente"
    return None


class NonConformityClipper:
    def __init__(
        self,
        source_path: Optional[Path],
        fps: float,
        blockchain_client: Optional["BlockchainClient"],
        output_dir: Path = TRECHOS_DIR,
        pre_seconds: float = PRE_EVENT_SECONDS,
        post_seconds: float = POST_EVENT_SECONDS,
        max_seconds: float = MAX_EVENT_SECONDS,
    ):
        self.source_path = source_path if source_path and source_path.is_file() else None
        self.output_dir = output_dir
        self.pre_seconds = max(0.0, pre_seconds)
        self.post_seconds = max(0.0, post_seconds)
        self.max_seconds = max(1.0, max_seconds)
        self.fps = fps if fps and fps > 0 else 30.0
        self.blockchain_client = blockchain_client if blockchain_client and blockchain_client.enabled else None
        self.event_open = False
        self.event_reason = ""
        self.event_reasons = set()
        self.last_active_ts = 0.0
        self.last_seen_ts = 0.0
        self.clip_start_ts = 0.0
        self.clip_output_path: Optional[Path] = None
        self.clip_writer = None
        self.clip_frame_count = 0
        self.clip_last_written_ts: Optional[float] = None
        self.clip_counter = 0
        self.recent_frames = deque()
        self.max_recent_frames = max(1, int(round(self.pre_seconds * self.fps)) + 2)

        self.enabled = self.source_path is not None
        if self.enabled:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self._log(
                f"recorte ativo para '{self.source_path.name}' com janela {self.pre_seconds:.1f}s antes, {self.post_seconds:.1f}s depois e maximo de {self.max_seconds:.1f}s por trecho"
            )
        else:
            self._log("recorte de trechos desativado para esta fonte.")

    def _log(self, msg: str) -> None:
        print(f"[trechos] {msg}")

    def _sanitize_label(self, value: str) -> str:
        texto = _normalizar_label(value)
        texto = texto.replace(" ", "_")
        texto = re.sub(r"[^a-z0-9_]+", "", texto)
        return texto or "nao_conformidade"

    def _make_output_path(self, reason: str, start_sec: float) -> Path:
        reason_slug = self._sanitize_label("_".join(sorted(self.event_reasons or {reason})))
        start_tag = int(max(0.0, start_sec) * 1000)
        source_stem = self._sanitize_label(self.source_path.stem if self.source_path else "video")
        self.clip_counter += 1
        return self.output_dir / f"trecho_{source_stem}_{reason_slug}_{start_tag}_{self.clip_counter:03d}.mp4"

    def _finalize_output_path(self, output_path: Path) -> Path:
        reason_slug = self._sanitize_label("_".join(sorted(self.event_reasons or {"nao_conformidade"})))
        start_tag = int(max(0.0, self.clip_start_ts) * 1000)
        source_stem = self._sanitize_label(self.source_path.stem if self.source_path else "video")
        final_path = self.output_dir / f"trecho_{source_stem}_{reason_slug}_{start_tag}_{self.clip_counter:03d}.mp4"
        if final_path == output_path:
            return output_path
        try:
            os.replace(output_path, final_path)
            return final_path
        except OSError as exc:
            self._log(f"falha ao renomear trecho '{output_path.name}': {exc}")
            return output_path

    def _append_recent(self, timestamp_sec: float, frame) -> None:
        self.recent_frames.append((timestamp_sec, frame.copy()))
        while len(self.recent_frames) > self.max_recent_frames:
            self.recent_frames.popleft()
        min_ts = max(0.0, timestamp_sec - self.pre_seconds)
        while self.recent_frames and self.recent_frames[0][0] < min_ts:
            self.recent_frames.popleft()

    def _open_writer(self, frame, output_path: Path) -> bool:
        import cv2

        height, width = frame.shape[:2]
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self.clip_writer = cv2.VideoWriter(str(output_path), fourcc, self.fps, (width, height))
        if not self.clip_writer.isOpened():
            self.clip_writer = None
            self._log(f"falha ao abrir writer para '{output_path.name}'")
            return False
        self.clip_output_path = output_path
        self.clip_frame_count = 0
        self.clip_last_written_ts = None
        return True

    def _write_frame(self, timestamp_sec: float, frame) -> None:
        if self.clip_writer is None:
            return
        if self.clip_last_written_ts is not None and abs(timestamp_sec - self.clip_last_written_ts) < 1e-6:
            return
        self.clip_writer.write(frame)
        self.clip_frame_count += 1
        self.clip_last_written_ts = timestamp_sec

    def _start_event(self, timestamp_sec: float, frame, reason: str, continued: bool = False) -> None:
        self.event_open = True
        self.event_reason = reason
        self.event_reasons = {reason}
        self.last_active_ts = timestamp_sec
        self.last_seen_ts = timestamp_sec
        self.clip_start_ts = timestamp_sec if continued else max(0.0, timestamp_sec - self.pre_seconds)
        output_path = self._make_output_path(reason, self.clip_start_ts)
        if not self._open_writer(frame, output_path):
            self.event_open = False
            self.event_reason = ""
            self.event_reasons = set()
            return
        if continued:
            self._log(f"continuando gravacao em novo trecho a partir de {self.clip_start_ts:.2f}s por motivo: {reason}")
            self._write_frame(timestamp_sec, frame)
            return

        self._log(f"iniciando gravacao do trecho em {timestamp_sec:.2f}s por motivo: {reason}")
        for buffered_ts, buffered_frame in self.recent_frames:
            if buffered_ts >= self.clip_start_ts:
                self._write_frame(buffered_ts, buffered_frame)

    def _reset_clip_state(self) -> None:
        self.event_open = False
        self.event_reason = ""
        self.event_reasons = set()
        self.clip_writer = None
        self.clip_output_path = None
        self.clip_frame_count = 0
        self.clip_last_written_ts = None

    def _close_event(self, end_sec: float) -> None:
        output_path = self.clip_output_path
        writer = self.clip_writer
        written_frames = self.clip_frame_count
        if writer is not None:
            writer.release()
        if output_path:
            output_path = self._finalize_output_path(output_path)
            self._log(
                f"trecho salvo em '{output_path.name}' de {self.clip_start_ts:.2f}s ate {end_sec:.2f}s com {written_frames} frames"
            )
            if self.blockchain_client:
                descricao = f"Trecho nao conforme: {', '.join(sorted(self.event_reasons))}"
                self.blockchain_client.register_file(output_path, descricao)
        self._reset_clip_state()

    def _rotate_event(self, timestamp_sec: float, frame, reason: Optional[str]) -> None:
        self._log(f"duracao maxima de {self.max_seconds:.1f}s atingida; salvando trecho e iniciando novo.")
        self._close_event(self.clip_start_ts + self.max_seconds)
        if reason:
            self._start_event(timestamp_sec, frame, reason, continued=True)

    def ingest(self, timestamp_sec: float, frame, reason: Optional[str]) -> None:
        if not self.enabled:
            return
        self.last_seen_ts = timestamp_sec
        self._append_recent(timestamp_sec, frame)
        if reason and not self.event_open:
            self._start_event(timestamp_sec, frame, reason)
        if self.event_open:
            if reason:
                if reason not in self.event_reasons:
                    self._log(f"gravacao em andamento; novo motivo detectado: {reason}")
                self.event_reasons.add(reason)
                self.last_active_ts = timestamp_sec
            if self.clip_writer is not None:
                self._write_frame(timestamp_sec, frame)
            if timestamp_sec - self.clip_start_ts >= self.max_seconds:
                self._rotate_event(timestamp_sec, frame, reason)
                return
            if (not reason) and (timestamp_sec - self.last_active_ts >= self.post_seconds):
                self._log(f"nao conformidade encerrada em {timestamp_sec:.2f}s")
                self._close_event(self.last_active_ts + self.post_seconds)

    def finish(self) -> None:
        if not self.enabled or not self.event_open:
            return
        self._log("fim do video com nao conformidade ainda aberta; salvando trecho final.")
        self._close_event(self.last_seen_ts)


class BlockchainClient:
    """Integração simples com os scripts Node para registrar hashes na blockchain."""

    def __init__(self, base_dir: Path, source: Optional[str]):
        self.base_dir = base_dir
        self.source_input = (source or "").strip()
        self.source_path = self._resolve_source(self.source_input)
        self.contract_address = self._buscar_endereco()
        self.enabled = False

        if not self.contract_address:
            self._log("Contrato não encontrado (contract_address.txt). Integração desativada.")
            return
        if not self.source_path:
            self._log("Fonte não é um arquivo local. Integração blockchain inativa.")
            return
        if not HRE_HASH_JS.exists() or not HRE_FOUND_JS.exists():
            self._log("Scripts hre_hash.js/hre_found.js não encontrados.")
            return

        self.enabled = True
        self._log(f"pronto. contrato={self.contract_address} arquivo='{self.source_path.name}'")

    def _log(self, msg: str) -> None:
        print(f"[blockchain] {msg}")

    def _resolve_source(self, src: str) -> Optional[Path]:
        if not src or src.isdigit():
            return None
        cand = Path(src)
        if not cand.is_absolute():
            cand = self.base_dir / cand
        if cand.exists() and cand.is_file():
            return cand
        self._log(f"arquivo de origem não localizado: {cand}")
        return None

    def _buscar_endereco(self) -> str:
        arquivo = self.base_dir / "contract_address.txt"
        try:
            conteudo = arquivo.read_text(encoding="utf-8").strip()
            if PADRAO_ENDERECO.match(conteudo):
                return conteudo
        except Exception:
            pass
        return ""

    def _calcular_hash(self, path: Path) -> Optional[str]:
        sha = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    sha.update(chunk)
            return "0x" + sha.hexdigest()
        except OSError as exc:
            self._log(f"erro ao ler '{path}': {exc}")
            return None

    def _run_node(self, script: Path, *args: str) -> Optional[str]:
        try:
            res = subprocess.run(
                ["node", str(script), *args], capture_output=True, text=True, check=True
            )
            return res.stdout.strip()
        except FileNotFoundError:
            self._log("Node.js não encontrado no PATH. Verifique a instalação.")
        except subprocess.CalledProcessError as exc:
            self._log(f"falha ao executar '{script.name}': {exc}; stderr={exc.stderr}")
        return None

    def _check_registered(self, file_hash: str) -> bool:
        if not self.enabled:
            return False
        out = self._run_node(HRE_FOUND_JS, file_hash, self.contract_address)
        if out is None:
            self._log("verificação on-chain sem resposta válida.")
            return False
        self._log(f"verificação on-chain retornou: '{out}'")
        low = out.lower()
        if low in {"true", "false"}:
            return low == "true"
        if re.fullmatch(r"0x[0-9a-fA-F]{64}", out):
            return out.lower() == file_hash.lower()
        return False

    def _enviar_para_blockchain(self, file_hash: str) -> bool:
        if not self.enabled:
            return False
        out = self._run_node(HRE_HASH_JS, file_hash, self.contract_address)
        if out is None:
            return False
        self._log(f"hash enviado. resposta: {out[:120]}")
        return True

    def register_file(self, path: Path, descricao: str) -> bool:
        if not self.enabled:
            return False
        file_hash = self._calcular_hash(path)
        if not file_hash:
            self._log(f"falha ao calcular hash para '{path.name}'")
            return False
        self._log(f"registrando arquivo '{path.name}' para evento: {descricao}")
        if self._check_registered(file_hash):
            self._log("hash do trecho já estava presente no contrato. Nenhum envio necessário.")
            return False
        return self._enviar_para_blockchain(file_hash)

    def reportar_nao_conformidade(self, registro: dict) -> None:
        return

class AFNRefinado:
    def __init__(self, audit_mode: bool = False, blockchain_client: Optional[BlockchainClient] = None):
        self.estado_atual = "q0"
        self.estado_anterior = None
        self.estados_finais = {"q5"}
        self.audit_mode = audit_mode
        self.erros_acumulados = []
        self.blockchain_client = blockchain_client

    def verificar_erro_critico(self, objetos, boxes=None):
        objetos_norm = _normalizar_objetos(objetos)
        tem_sem_luva = "mao sem luva" in objetos_norm
        tem_ativimetro = "ativimetro" in objetos_norm
        crit = tem_sem_luva or not tem_ativimetro
        if crit:
            if tem_sem_luva and not tem_ativimetro:
                mensagem = "Erro critico: mao sem luva e ativimetro ausente"
            elif tem_sem_luva:
                mensagem = "Erro critico: mao sem luva"
            else:
                mensagem = "Erro critico: ativimetro ausente"
            self._registrar_nao_conformidade("critical", objetos_norm, boxes, mensagem)
        return crit

    def mapear_entrada(self, objetos, boxes, nomes):
        objetos_norm = _normalizar_objetos(objetos)
        if self.estado_atual == "q0":
            return "tem_mao" if "mao com luva" in objetos_norm else ""

        if self.estado_atual == "q1":
            return "com_luva" if "mao com luva" in objetos_norm else ""

        if self.estado_atual == "q2":
            has_cesta = any("cesta" in obj for obj in objetos_norm)
            has_amostra = any("amostra" in obj for obj in objetos_norm)
            if has_cesta and has_amostra:
                return "cesta_dentro"
            return ""

        if self.estado_atual == "q3":
            # Novo comportamento: usar IoU entre caixas "amostra" e "cesta".
            # Complementos:
            #  - aceita rótulos compostos (e.g., "cesta_com_amostra", "amostra_dentro_cesta").
            #  - fallback por centro-dentro e por fração de cobertura da amostra.
            box_amostras = []
            box_cestas = []
            for i, nome in enumerate(objetos_norm):
                if i >= len(boxes):
                    continue
                b = boxes[i]
                if nome == "amostra":
                    box_amostras.append(b)
                elif nome == "cesta":
                    box_cestas.append(b)

            # Aceita rótulos compostos detectados pelo modelo (qualquer label contendo ambas as palavras)
            for lbl in objetos_norm:
                l = str(lbl).strip().lower()
                if ("cesta" in l) and ("amostra" in l):
                    return "amostra_dentro_cesta"

            if box_amostras and box_cestas:
                for ba in box_amostras:
                    for bc in box_cestas:
                        # 1) IoU
                        if iou_xyxy(ba, bc) >= IOU_THR_Q3:
                            return "amostra_dentro_cesta"
                        # 2) Centro da amostra dentro da cesta
                        if esta_dentro(centro(ba), bc):
                            return "amostra_dentro_cesta"
                        # 3) Fração de cobertura da amostra (intersecção / área amostra)
                        a_am = area(ba)
                        if a_am > 0:
                            frac = inter_area(ba, bc) / a_am
                            if frac >= FRAC_THR_Q3:
                                return "amostra_dentro_cesta"
            return ""

        if self.estado_atual == "q4":
            box_amostra = None
            box_ativimetro = None

            for i, nome in enumerate(objetos_norm):
                box = boxes[i]
                if nome == "amostra":
                    box_amostra = box
                elif nome == "ativimetro":
                    box_ativimetro = box

            if box_amostra and box_ativimetro:
                c = centro(box_amostra)
                return "tracking" if esta_dentro(c, box_ativimetro) else "erro_medio"
            return "erro_medio"

        return ""

    def transita(self, simbolo):
        transicoes = {
            ("q0", "tem_mao"): "q1",
            ("q1", "com_luva"): "q2",
            ("q2", "cesta_dentro"): "q3",
            ("q3", "amostra_dentro_cesta"): "q4",
            ("q4", "tracking"): "q5",
        }
        return transicoes.get((self.estado_atual, simbolo))

    def _registrar_nao_conformidade(self, tipo: str, objetos, boxes, mensagem: str) -> None:
        try:
            registro = {
                "tipo": tipo,
                "mensagem": mensagem,
                "state": self.estado_atual,
                "prev_state": self.estado_anterior,
                "ts": time.time(),
                "objetos": _normalizar_objetos(objetos),
                "boxes": boxes,
            }
            self.erros_acumulados.append(registro)
            if self.blockchain_client:
                self.blockchain_client.reportar_nao_conformidade(registro)
        except Exception:
            pass

    def processar(self, objetos, boxes, nomes):
        print(f"\n Simulando objetos: {objetos}")
        print(f" Estado atual: {self.estado_atual}")
        if self.verificar_erro_critico(objetos, boxes):
            print("Erro critico -> reset para q0")
            self.estado_atual = "q0"
            self.estado_anterior = None
            return

        simbolo = self.mapear_entrada(objetos, boxes, nomes)

        if simbolo == "erro_medio":
            self._registrar_nao_conformidade(
                "medium", objetos, boxes, "Erro medio: amostra fora do ativimetro ou condicao invalida"
            )
            print("Erro medio -> retornando ao estado anterior")
            self.estado_atual = self.estado_anterior
        elif simbolo:
            prox = self.transita(simbolo)
            if prox:
                print(f"{self.estado_atual} -> {prox} via '{simbolo}'")
                self.estado_anterior = self.estado_atual
                self.estado_atual = prox
                if self.estado_atual in self.estados_finais:
                    print("Final alcancado. Encerrando execucao.")
                    return "fim"
        else:
            print("Sem mudanca de estado.")

class AFNAuditoria(AFNRefinado):
    def __init__(self, blockchain_client: Optional[BlockchainClient] = None):
        super().__init__(audit_mode=True, blockchain_client=blockchain_client)

    def processar(self, objetos, boxes, nomes):
        print(f"\n Simulando objetos: {objetos}")
        print(f" Estado atual: {self.estado_atual}")
        if self.verificar_erro_critico(objetos, boxes):
            print("Erro critico (auditoria) - permanecendo no estado atual")
            return

        simbolo = self.mapear_entrada(objetos, boxes, nomes)

        if simbolo == "erro_medio":
            self._registrar_nao_conformidade(
                "medium", objetos, boxes, "Erro medio: amostra fora do ativimetro ou condicao invalida"
            )
            print("Erro medio (auditoria) - permanecendo no estado atual")
        elif simbolo:
            prox = self.transita(simbolo)
            if prox:
                print(f"{self.estado_atual} -> {prox} via '{simbolo}'")
                self.estado_anterior = self.estado_atual
                self.estado_atual = prox
                if self.estado_atual in self.estados_finais:
                    print("Final alcancado. Encerrando execucao.")
                    return "fim"
        else:
            print("Sem mudanca de estado.")
        # já acumulado em _registrar_nao_conformidade

class AFNAuditoria2(AFNRefinado):
    def __init__(self, blockchain_client: Optional[BlockchainClient] = None):
        super().__init__(audit_mode=True, blockchain_client=blockchain_client)

    def processar(self, objetos, boxes, nomes):
        print(f"\n Simulando objetos: {objetos}")
        print(f" Estado atual: {self.estado_atual}")
        if self.verificar_erro_critico(objetos, boxes):
            print("Erro critico (auditoria) - permanecendo no estado atual")
            return

        simbolo = self.mapear_entrada(objetos, boxes, nomes)

        if simbolo == "erro_medio":
            self._registrar_nao_conformidade(
                "medium", objetos, boxes, "Erro medio: amostra fora do ativimetro ou condicao invalida"
            )
            print("Erro medio (auditoria) - permanecendo no estado atual")
        elif simbolo:
            prox = self.transita(simbolo)
            if prox:
                print(f"{self.estado_atual} -> {prox} via '{simbolo}'")
                self.estado_anterior = self.estado_atual
                self.estado_atual = prox
                if self.estado_atual in self.estados_finais:
                    print("Final alcancado.")
                    self.estado_atual = "q0"
                    self.estado_anterior = None
        else:
            print("Sem mudanca de estado.")
def _choose_model_path(base_dir: str) -> str:
    # Preferência solicitada: ModeloTreinadoV3.pt
    cand0 = os.path.join(base_dir, "sistema\compilado\ModeloTreinadoV3.pt")
    cand1 = os.path.join(base_dir, "modelo_treinadov2.1.pt")
    cand2 = os.path.join(base_dir, "yolov8n.pt")
    for c in (cand0, cand1, cand2):
        if os.path.exists(c):
            return c
    return cand0


def _gen_from_image(model, path):
    import cv2
    import numpy as np

    img = cv2.imread(path)
    if img is None:
        print(f"Não foi possível ler a imagem: {path}")
        return []
    res = model(img, verbose=False)
    if not res:
        return []
    r0 = res[0]
    names = r0.names
    boxes = r0.boxes.xyxy.cpu().tolist() if r0.boxes is not None else []
    classes = r0.boxes.cls.tolist() if r0.boxes is not None else []
    objetos = _normalizar_objetos([names[int(i)] for i in classes])
    return [(objetos, boxes)]


def _format_seconds_label(value: Optional[float]) -> str:
    if value is None or value < 0:
        return "--:--"
    total = int(value)
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _stream_from_capture(model, cap, afn, clipper: Optional[NonConformityClipper] = None):
    import cv2
    import numpy as np
    WIN = "YOLO + AFN"
    MAX_W, MAX_H = 960, 540  # janela padrão para enquadrar o vídeo
    def _letterbox_display(img):
        h, w = img.shape[:2]
        # escala para caber inteiramente na janela alvo
        scale = min(MAX_W / max(w, 1), MAX_H / max(h, 1))
        new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
        canvas = np.zeros((MAX_H, MAX_W, 3), dtype=resized.dtype)
        off_x = (MAX_W - new_w) // 2
        off_y = (MAX_H - new_h) // 2
        canvas[off_y:off_y + new_h, off_x:off_x + new_w] = resized
        return canvas
    if SHOW_GUI:
        try:
            cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(WIN, MAX_W, MAX_H)
        except Exception:
            pass
    # Uma vez que alguns ambientes têm incompatibilidade numpy/scipy
    # ao usar tracking (model.track), caímos para detecção simples
    # quando o tracking falhar.
    frame_output_path = FRAME_OUTPUT_PATH
    frame_notice_emitted = False
    frame_error_reported = False
    last_frame_publish_ts = 0.0

    def _publish_frame(image):
        nonlocal frame_notice_emitted, frame_error_reported, last_frame_publish_ts
        if not frame_output_path:
            return
        now = time.time()
        min_interval = 1.0 / DISPLAY_FPS if DISPLAY_FPS > 0 else 0.0
        if min_interval > 0 and (now - last_frame_publish_ts) < min_interval:
            return
        tmp_path = frame_output_path.parent / f".{frame_output_path.name}.tmp"
        try:
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            Image.fromarray(rgb).save(tmp_path, format="JPEG")
            os.replace(tmp_path, frame_output_path)
            last_frame_publish_ts = now
            if not frame_notice_emitted:
                frame_notice_emitted = True
                print(f"[frames] enviando visualiza��o para {frame_output_path}")
        except Exception as exc:
            if not frame_error_reported:
                frame_error_reported = True
                print(f"[frames] falha ao salvar frames: {exc}")
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass

    def _draw_overlays(img, boxes, labels, state, elapsed_sec: Optional[float] = None):
        colors = {
            "amostra": (0, 255, 255),
            "cesta": (255, 165, 0),
            "ativimetro": (0, 128, 255),
            "mao com luva": (0, 255, 0),
            "mao sem luva": (0, 0, 255),
        }
        if not (SHOW_GUI or frame_output_path):
            return img
        for lbl, box in zip(labels, boxes):
            try:
                x1, y1, x2, y2 = [int(v) for v in box]
            except Exception:
                continue
            color = colors.get(str(lbl).lower(), (255, 255, 255))
            cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
            cv2.putText(
                img,
                str(lbl),
                (x1, max(20, y1 - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                color,
                2,
                cv2.LINE_AA,
            )
        try:
            cv2.putText(
                img,
                f"Estado: {state}",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                1,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )
            if elapsed_sec is not None:
                tempo_texto = f"Tempo: {_format_seconds_label(elapsed_sec)} / {_format_seconds_label(total_duration_sec)}"
                cv2.putText(
                    img,
                    tempo_texto,
                    (10, 62),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.85,
                    (255, 255, 255),
                    2,
                    cv2.LINE_AA,
                )
        except Exception:
            pass
        return img

    warned_fallback = False
    fps = cap.get(cv2.CAP_PROP_FPS)
    fps = fps if fps and fps > 0 else 30.0
    analysis_stride = max(1, int(round(fps / ANALYSIS_FPS))) if ANALYSIS_FPS > 0 else 1
    total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    total_duration_sec: Optional[float] = None
    if total_frames and total_frames > 0 and fps > 0:
        total_duration_sec = total_frames / fps
    playback_start_wall = time.time()
    playback_start_video_ts: Optional[float] = None
    frame_index = 0
    current_measurement_index = 1
    next_measurement_boundary = MEASUREMENT_SECONDS if MEASUREMENT_SECONDS > 0 else None
    last_boxes = []
    last_objetos = []
    last_processed_frame = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            print("[info] Fim do vídeo. Execução encerrada; escolha executar novamente ou selecione outro vídeo.")
            break
        frame_index += 1
        timestamp_msec = cap.get(cv2.CAP_PROP_POS_MSEC)
        timestamp_sec = (timestamp_msec / 1000.0) if timestamp_msec and timestamp_msec > 0 else (frame_index / fps)
        if playback_start_video_ts is None:
            playback_start_video_ts = timestamp_sec
        target_elapsed = max(0.0, timestamp_sec - playback_start_video_ts)
        wall_elapsed = time.time() - playback_start_wall
        delay = target_elapsed - wall_elapsed
        if delay > 0:
            time.sleep(delay)
        if next_measurement_boundary is not None:
            while timestamp_sec >= next_measurement_boundary:
                print(
                    f"[medicao] medicao {current_measurement_index} encerrada aos {next_measurement_boundary:.2f}s; iniciando medicao {current_measurement_index + 1}."
                )
                current_measurement_index += 1
                next_measurement_boundary += MEASUREMENT_SECONDS
        should_run_inference = (frame_index == 1) or ((frame_index - last_processed_frame) >= analysis_stride)
        if should_run_inference:
            try:
                res = model.track(frame, persist=True, verbose=False)
            except Exception as e:
                if not warned_fallback:
                    try:
                        print("[aviso] Tracking indisponível (scipy/numpy). Usando detecção por frame. Detalhe:", str(e))
                    except Exception:
                        pass
                    warned_fallback = True
                res = model(frame, verbose=False)
            if res:
                r0 = res[0]
                names = r0.names
                last_boxes = r0.boxes.xyxy.cpu().tolist() if r0.boxes is not None else []
                classes = r0.boxes.cls.tolist() if r0.boxes is not None else []
                last_objetos = _normalizar_objetos([names[int(i)] for i in classes])
                last_processed_frame = frame_index
        boxes = last_boxes
        objetos = last_objetos
        critical_reason = detectar_nao_conformidade_critica(objetos)
        if clipper:
            clipper.ingest(timestamp_sec, frame, critical_reason)
        r = afn.processar(objetos, boxes, objetos)
        if r == "fim":
            print("[info] Autômato finalizado. Saindo do loop de captura.")
            break

        if SHOW_GUI or frame_output_path:
            try:
                annotated = _draw_overlays(frame.copy(), boxes, objetos, afn.estado_atual, timestamp_sec)
                disp = _letterbox_display(annotated)
                if SHOW_GUI:
                    cv2.imshow(WIN, disp)
                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        break
                if frame_output_path:
                    _publish_frame(disp)
            except Exception:
                pass

    try:
        if clipper:
            clipper.finish()
        cap.release()
        if SHOW_GUI:
            import cv2 as _cv
            _cv.destroyAllWindows()
    except Exception:
        pass


def main():
    import argparse
    parser = argparse.ArgumentParser(description="AFN + YOLO (imagem/vídeo)")
    parser.add_argument("--source", type=str, default=os.getenv("SOURCE", "0"),
                        help="Caminho de vídeo/imagem ou índice da webcam (ex: 0)")
    parser.add_argument("--model", type=str, default=os.getenv("MODEL", ""),
                        help="Caminho do modelo .pt (padrão: ModeloTreinadoV3.pt)")
    parser.add_argument("--simulate", action="store_true", help="Usar palavras simuladas (modo legado)")
    args = parser.parse_args()

    _mode = os.getenv("MODO", "").strip().lower()
    _audit = _mode in {"auditoria", "audit"}

    base_dir = os.path.dirname(__file__)
    blockchain_client = BlockchainClient(BASE_DIR, args.source)
    afn = AFNAuditoria2(blockchain_client=None) if _audit else AFNRefinado(blockchain_client=None)

    if args.simulate:
        print("Usando modo simulado legado.")
        # Pequena simulação mínima só para manter compatibilidade
        palavras = [
            (["mao sem luva", "ativimetro"], [[0, 0, 100, 100], [300, 300, 400, 400]]),
            (["mao com luva", "ativimetro"], [[0, 0, 100, 100], [300, 300, 400, 400]]),
            (["cesta", "amostra", "ativimetro"], [[50, 50, 100, 100], [70, 70, 110, 110], [300, 300, 400, 400]]),
            # A partir daqui, q3 vai validar se amostra está dentro da cesta via boxes
            (["amostra", "cesta", "ativimetro"], [[60, 60, 100, 100], [50, 50, 120, 120], [300, 300, 400, 400]]),
            (["amostra", "ativimetro"], [[320, 320, 350, 350], [300, 300, 400, 400]]),
        ]
        for objetos, boxes in palavras:
            afn.processar(objetos, boxes, objetos)
        return

    # YOLO em imagem/vídeo/webcam
    try:
        from ultralytics import YOLO
        import cv2
    except Exception as e:
        print("YOLO não disponível. Instale com: pip install ultralytics opencv-python")
        return

    model_path = args.model.strip() or _choose_model_path(base_dir)
    if not os.path.exists(model_path):
        print("Modelo YOLO não encontrado. Coloque 'ModeloTreinadoV3.pt' (padrão) ou outro .pt na pasta Automatos e/ou use --model.")
        return
    print(f"Carregando modelo: {model_path}")
    modelo = YOLO(model_path)

    src = args.source
    # Webcam se número inteiro
    cap = None
    if src.isdigit():
        cap = cv2.VideoCapture(int(src))
    elif os.path.isfile(src):
        # Detecta se é imagem por extensão
        ext = os.path.splitext(src)[1].lower()
        if ext in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}:
            pares = _gen_from_image(modelo, src)
            for objetos, boxes in pares:
                r = afn.processar(objetos, boxes, objetos)
                if r == "fim":
                    break
            return
        else:
            cap = cv2.VideoCapture(src)
    else:
        print(f"Fonte inválida: {src}")
        return

    if not cap or not cap.isOpened():
        print(f"Não foi possível abrir a fonte: {src}")
        return

    clipper = None
    clip_source_path = None
    if not src.isdigit():
        cand = Path(src)
        if not cand.is_absolute():
            cand = BASE_DIR / cand
        if cand.exists() and cand.is_file():
            clip_source_path = cand

    if clip_source_path is not None:
        fps = cap.get(cv2.CAP_PROP_FPS)
        clipper = NonConformityClipper(clip_source_path, fps, blockchain_client)
    else:
        print("[trechos] extração automática disponível apenas para vídeos em arquivo nesta versão.")

    _stream_from_capture(modelo, cap, afn, clipper)


if __name__ == "__main__":
    main()
