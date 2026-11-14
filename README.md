# Flame in Freefall — backend de dados + chatbot (PSI-159 / ACME CFI-G)

## Estrutura
```
data/raw/        CSVs originais da NASA (não editar)
data/processed/  saída do pipeline (gerada)
  psi159_unificado.csv   1 linha = 1 teste (333 testes: 154 inversos + 179 normais)
  psi159.db              mesmo dado em SQLite (tabelas: testes, cenarios, dicionario)
  cenarios.csv           Terra, traje espacial, base lunar (valores do texto do desafio)
  dicionario_dados.csv   significado de cada coluna em linguagem simples
  limpeza_log.csv        tudo que foi alterado ou descartado, com o ID do teste
src/pipeline.py  unifica e limpa
src/tools.py     funções SQL que o chatbot pode chamar
src/chatbot.py   Gemini Flash + function calling
```

## Rodar
```bash
pip install -r requirements.txt
python src/pipeline.py                    # gera data/processed/*
export GEMINI_API_KEY="..."               # chave do Google AI Studio
export GEMINI_MODEL="gemini-2.5-flash"    # confira o nome vigente na doc do Google
python src/chatbot.py                     # teste no terminal
```

## No Streamlit
```python
import sys, streamlit as st
sys.path.append("src")
from chatbot import FlameChatbot

if "bot" not in st.session_state:
    st.session_state.bot = FlameChatbot(nivel="adolescente")
    st.session_state.msgs = []

for m in st.session_state.msgs:
    st.chat_message(m["role"]).write(m["text"])

if q := st.chat_input("Pergunte sobre fogo no espaço..."):
    st.chat_message("user").write(q)
    with st.spinner("Pensando..."):
        r = st.session_state.bot.perguntar(q)
    st.chat_message("assistant").write(r)
    st.session_state.msgs += [{"role": "user", "text": q}, {"role": "assistant", "text": r}]
```
Para o painel, leia `data/processed/psi159_unificado.csv` com `@st.cache_data`.

## Decisões de modelagem (para a banca)
* `o2_fraction` significa coisas diferentes: em chama **inversa** é o O2 que sai do queimador;
  em chama **normal** é o O2 da câmara (`o2_location`). Compare tipos de chama com cuidado.
* `outcome` resume Hot ignition + Self-ext/Flow term + comentários.
* Valores `-`, `#DIV/0!` e `#VALUE!` da planilha viraram vazio e estão no log.
* Pontos a CONFIRMAR na documentação do PSI-159: significado de `TFP` e de `PCI-A..G`.
