"""
Pipeline de unificação e limpeza — PSI-159 (ACME CFI-G)
=======================================================
Lê as duas tabelas experimentais (chamas INVERSAS e NORMAIS) e produz:

  data/processed/psi159_unificado.csv   tabela única, uma linha = um teste
  data/processed/psi159.db              o mesmo, em SQLite (para SQL e chatbot)
  data/processed/cenarios.csv           ambientes de referência (Terra, traje, Lua)
  data/processed/dicionario_dados.csv   dicionário em linguagem simples
  data/processed/limpeza_log.csv        TUDO que foi alterado/descartado (auditoria)

Uso:  python src/pipeline.py
Para acrescentar outra investigação (ex.: PSI-10), crie uma função
`carrega_xxx()` que devolva as mesmas colunas de COLUNAS_FINAIS e
concatene em `main()`.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
RAW = RAIZ / "data" / "raw"
OUT = RAIZ / "data" / "processed"

ARQ_INVERSA = RAW / "PSI-159_Experimental_table_CFI-G_Inverse_Flames.csv"
ARQ_NORMAL = RAW / "PSI-159_Experimental_table_CFI-G_Normal_Flames.csv"

PSI_POR_BAR = 14.5038
LOG: list[dict] = []


def log(arquivo: str, acao: str, coluna: str = "", teste: str = "", detalhe: str = "") -> None:
    LOG.append(dict(arquivo=arquivo, acao=acao, coluna=coluna, test_id=teste, detalhe=detalhe))


# ----------------------------------------------------------------------------
# utilidades de limpeza
# ----------------------------------------------------------------------------
def normaliza_id(s: str) -> str:
    """'21088A1' -> '21088 A1' (a planilha original mistura os dois formatos)."""
    s = str(s).strip()
    return re.sub(r"^(\d+)\s*([A-Za-z]+\d+)$", r"\1 \2", s)


def para_numero(serie: pd.Series, arquivo: str, coluna: str, ids: pd.Series) -> pd.Series:
    """Converte para número; '-', '#DIV/0!' etc. viram vazio (NaN) e ficam registrados."""
    num = pd.to_numeric(serie, errors="coerce")
    ruins = serie.notna() & num.isna()
    for i in serie[ruins].index:
        log(arquivo, "valor não numérico -> vazio", coluna, ids[i], f"valor original: {serie[i]!r}")
    return num


def sim_nao(v) -> bool | None:
    if pd.isna(v):
        return None
    return str(v).strip().lower() in {"yes", "y", "sim", "true"}


def temp_limite(c: str) -> bool:
    c = c.lower()
    return "limit" in c and bool(re.search(r"\bt(emp|emperature)?\b", c))


REGRAS_QUALIDADE = {
    "teste_abortado": lambda c: "abort" in c.lower(),
    "dados_perdidos": lambda c: "lost" in c.lower(),
    "chama_nao_esferica": lambda c: bool(re.search(r"not (spherical|round)", c.lower())),
    "repeticao": lambda c: "repeat" in c.lower(),
    "sem_nitrogenio": lambda c: "no nitrogen" in c.lower(),
}


def notas_qualidade(coment) -> str:
    if not isinstance(coment, str):
        return ""
    return ";".join(k for k, f in REGRAS_QUALIDADE.items() if f(coment))


# ----------------------------------------------------------------------------
# tabela de chamas INVERSAS
# ----------------------------------------------------------------------------
def carrega_inversa() -> pd.DataFrame:
    arq = ARQ_INVERSA.name
    d = pd.read_csv(ARQ_INVERSA)
    d.columns = [re.sub(r"\s+", " ", c).strip() for c in d.columns]
    vazias = int(d.isna().all(axis=1).sum())
    d = d.dropna(how="all").reset_index(drop=True)
    log(arq, "linhas totalmente vazias removidas", detalhe=f"{vazias} linhas (sobra de formatação da planilha)")

    ids = d["Test"].map(normaliza_id)
    for antigo, novo in zip(d["Test"], ids):
        if antigo != novo:
            log(arq, "id padronizado", "Test", novo, f"{antigo!r} -> {novo!r}")

    hot = d["Hot ignition"].str.strip().str.lower()
    cor = d["Color"].str.strip().str.lower().replace({"yellow then blue": "yellow-blue", "-": np.nan})
    limite_cor = (cor == "limit").to_numpy()
    cor = cor.mask(cor == "limit")
    coment = d["Comments"]

    pc = para_numero(d["Peak burner Temp (C)"], arq, "Peak burner Temp (C)", ids)
    pk = para_numero(d["Peak burner temp (K)"], arq, "Peak burner temp (K)", ids)
    pk = pk.fillna(pc + 273.15)

    out = pd.DataFrame({
        "investigation": "PSI-159",
        "test_id": ids,
        "flame_type": "inverse",
        "day": d["Day"].astype(int),
        "fuel": d["Fuel"].str.strip().str.lower(),
        "fuel_mix_pct": 100.0,
        "o2_fraction": d["XO2"],
        "o2_fraction_after": d["XO2"],
        "o2_location": "burner_flow",
        "fuel_fraction": d["Xf before test"],
        "fuel_fraction_after": d["Xf after test"],
        "fuel_location": "chamber",
        "pressure_bar": d["approx p (bar)"],
        "hc_flow_mg_s": d["HC burnrate (mg/s)"],
        "o2_flow_mg_s": d["O2 flow rate (mg/s)"],
        "tfp": d["TFP"].map(sim_nao),
        "tad_k": para_numero(d["Tad (K)"], arq, "Tad (K)", ids),
        "zst": para_numero(d["Zst"], arq, "Zst", ids),
        "ignition_raw": hot,
        "burn_time_s": d["burn time (sec)"],
        "end_code": d["Self -ext or flow term"].str.strip(),
        "peak_burner_temp_c": pc,
        "peak_burner_temp_k": pk,
        "flame_color": cor,
        "cool_flame": False,
        "cool_flame_code": np.nan,
        "comments": coment,
    })
    out["hit_burner_temp_limit"] = [bool(limite_cor[i]) or (isinstance(c, str) and temp_limite(c))
                                    for i, c in enumerate(coment)]
    out["_arquivo"] = arq
    return out


# ----------------------------------------------------------------------------
# tabela de chamas NORMAIS
# ----------------------------------------------------------------------------
def carrega_normal() -> pd.DataFrame:
    arq = ARQ_NORMAL.name
    d = pd.read_csv(ARQ_NORMAL)
    d.columns = [re.sub(r"\s+", " ", c).strip() for c in d.columns]   # espaços duplos/sobrando
    ids = d["Test"].map(normaliza_id)
    for antigo, novo in zip(d["Test"], ids):
        if antigo != novo:
            log(arq, "id padronizado", "Test", novo, f"{antigo!r} -> {novo!r}")

    # '30% butane' -> combustível 'butane' com 30% de pureza/diluição
    parte = d["Fuel"].str.extract(r"^(?:(\d+)%\s*)?(\w+)$")
    fuel = parte[1].str.lower()
    pct = pd.to_numeric(parte[0]).fillna(100.0)

    hot = d["Hot ignition"].str.strip().str.lower()
    coment = d["Comments"]
    cf_raw = d["Cool flame?"]
    cool = cf_raw.fillna("").str.lower().str.startswith("yes")
    cool |= coment.fillna("").str.lower().str.contains("cool flame!|direct cool", regex=True)
    codigo = cf_raw.str.extract(r"(PCI-\w+)")[0]
    codigo = codigo.mask(codigo == "PCI-hot")

    # inconsistência: marcado 'No' mas com 22 s de chama e comentário "hot flame"
    fix = ((hot == "no") & coment.fillna("").str.lower().str.contains("hot flame")
           & (d["burn time (sec)"] > 0))
    for i in d.index[fix]:
        log(arq, "inconsistência corrigida", "Hot ignition", ids[i],
            "estava 'No', mas há burn time > 0 e comentário 'hot flame' -> 'yes'")
    hot = hot.mask(fix, "yes")

    pc = para_numero(d["Peak burner T (C )"], arq, "Peak burner T (C )", ids)
    out = pd.DataFrame({
        "investigation": "PSI-159",
        "test_id": ids,
        "flame_type": "normal",
        "day": d["Day"].astype(int),
        "fuel": fuel,
        "fuel_mix_pct": pct,
        "o2_fraction": d["XO2 before test"],
        "o2_fraction_after": d["XO2 after test"],
        "o2_location": "chamber",
        "fuel_fraction": d["Xf"],
        "fuel_fraction_after": d["Xf"],
        "fuel_location": "burner_flow",
        "pressure_bar": d["approx p (bar)"],
        "hc_flow_mg_s": d["mHC (mg/s)"],
        "o2_flow_mg_s": np.nan,
        "tfp": d["TFP"].map(sim_nao),
        "tad_k": para_numero(d["Tad (K)"], arq, "Tad (K)", ids),
        "zst": para_numero(d["Zst"], arq, "Zst", ids),
        "ignition_raw": hot,
        "burn_time_s": d["burn time (sec)"],
        "end_code": d["Self-ext or flow term"].str.strip(),
        "peak_burner_temp_c": pc,
        "peak_burner_temp_k": pc + 273.15,
        "flame_color": np.nan,
        "cool_flame": cool,
        "cool_flame_code": codigo,
        "comments": coment,
    })
    out["hit_burner_temp_limit"] = [isinstance(c, str) and temp_limite(c) for c in coment]
    out["_arquivo"] = arq
    return out


# ----------------------------------------------------------------------------
# regras de negócio comuns
# ----------------------------------------------------------------------------
def classifica(df: pd.DataFrame) -> pd.DataFrame:
    comentl = df["comments"].fillna("").str.lower()
    direct_cool = comentl.str.contains("direct cool")

    ign = df["ignition_raw"].map({"yes": "hot", "flash": "flash", "no": "none"})
    df["ignition_type"] = ign.mask(direct_cool, "cool")

    def desfecho(r) -> str:
        if r.ignition_type == "none":
            return "no_ignition"
        if r.ignition_type == "cool":
            return "cool_flame_only"
        if r.ignition_type == "flash":
            return "flash_only"
        return {"SE": "self_extinguished", "FT": "flow_terminated"}.get(r.end_code, "unknown")

    df["outcome"] = df.apply(desfecho, axis=1)
    df["ignited"] = df["outcome"] != "no_ignition"
    df["quality_notes"] = df["comments"].map(notas_qualidade)
    df["pressure_psi"] = (df["pressure_bar"] * PSI_POR_BAR).round(2)

    # teste sem ignição: o tempo de queima vazio no original significa 0 s
    sem = df["outcome"] == "no_ignition"
    df.loc[sem & df["burn_time_s"].isna(), "burn_time_s"] = 0.0
    return df.drop(columns=["ignition_raw", "end_code"])


COLUNAS_FINAIS = [
    "investigation", "test_id", "flame_type", "day", "fuel", "fuel_mix_pct",
    "o2_fraction", "o2_fraction_after", "o2_location",
    "fuel_fraction", "fuel_fraction_after", "fuel_location",
    "pressure_bar", "pressure_psi", "hc_flow_mg_s", "o2_flow_mg_s",
    "tad_k", "zst", "tfp",
    "ignition_type", "ignited", "outcome", "burn_time_s",
    "cool_flame", "cool_flame_code",
    "peak_burner_temp_c", "peak_burner_temp_k", "flame_color",
    "hit_burner_temp_limit", "quality_notes", "comments", "_arquivo",
]


# ----------------------------------------------------------------------------
# tabelas auxiliares
# ----------------------------------------------------------------------------
def cenarios() -> pd.DataFrame:
    """Valores tirados do texto do desafio (project_and_details.txt)."""
    psi = lambda p: round(p / PSI_POR_BAR, 3)  # noqa: E731
    linhas = [
        dict(scenario_id="earth", nome="Ar da Terra (nível do mar)", o2_min=0.18, o2_max=0.24,
             p_min_bar=0.90, p_max_bar=1.20, o2_centro=0.21, p_centro_bar=1.013,
             descricao="Ar que respiramos: ~21% de oxigênio a 14,7 psi (1 atm).",
             fonte="project_and_details.txt"),
        dict(scenario_id="spacesuit", nome="Traje espacial", o2_min=0.90, o2_max=1.0,
             p_min_bar=0.20, p_max_bar=0.40, o2_centro=1.0, p_centro_bar=psi(4.3),
             descricao="100% de oxigênio a apenas 4,3 psi, para o traje ficar flexível.",
             fonte="project_and_details.txt"),
        dict(scenario_id="moon_base", nome="Base lunar / rover pressurizado (previsto)",
             o2_min=0.30, o2_max=1.0, p_min_bar=0.20, p_max_bar=psi(10.2),
             o2_centro=0.30, p_centro_bar=psi(10.2),
             descricao="Previsão: 30% de O2 ou mais, com até 10,2 psi. Mais O2 = materiais pegam fogo mais fácil.",
             fonte="project_and_details.txt (valores previstos, não definitivos)"),
    ]
    return pd.DataFrame(linhas)


DICIONARIO = [
    ("test_id", "Código do teste", "", "Nome único do experimento (ex.: 21196 A2).", "Padronizado: 'número espaço letra+número'."),
    ("flame_type", "Tipo de chama", "", "'normal': o combustível sai do queimador e o oxigênio está ao redor. 'inverse' (invertida): o contrário, o oxigênio sai do queimador e o combustível está ao redor.", "Definido pela tabela de origem."),
    ("day", "Dia de operação", "", "Em qual dia da campanha o teste foi feito.", ""),
    ("fuel", "Combustível", "", "Gás que queimou: etano, propano ou butano.", ""),
    ("fuel_mix_pct", "Pureza do combustível", "%", "100 = gás puro. 30 = mistura com 30% de combustível (o resto é gás que não queima).", "Extraído de nomes como '30% butane' na tabela de chamas normais."),
    ("o2_fraction", "Oxigênio (início)", "fração 0-1", "Quanto do gás é oxigênio. 0,21 = o ar da Terra; 0,85 = quase só oxigênio.", "Em chama inversa é o O2 do gás que sai do queimador; em chama normal é o O2 da câmara. Não comparar os dois tipos só por este número."),
    ("o2_fraction_after", "Oxigênio (fim)", "fração 0-1", "Mesmo que acima, medido no fim do teste.", "Em chama inversa é igual ao inicial (não medido separadamente)."),
    ("o2_location", "Onde está o O2", "", "'burner_flow' = sai do queimador; 'chamber' = enche a câmara.", "Deduzido do tipo de chama (ver descrição do experimento)."),
    ("fuel_fraction", "Combustível (início)", "fração 0-1", "Quanto do gás é combustível.", "Xf nas tabelas originais."),
    ("fuel_fraction_after", "Combustível (fim)", "fração 0-1", "Mesmo que acima, no fim do teste.", ""),
    ("fuel_location", "Onde está o combustível", "", "'chamber' = enche a câmara; 'burner_flow' = sai do queimador.", "Deduzido do tipo de chama."),
    ("pressure_bar", "Pressão", "bar", "Pressão do gás na câmara. 1 bar é quase a pressão do ar ao nível do mar.", "Valor aproximado ('approx p')."),
    ("pressure_psi", "Pressão", "psi", "Mesma pressão em psi, unidade usada nas metas da NASA para habitats.", "bar x 14,5038."),
    ("hc_flow_mg_s", "Vazão de combustível", "mg/s", "Quantos miligramas de combustível entram por segundo.", "'HC burnrate' (inversa) ou 'mHC' (normal)."),
    ("o2_flow_mg_s", "Vazão de oxigênio", "mg/s", "Quantos miligramas de oxigênio saem do queimador por segundo.", "Só existe nas chamas inversas."),
    ("tad_k", "Temperatura adiabática da chama", "K", "Temperatura máxima teórica da chama (calculada, não medida). 300 K ≈ 27 °C; 2000 K ≈ 1.700 °C.", "Calculada com o ACME Chamber Calculator."),
    ("zst", "Zst (mistura ideal)", "0-1", "Número que indica em que ponto a mistura combustível+oxigênio é 'perfeita' para queimar. Menor = chama mais perto do queimador.", "Fração de mistura estequiométrica."),
    ("tfp", "TFP", "sim/não", "Sigla marcada sim/não nas tabelas originais. SIGNIFICADO A CONFIRMAR na documentação do PSI-159: não está definido nos arquivos recebidos.", "Mantida como veio."),
    ("ignition_type", "Tipo de ignição", "", "'hot' = chama quente normal; 'cool' = só chama fria; 'flash' = clarão rápido que some; 'none' = não acendeu.", "Limpeza de 'Hot ignition' + comentários."),
    ("ignited", "Acendeu?", "sim/não", "Verdadeiro se houve qualquer chama (inclusive clarão ou chama fria).", "Derivada."),
    ("outcome", "Como terminou", "", "no_ignition = não acendeu; self_extinguished = a chama apagou sozinha; flow_terminated = o experimento cortou o gás; flash_only = só um clarão; cool_flame_only = só chama fria.", "Resume 'Hot ignition' e 'Self-ext or flow term'."),
    ("burn_time_s", "Tempo de queima", "s", "Quantos segundos a chama durou.", "0 quando não acendeu."),
    ("cool_flame", "Teve chama fria?", "sim/não", "Chama fria é uma chama bem mais fraca e de baixa temperatura (quase invisível), típica da microgravidade.", "Tabela de chamas normais."),
    ("cool_flame_code", "Código da chama fria", "", "Código (PCI-A a PCI-G) anotado pelos pesquisadores quando houve chama fria.", "Sentido da sigla PCI a confirmar."),
    ("peak_burner_temp_c", "Pico de temperatura do queimador", "°C", "Temperatura mais alta que o queimador atingiu.", "Vazio quando não acendeu."),
    ("peak_burner_temp_k", "Pico de temperatura do queimador", "K", "O mesmo em Kelvin.", "°C + 273,15 quando faltava."),
    ("flame_color", "Cor da chama", "", "Cor observada (azul, amarela, amarela-azul).", "Só chamas inversas. 'limit' do original virou a coluna hit_burner_temp_limit."),
    ("hit_burner_temp_limit", "Atingiu limite de temperatura", "sim/não", "O teste foi interrompido porque o queimador ficou quente demais.", "Extraído da cor 'limit' e dos comentários."),
    ("quality_notes", "Alertas de qualidade", "", "Avisos tirados dos comentários: teste_abortado, dados_perdidos, chama_nao_esferica, repeticao, sem_nitrogenio.", ""),
    ("comments", "Comentários originais", "", "Texto original dos pesquisadores, em inglês.", ""),
    ("investigation", "Investigação", "", "De qual investigação do PSI o dado vem.", "Permite somar outras (ex.: PSI-10) na mesma tabela."),
    ("_arquivo", "Arquivo de origem", "", "CSV de onde a linha veio.", "Rastreabilidade."),
]


def dicionario() -> pd.DataFrame:
    return pd.DataFrame(DICIONARIO, columns=["coluna", "rotulo", "unidade",
                                             "explicacao_simples", "observacao_tecnica"])


# ----------------------------------------------------------------------------
def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    df = pd.concat([carrega_inversa(), carrega_normal()], ignore_index=True)
    df = classifica(df)[COLUNAS_FINAIS]

    # validações: se alguma falhar, o pipeline para (melhor que dado errado em silêncio)
    assert df["test_id"].is_unique, "IDs de teste duplicados"
    assert df["pressure_bar"].between(0.3, 4).all(), "pressão fora do esperado"
    assert df["o2_fraction"].between(0.1, 1).all(), "O2 fora do esperado"
    assert (df["outcome"] != "unknown").all(), "desfecho não classificado"

    df.to_csv(OUT / "psi159_unificado.csv", index=False)
    cen, dic = cenarios(), dicionario()
    cen.to_csv(OUT / "cenarios.csv", index=False)
    dic.to_csv(OUT / "dicionario_dados.csv", index=False)
    pd.DataFrame(LOG).to_csv(OUT / "limpeza_log.csv", index=False)

    db = OUT / "psi159.db"
    db.unlink(missing_ok=True)
    with sqlite3.connect(db) as con:
        df.to_sql("testes", con, index=False)
        cen.to_sql("cenarios", con, index=False)
        dic.to_sql("dicionario", con, index=False)
        con.execute("CREATE INDEX idx_testes_cond ON testes(fuel, flame_type, o2_fraction, pressure_bar)")

    print(f"OK: {len(df)} testes ({(df.flame_type == 'inverse').sum()} inversos, "
          f"{(df.flame_type == 'normal').sum()} normais); {len(LOG)} ações de limpeza registradas.")
    print(df["outcome"].value_counts().to_string())


if __name__ == "__main__":
    main()
