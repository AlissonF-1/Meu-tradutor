import os
import base64
import json
import sqlite3
from flask import Flask, request, jsonify
from flask_cors import CORS
from google import genai
from google.genai import types
import re

app = Flask(__name__)
CORS(app)

# --- 1. CONFIGURAÇÃO DA IA ---
# Chave e Modelo
API_KEY = os.environ.get("GOOGLE_API_KEY") 
MODELO = "gemini-2.5-flash" 
CLIENT_AI = genai.Client(api_key=API_KEY)

DB_NAME = 'meu_ingles.db'

def limpar_json(texto):
    """Remove blocos de markdown e espaços extras da resposta da IA."""
    # Remove as marcações ```json e ``` que a IA às vezes coloca
    texto_limpo = re.sub(r'```json|```', '', texto).strip()
    return texto_limpo

# --- 2. GERENCIAMENTO DO BANCO DE DADOS ---

def iniciar_banco():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS flashcards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            texto_ingles TEXT,
            texto_pt TEXT,
            data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vocabulario (
            palavra TEXT PRIMARY KEY,
            traducao TEXT,
            contexto TEXT,
            data_descoberta TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def obter_palavras_conhecidas():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT palavra FROM vocabulario")
    palavras = [row[0] for row in cursor.fetchall()]
    conn.close()
    return palavras

iniciar_banco()

# --- 3. ROTAS DA API ---

# ROTA A: Tradução por Imagem
@app.route('/traduzir-imagem', methods=['POST'])
def traduzir_imagem():
    try:
        data = request.json
        imagem_b64 = data.get('imagem_base64')
        if not imagem_b64:
            return jsonify({"erro": "Imagem não fornecida"}), 400

        palavras_ignoradas = obter_palavras_conhecidas()
        lista_ignoradas_str = ", ".join(palavras_ignoradas)
        print(f"🧠 Memória Imagem: ignorando {len(palavras_ignoradas)} palavras.")

        prompt = f"""
        Analise a imagem. 
        TAREFA 1: Tradução completa.
        TAREFA 2: Escolha 3 palavras interessantes.
        REGRAS: Ignore estas palavras: [{lista_ignoradas_str}].
        SAÍDA: JSON com flashcard_principal e palavras_destaque.
        """

        response = CLIENT_AI.models.generate_content(
            model=MODELO,
            contents=[
                types.Content(
                    parts=[
                        types.Part.from_text(text=prompt),
                        types.Part.from_bytes(data=base64.b64decode(imagem_b64), mime_type="image/jpeg")
                    ]
                )
            ],
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        return processar_e_salvar(response.text)

    except Exception as e:
        print(f"❌ Erro Imagem: {e}")
        return jsonify({"erro": str(e)}), 500

# ROTA B: Tradução por Texto
@app.route('/traduzir-texto', methods=['POST'])
def traduzir_texto():
    try:
        data = request.json
        texto_usuario = data.get('texto')
        if not texto_usuario:
            return jsonify({"erro": "Texto não fornecido"}), 400

        palavras_ignoradas = obter_palavras_conhecidas()
        lista_ignoradas_str = ", ".join(palavras_ignoradas)
        print(f"🧠 Memória Texto: ignorando {len(palavras_ignoradas)} palavras.")

        prompt = f"""
        O usuário quer aprender: "{texto_usuario}"
        TAREFA: Traduza e sugira 2 sinônimos ou termos relacionados.
        REGRAS: Ignore estas palavras: [{lista_ignoradas_str}].
        SAÍDA OBRIGATÓRIA (JSON puro):
        {{
            "flashcard_principal": {{ "ingles": "{texto_usuario}", "portugues": "tradução" }},
            "palavras_destaque": [
                {{ "palavra": "...", "traducao": "...", "contexto": "..." }}
            ]
        }}
        """

        response = CLIENT_AI.models.generate_content(
            model=MODELO, 
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        return processar_e_salvar(response.text)

    except Exception as e:
        print(f"❌ Erro Texto: {e}")
        return jsonify({"erro": str(e)}), 500

# Função auxiliar para evitar repetição de código
def processar_e_salvar(json_ia):
    try:
        # 1. Limpa e converte a string para dicionário
        texto_puro = limpar_json(json_ia)
        resultado = json.loads(texto_puro)
        
        # 2. Se por algum motivo 'resultado' ainda for string, tenta carregar de novo (double-decode)
        if isinstance(resultado, str):
            resultado = json.loads(resultado)

        # 3. Validação de segurança: verifica se as chaves existem
        if 'flashcard_principal' not in resultado:
            raise KeyError("flashcard_principal ausente")
        
        f = resultado['flashcard_principal']
        # Verifica se 'ingles' e 'portugues' existem, senão usa valores padrão
        ingles = f.get('ingles', 'N/A')
        portugues = f.get('portugues', 'N/A')

        # 4. Salva no Banco de Dados
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO flashcards (texto_ingles, texto_pt) VALUES (?, ?)", (ingles, portugues))
        
        for item in resultado.get('palavras_destaque', []):
            p = item.get('palavra', '').lower()
            t = item.get('traducao', '')
            c = item.get('contexto', '')
            if p:
                cursor.execute("INSERT OR IGNORE INTO vocabulario (palavra, traducao, contexto) VALUES (?, ?, ?)", (p, t, c))
        
        conn.commit()
        conn.close()
        return jsonify(resultado)

    except Exception as e:
        print(f"❌ Erro no Processamento: {e}")
       
        return jsonify({
            "flashcard_principal": {"ingles": "Error processing", "portugues": "Erro ao processar imagem"},
            "palavras_destaque": []
        }), 200 

@app.route('/meus-dados', methods=['GET'])
def listar_tudo():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT palavra, traducao, contexto FROM vocabulario ORDER BY data_descoberta DESC")
    vocab = cursor.fetchall()
    conn.close()
    return jsonify({"ultimas_palavras": vocab})

@app.route('/deletar-palavra/<palavra>', methods=['DELETE'])
def deletar_palavra(palavra):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM vocabulario WHERE palavra = ?", (palavra.lower(),))
        conn.commit()
        conn.close()
        return jsonify({"status": "sucesso", "mensagem": f"'{palavra}' removida."})
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

# BLOCO PRINCIPAL CORRIGIDO
if __name__ == '__main__':
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)