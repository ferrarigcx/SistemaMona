import time
import random

def tarefa_aleatoria():
    """Simula uma tarefa que leva um tempo aleatório para ser concluída."""
    print("Iniciando a tarefa aleatória...")
    
    # Gera um tempo aleatório entre 1 e 5 segundos para a tarefa.
    tempo_de_espera = random.uniform(1, 5)
    time.sleep(tempo_de_espera)
    
    print("Tarefa concluída!")
    return tempo_de_espera

# --- INÍCIO DO CRONÔMETRO ---
inicio = time.time()

# Executa a tarefa aleatória
tempo_gasto = tarefa_aleatoria()

# --- FIM DO CRONÔMETRO ---
fim = time.time()
duracao_total = fim - inicio

# Exibe o resultado no terminal
print(f"\n--- Resumo do Cronômetro ---")
print(f"O tempo de espera aleatório foi de: {tempo_gasto:.2f} segundos.")
print(f"O tempo total de execução do script foi de: {duracao_total:.2f} segundos.")