import sqlite3
import hashlib
import os
import time

# --- ÁREA DE CONFIGURAÇÃO ---
inicio = time.time()
# 1. Coloque aqui o caminho COMPLETO para a pasta onde o YOLO salva os vídeos.
#    Lembre-se de usar o formato do Linux (WSL).
#    Exemplo: "/mnt/c/Users/SeuNome/Desktop/videos_nao_conformes"
PASTA_DE_VIDEOS = "../trechos_nao_conformes"

# 2. Nome do arquivo do banco de dados que será criado para guardar os hashes.
DB_FILE = "registros_offchain.db"

# 3. Extensões de vídeo que o script deve procurar.
EXTENSOES_VIDEO = ('.mp4', '.avi', '.mov', '.mkv')


# --------------------------

def inicializar_db():
    """Cria o banco de dados e a tabela se eles não existirem."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # A coluna 'nome_arquivo' será única para evitar duplicatas.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS nao_conformidades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome_arquivo TEXT NOT NULL UNIQUE,
            hash_video TEXT NOT NULL,
            status TEXT NOT NULL,
            timestamp_processamento DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def calcular_hash_video(caminho_do_video):
    """Calcula o hash SHA-256 de um arquivo de vídeo."""
    sha256_hash = hashlib.sha256()
    try:
        with open(caminho_do_video, "rb") as f:
            # Lê o arquivo em pedaços para não sobrecarregar a memória.
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        # O '0x' é adicionado para manter o formato consistente com o que a blockchain espera.
        return "0x" + sha256_hash.hexdigest()
    except IOError as e:
        print(f"‼️  Erro ao ler o arquivo {os.path.basename(caminho_do_video)}: {e}")
        return None

def arquivo_ja_processado(nome_arquivo):
    """Verifica no DB se um arquivo com este nome já foi salvo. Retorna True ou False."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM nao_conformidades WHERE nome_arquivo = ?", (nome_arquivo,))
    data = cursor.fetchone()
    conn.close()
    return data is not None

def salvar_hash_no_db(nome_arquivo, hash_video):
    """Salva o nome do arquivo e seu hash no banco de dados SQLite."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        # O status 'PENDENTE' significa que o hash foi salvo localmente (off-chain)
        # mas ainda está pendente para ser enviado para a Blockchain.
        cursor.execute(
            "INSERT INTO nao_conformidades (nome_arquivo, hash_video, status) VALUES (?, ?, ?)",
            (nome_arquivo, hash_video, 'PENDENTE')
        )
        conn.commit()
        print(f"✅ Hash para '{nome_arquivo}' salvo com sucesso no banco de dados.")
    except sqlite3.IntegrityError:
        # Esta é uma segurança extra, mas a função arquivo_ja_processado já deve prevenir isso.
        print(f"⚠️  Atenção: O arquivo '{nome_arquivo}' já existia no banco de dados.")
    finally:
        conn.close()

# --- PONTO DE PARTIDA DO SCRIPT ---
if __name__ == "__main__":
    print("--- 🏛️  Iniciando Guardião de Hashes (Processador Off-Chain) ---")
    time.sleep(1)

    # Garante que o banco de dados e a tabela existem.
    inicializar_db()

    # Pega a lista de todos os arquivos na pasta de vídeos.
    try:
        arquivos_na_pasta = os.listdir(PASTA_DE_VIDEOS)
    except FileNotFoundError:
        print(f"‼️  ERRO CRÍTICO: A pasta '{PASTA_DE_VIDEOS}' não foi encontrada.")
        print("    Por favor, configure o caminho correto na variável 'PASTA_DE_VIDEOS' no início do script.")
        exit() # Encerra o script se a pasta não existe.

    videos_encontrados = [f for f in arquivos_na_pasta if f.endswith(EXTENSOES_VIDEO)]
    
    if not videos_encontrados:
        print("ℹ️  Nenhum arquivo de vídeo encontrado na pasta para processar.")
    else:
        print(f"🔎 Encontrados {len(videos_encontrados)} vídeos na pasta. Verificando...")

    novos_arquivos_processados = 0
    # Itera sobre cada arquivo de vídeo encontrado.
    for nome_arquivo in videos_encontrados:
        # Verifica se o arquivo já foi processado antes para não repetir o trabalho.
        if arquivo_ja_processado(nome_arquivo):
            continue
        
        # Se for um arquivo novo, processa.
        print(f"\n📥 Novo arquivo detectado: '{nome_arquivo}'.")
        caminho_completo = os.path.join(PASTA_DE_VIDEOS, nome_arquivo)
        
        hash_calculado = calcular_hash_video(caminho_completo)
        
        if hash_calculado:
            salvar_hash_no_db(nome_arquivo, hash_calculado)
            novos_arquivos_processados += 1

    print("\n--- Processamento Concluído ---")
    if novos_arquivos_processados > 0:
        print(f"🎉 Resumo: {novos_arquivos_processados} nova(s) não conformidade(s) foram processadas e salvas.")
    else:
        print("👍 Nenhum arquivo novo para processar. O banco de dados já está atualizado.")
        
    # --- CRONÔMETRO ---
    fim = time.time()
    duracao_segundos = fim - inicio
    print(f"\n⏱️  Tempo total de execução do script: {duracao_segundos:.2f} segundos.")