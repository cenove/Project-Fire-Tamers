"""
Flame in Freefall — arquivo principal do Streamlit (main file = app.py)
Rodar local:  streamlit run app.py
"""
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ / "src"))          # para importar tools.py e chatbot.py

import tools  # noqa: E402

st.set_page_config(page_title="Flame in Freefall", page_icon="🔥", layout="wide")

# --- chave do Gemini: vem dos "Secrets" do Streamlit (nunca fica no código) ---------
try:
    if "GEMINI_API_KEY" in st.secrets:
        os.environ["GEMINI_API_KEY"] = st.secrets["GEMINI_API_KEY"]
except Exception:
    pass  # sem secrets.toml: usa a variável de ambiente, se existir

DESFECHOS = {
    "no_ignition": "Não acendeu",
    "self_extinguished": "Apagou sozinha",
    "flow_terminated": "Gás cortado pelo experimento",
    "flash_only": "Só um clarão",
    "cool_flame_only": "Só chama fria",
}
TIPOS = {"normal": "Chama normal", "inverse": "Chama invertida"}


@st.cache_data
def carregar() -> pd.DataFrame:
    df = pd.read_csv(RAIZ / "data" / "processed" / "psi159_unificado.csv")
    df["desfecho"] = df["outcome"].map(DESFECHOS)
    df["tipo"] = df["flame_type"].map(TIPOS)
    return df


df = carregar()

st.title("🔥 Flame in Freefall")
st.caption("Como o fogo se comporta sem gravidade? Dados reais da NASA (PSI-159 · ACME CFI-G) "
           "feitos na Estação Espacial Internacional.")

aba1, aba2, aba3, aba4 = st.tabs(["📊 Visão geral", "🔎 Explorar testes", "🌙 Ambientes reais", "💬 Pergunte à Fagulha"])

# ------------------------------------------------------------------ visão geral
with aba1:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Testes", len(df))
    c2.metric("Acenderam", f"{df['ignited'].mean() * 100:.0f}%")
    c3.metric("Maior nível de O₂", f"{df['o2_fraction'].max() * 100:.0f}%")
    c4.metric("Maior tempo de chama", f"{df['burn_time_s'].max():.0f} s")
    st.info("⚠️ Os testes buscavam o limite em que a chama se apaga. "
            "A porcentagem de testes que acenderam **não** é a chance de um incêndio de verdade.")

    st.subheader("Como cada teste terminou")
    cont = df.groupby(["desfecho", "tipo"]).size().unstack(fill_value=0)
    st.bar_chart(cont)

# ------------------------------------------------------------------ explorar
with aba2:
    f1, f2, f3 = st.columns(3)
    comb = f1.multiselect("Combustível", sorted(df["fuel"].unique()), default=sorted(df["fuel"].unique()))
    tipo = f2.multiselect("Tipo de chama", list(TIPOS.values()), default=list(TIPOS.values()))
    o2 = f3.slider("Oxigênio (fração; 0,21 = ar da Terra)", 0.10, 0.90, (0.10, 0.90), step=0.01)

    filtro = df[df["fuel"].isin(comb) & df["tipo"].isin(tipo) & df["o2_fraction"].between(*o2)]
    st.write(f"**{len(filtro)}** testes nesse filtro.")
    if len(filtro):
        st.scatter_chart(filtro, x="o2_fraction", y="pressure_bar", color="desfecho",
                         x_label="Oxigênio (fração)", y_label="Pressão (bar)")
        cols = ["test_id", "tipo", "fuel", "o2_fraction", "pressure_bar", "desfecho", "burn_time_s", "tad_k"]
        st.dataframe(filtro[cols], use_container_width=True, hide_index=True)
    st.caption("Em chama invertida o O₂ sai do queimador; em chama normal ele enche a câmara. "
               "Compare os dois tipos com cuidado.")

# ------------------------------------------------------------------ cenários
with aba3:
    cen = pd.DataFrame(tools.listar_cenarios()["cenarios"])
    escolha = st.selectbox("Escolha um ambiente", cen["scenario_id"],
                           format_func=lambda i: cen.set_index("scenario_id").loc[i, "nome"])
    res = tools.comparar_com_cenario(escolha)
    st.write(res["cenario"]["descricao"])
    if "aviso" in res:
        st.warning(res["aviso"])
        st.dataframe(pd.DataFrame(res["testes_mais_proximos"]), use_container_width=True, hide_index=True)
    else:
        st.dataframe(pd.DataFrame(res["testes_na_faixa"]), use_container_width=True, hide_index=True)
    st.caption(res["nota"])

# ------------------------------------------------------------------ chatbot
with aba4:
    nivel = st.radio("Nível das explicações", ["adolescente", "universitario"], horizontal=True)
    if st.session_state.get("nivel") != nivel:           # trocou o nível: nova conversa
        st.session_state.nivel = nivel
        st.session_state.pop("bot", None)
        st.session_state.msgs = []

    if "msgs" not in st.session_state:
        st.session_state.msgs = []

    if "bot" not in st.session_state:
        try:
            from chatbot import FlameChatbot
            st.session_state.bot = FlameChatbot(nivel=nivel)
        except Exception as e:
            st.session_state.bot = None
            st.session_state.erro_bot = str(e)

    if st.session_state.bot is None:
        st.info(f"Chatbot indisponível: {st.session_state.get('erro_bot')}")
    else:
        for m in st.session_state.msgs:
            st.chat_message(m["role"]).write(m["text"])
        if q := st.chat_input("Ex.: Por que a chama fica redonda no espaço?"):
            st.chat_message("user").write(q)
            with st.spinner("Pensando..."):
                r = st.session_state.bot.perguntar(q)
            st.chat_message("assistant").write(r)
            st.session_state.msgs += [{"role": "user", "text": q}, {"role": "assistant", "text": r}]
