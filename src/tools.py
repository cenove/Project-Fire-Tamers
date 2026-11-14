"""
Ferramentas do chatbot — consultas seguras sobre o banco psi159.db
==================================================================
O Gemini NÃO lê a planilha: ele chama estas funções e recebe números prontos.
Assim ele não inventa dados, e toda resposta pode citar `test_id`.

Regras de segurança:
  * só SELECT, sempre com parâmetros (nada de SQL escrito pelo modelo);
  * colunas/valores aceitos passam por listas fixas;
  * todo resultado traz um campo "nota" lembrando os limites dos dados.
Os docstrings abaixo são lidos pelo Gemini para decidir quando usar cada função.
"""

import os
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Optional

DB = Path(os.environ.get("FLAME_DB", Path(__file__).resolve().parents[1] / "data" / "processed" / "psi159.db"))

FUELS = {"ethane": "ethane", "etano": "ethane", "propane": "propane", "propano": "propane",
         "butane": "butane", "butano": "butane"}
FLAMES = {"normal": "normal", "inverse": "inverse", "invertida": "inverse", "inversa": "inverse"}
OUTCOMES = {"no_ignition", "self_extinguished", "flow_terminated", "flash_only", "cool_flame_only"}

NOTA_BASE = ("Dados de laboratório: experimentos em microgravidade (Estação Espacial Internacional) com "
             "combustíveis gasosos numa câmara. Não existem dados de gravidade parcial (Lua/Marte) no PSI. "
             "Os testes foram desenhados para chegar perto do limite em que a chama se apaga, então "
             "'taxa de ignição' NÃO é a chance real de um incêndio.")

COLS_RESUMO = ("test_id, flame_type, fuel, fuel_mix_pct, o2_fraction, o2_location, pressure_bar, pressure_psi, "
               "ignition_type, outcome, burn_time_s, tad_k, cool_flame")


def _con() -> sqlite3.Connection:
    if not DB.exists():
        raise FileNotFoundError(f"Banco não encontrado em {DB}. Rode: python src/pipeline.py")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)   # somente leitura
    con.row_factory = sqlite3.Row
    return con


BOOL_COLS = {"ignited", "cool_flame", "tfp", "hit_burner_temp_limit"}


def _consulta(sql: str, params: tuple = ()) -> list[dict]:
    with _con() as con:
        linhas = [dict(r) for r in con.execute(sql, params)]
    for r in linhas:
        for k, v in r.items():
            if k in BOOL_COLS and v is not None:
                r[k] = bool(v)
            elif isinstance(v, float):
                r[k] = None if v != v else round(v, 4)
    return linhas


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


# ----------------------------------------------------------------------------
# 1. visão geral
# ----------------------------------------------------------------------------
def resumo_geral() -> dict:
    """Visão geral do conjunto de dados PSI-159 (ACME CFI-G): quantos testes, tipos de chama,
    combustíveis, faixas de oxigênio e pressão. Use para perguntas amplas como
    'o que esse projeto estudou?' ou 'quantos experimentos existem?'."""
    geral = _consulta("""SELECT COUNT(*) AS total_testes, SUM(ignited) AS testes_que_acenderam,
                         MIN(o2_fraction) AS o2_min, MAX(o2_fraction) AS o2_max,
                         MIN(pressure_bar) AS pressao_min_bar, MAX(pressure_bar) AS pressao_max_bar,
                         MAX(burn_time_s) AS maior_tempo_de_queima_s FROM testes""")[0]
    por_tipo = _consulta("SELECT flame_type, COUNT(*) AS testes FROM testes GROUP BY flame_type")
    por_comb = _consulta("SELECT fuel, COUNT(*) AS testes FROM testes GROUP BY fuel ORDER BY testes DESC")
    return {"geral": geral, "por_tipo_de_chama": por_tipo, "por_combustivel": por_comb, "nota": NOTA_BASE}


# ----------------------------------------------------------------------------
# 2. busca de testes
# ----------------------------------------------------------------------------
def buscar_testes(fuel: Optional[str] = None, flame_type: Optional[str] = None,
                  o2_min: Optional[float] = None, o2_max: Optional[float] = None,
                  pressure_min_bar: Optional[float] = None, pressure_max_bar: Optional[float] = None,
                  outcome: Optional[str] = None, limit: int = 8) -> dict:
    """Procura testes que atendem a filtros e devolve o total encontrado e alguns exemplos.
    fuel: 'ethane', 'propane' ou 'butane' (aceita etano/propano/butano).
    flame_type: 'normal' ou 'inverse' (aceita 'invertida').
    o2_min / o2_max: fração de oxigênio entre 0 e 1 (0.21 = ar da Terra; 0.85 = 85%).
    pressure_min_bar / pressure_max_bar: pressão em bar (1 bar ≈ ar ao nível do mar).
    outcome: 'no_ignition', 'self_extinguished', 'flow_terminated', 'flash_only' ou 'cool_flame_only'.
    limit: quantos exemplos devolver (máx. 20)."""
    where, p = [], []
    if fuel:
        f = FUELS.get(_sem_acento(fuel).strip())
        if not f:
            return {"erro": f"combustível '{fuel}' não existe nos dados. Use ethane, propane ou butane."}
        where.append("fuel = ?"); p.append(f)
    if flame_type:
        t = FLAMES.get(_sem_acento(flame_type).strip())
        if not t:
            return {"erro": "flame_type deve ser 'normal' ou 'inverse'."}
        where.append("flame_type = ?"); p.append(t)
    if outcome:
        if outcome not in OUTCOMES:
            return {"erro": f"outcome deve ser um de: {sorted(OUTCOMES)}"}
        where.append("outcome = ?"); p.append(outcome)
    for col, op, v in [("o2_fraction", ">=", o2_min), ("o2_fraction", "<=", o2_max),
                       ("pressure_bar", ">=", pressure_min_bar), ("pressure_bar", "<=", pressure_max_bar)]:
        if v is not None:
            where.append(f"{col} {op} ?"); p.append(float(v))
    cond = ("WHERE " + " AND ".join(where)) if where else ""
    limit = max(1, min(int(limit), 20))

    total = _consulta(f"SELECT COUNT(*) AS n, SUM(ignited) AS acenderam FROM testes {cond}", tuple(p))[0]
    exemplos = _consulta(f"SELECT {COLS_RESUMO} FROM testes {cond} ORDER BY test_id LIMIT ?", tuple(p) + (limit,))
    return {"total_encontrado": total["n"], "desses_acenderam": total["acenderam"] or 0,
            "exemplos": exemplos, "mostrando": len(exemplos), "nota": NOTA_BASE}


# ----------------------------------------------------------------------------
# 3. estatísticas por grupo
# ----------------------------------------------------------------------------
_FAIXAS = {
    "o2": ("CASE WHEN o2_fraction < 0.19 THEN '1) menos de 19% de O2 (abaixo do ar)' "
           "WHEN o2_fraction <= 0.24 THEN '2) 19-24% (parecido com o ar)' "
           "WHEN o2_fraction <= 0.35 THEN '3) 25-35%' "
           "WHEN o2_fraction <= 0.50 THEN '4) 36-50%' ELSE '5) mais de 50%' END"),
    "pressao": ("CASE WHEN pressure_bar <= 0.6 THEN '1) até 0,6 bar (~9 psi)' "
                "WHEN pressure_bar <= 1.2 THEN '2) 0,6-1,2 bar (como a Terra)' "
                "WHEN pressure_bar <= 2.3 THEN '3) 1,2-2,3 bar' ELSE '4) mais de 2,3 bar' END"),
    "combustivel": "fuel",
    "tipo_de_chama": "flame_type",
}


def comparar_grupos(agrupar_por: str, fuel: Optional[str] = None, flame_type: Optional[str] = None) -> dict:
    """Compara grupos de testes: quantos acenderam, por quanto tempo a chama durou e a temperatura
    teórica da chama. Use para perguntas como 'mais oxigênio deixa o fogo mais fácil?' ou
    'qual combustível queima melhor?'.
    agrupar_por: 'o2' (faixas de oxigênio), 'pressao' (faixas de pressão), 'combustivel' ou 'tipo_de_chama'.
    fuel / flame_type: filtros opcionais, como em buscar_testes.
    Atenção: em chamas inversas o O2 sai do queimador; em chamas normais ele enche a câmara. Para uma
    comparação justa de oxigênio, prefira filtrar flame_type."""
    if agrupar_por not in _FAIXAS:
        return {"erro": f"agrupar_por deve ser um de {list(_FAIXAS)}"}
    where, p = [], []
    if fuel:
        f = FUELS.get(_sem_acento(fuel).strip())
        if not f:
            return {"erro": "combustível inválido. Use ethane, propane ou butane."}
        where.append("fuel = ?"); p.append(f)
    if flame_type:
        t = FLAMES.get(_sem_acento(flame_type).strip())
        if not t:
            return {"erro": "flame_type deve ser 'normal' ou 'inverse'."}
        where.append("flame_type = ?"); p.append(t)
    cond = ("WHERE " + " AND ".join(where)) if where else ""
    g = _FAIXAS[agrupar_por]
    linhas = _consulta(f"""
        SELECT {g} AS grupo, COUNT(*) AS testes, SUM(ignited) AS acenderam,
               ROUND(100.0 * SUM(ignited) / COUNT(*), 1) AS pct_acenderam,
               ROUND(AVG(CASE WHEN ignited = 1 THEN burn_time_s END), 1) AS tempo_medio_queima_s,
               ROUND(AVG(tad_k), 0) AS temp_adiabatica_media_k
        FROM testes {cond} GROUP BY grupo ORDER BY grupo""", tuple(p))
    return {"agrupado_por": agrupar_por, "filtros": {"fuel": fuel, "flame_type": flame_type},
            "grupos": linhas, "nota": NOTA_BASE + " Grupos com poucos testes dizem pouco."}


# ----------------------------------------------------------------------------
# 4. cenários (Terra, traje, base lunar)
# ----------------------------------------------------------------------------
def listar_cenarios() -> dict:
    """Lista os ambientes de referência: ar da Terra, traje espacial e base lunar prevista,
    com oxigênio e pressão de cada um."""
    return {"cenarios": _consulta("SELECT scenario_id, nome, o2_min, o2_max, p_min_bar, p_max_bar, descricao FROM cenarios")}


def comparar_com_cenario(scenario_id: str) -> dict:
    """Mostra o que os experimentos encontraram em condições parecidas com um ambiente real.
    scenario_id: 'earth' (ar da Terra), 'spacesuit' (traje espacial) ou 'moon_base' (base lunar prevista).
    Se não houver nenhum teste naquela faixa, devolve os mais próximos e avisa. Use para perguntas como
    'e na Lua, o fogo seria pior?' (responda com cuidado: não há dados de gravidade lunar)."""
    cen = _consulta("SELECT * FROM cenarios WHERE scenario_id = ?", (scenario_id.strip().lower(),))
    if not cen:
        return {"erro": "cenário desconhecido", "validos": [c["scenario_id"] for c in _consulta("SELECT scenario_id FROM cenarios")]}
    c = cen[0]
    janela = (c["o2_min"], c["o2_max"], c["p_min_bar"], c["p_max_bar"])
    dentro = _consulta("""SELECT flame_type, COUNT(*) AS testes, SUM(ignited) AS acenderam,
                          ROUND(AVG(CASE WHEN ignited = 1 THEN burn_time_s END), 1) AS tempo_medio_queima_s,
                          MIN(o2_fraction) AS o2_min_testado, MAX(o2_fraction) AS o2_max_testado
                          FROM testes WHERE o2_fraction BETWEEN ? AND ? AND pressure_bar BETWEEN ? AND ?
                          GROUP BY flame_type""", janela)
    res = {"cenario": {k: c[k] for k in ("scenario_id", "nome", "descricao", "o2_min", "o2_max", "p_min_bar", "p_max_bar")},
           "testes_na_faixa": dentro, "nota": NOTA_BASE}
    if not dentro:
        prox = _consulta(f"""SELECT {COLS_RESUMO},
                              ((o2_fraction - ?) * (o2_fraction - ?) / 0.01 +
                               (pressure_bar - ?) * (pressure_bar - ?) / 0.25) AS distancia
                              FROM testes ORDER BY distancia LIMIT 5""",
                         (c["o2_centro"], c["o2_centro"], c["p_centro_bar"], c["p_centro_bar"]))
        res["aviso"] = ("NENHUM teste ficou nessa faixa de oxigênio e pressão. Abaixo estão os 5 mais próximos; "
                        "eles NÃO representam esse ambiente.")
        res["testes_mais_proximos"] = prox
    else:
        ex = _consulta(f"""SELECT {COLS_RESUMO} FROM testes
                           WHERE o2_fraction BETWEEN ? AND ? AND pressure_bar BETWEEN ? AND ?
                           ORDER BY burn_time_s DESC LIMIT 5""", janela)
        res["exemplos_maior_tempo_de_queima"] = ex
    return res


# ----------------------------------------------------------------------------
# 5. um teste específico
# ----------------------------------------------------------------------------
def detalhes_do_teste(test_id: str) -> dict:
    """Devolve todos os dados de UM teste a partir do código (ex.: '21196 A2').
    Use quando a pessoa citar um teste específico."""
    tid = re.sub(r"^(\d+)\s*([A-Za-z]+\d+)$", r"\1 \2", test_id.strip()).upper()
    r = _consulta("SELECT * FROM testes WHERE test_id = ?", (tid,))
    if not r:
        return {"erro": f"teste '{test_id}' não encontrado. Os códigos têm o formato '21196 A2'."}
    r[0].pop("_arquivo", None)
    return {"teste": r[0], "nota": NOTA_BASE}


# ----------------------------------------------------------------------------
# 6. glossário
# ----------------------------------------------------------------------------
GLOSSARIO_EXTRA = {
    "microgravidade": "Sensação de 'flutuar' dos astronautas em órbita. A gravidade não some: a estação e as pessoas estão em queda livre ao redor da Terra, então nada 'pesa'.",
    "psi (banco de dados)": "PSI (Physical Science Informatics) é o banco de dados público da NASA com resultados de experimentos de ciência física feitos no espaço.",
    "acme": "ACME (Advanced Combustion via Microgravity Experiments): conjunto de experimentos de combustão na Estação Espacial Internacional, com chamas de gases, feitos no Combustion Integrated Rack (CIR).",
    "cfi-g": "CFI-G (Cool Flames Investigation with Gases): parte do ACME que estudou chamas frias de gases, incluindo testes com muito oxigênio (até 85%).",
    "chama esférica": "Na Terra o ar quente sobe e estica a chama como uma gota. Na microgravidade isso não acontece e a chama tende a ficar redonda.",
    "pressão": "Força do gás sobre as superfícies. 1 bar ≈ 14,5 psi ≈ pressão do ar ao nível do mar. Trajes espaciais usam só 4,3 psi.",
    "oxigênio e fogo": "O fogo precisa de combustível, oxigênio e calor. Com mais oxigênio, os materiais pegam fogo com mais facilidade e queimam mais forte. Por isso a concentração de O2 de habitats lunares é uma preocupação.",
    "autoextinção": "A chama se apaga sozinha (código SE nas tabelas), sem que o experimento corte o gás.",
    "corte de fluxo": "O experimento interrompeu o fornecimento de gás (código FT) com a chama ainda acesa, por exemplo ao fim do tempo programado ou por limite de temperatura.",
    "chama inversa": "Chama 'de dentro para fora': o oxigênio sai de um queimador esférico e o combustível enche a câmara ao redor. É o contrário de uma vela, onde o combustível sai do pavio e o oxigênio está no ar ao redor.",
    "chama normal": "Chama comum: o combustível sai do queimador e o oxigênio está ao redor, como numa vela ou fogão.",
    "chama fria": "Chama muito mais fraca e de temperatura bem menor que uma chama comum, quase invisível. Importa porque pode existir em condições onde a chama comum não existe.",
}


ALIASES = {
    "burn time": "burn_time_s", "tempo de queima": "burn_time_s", "tempo": "burn_time_s", "duracao": "burn_time_s",
    "tad": "tad_k", "temperatura da chama": "tad_k", "temperatura": "tad_k", "adiabatica": "tad_k",
    "zst": "zst", "xo2": "o2_fraction", "o2": "o2_fraction", "oxigenio": "o2_fraction",
    "xf": "fuel_fraction", "combustivel": "fuel", "pressao": "pressure_bar", "psi": "pressure_psi",
    "bar": "pressure_bar", "tfp": "tfp", "ignicao": "ignition_type", "hot ignition": "ignition_type",
    "self-ext": "outcome", "flow term": "outcome", "cor": "flame_color", "pci": "cool_flame_code",
}


def explicar_termo(termo: str) -> dict:
    """Explica em linguagem simples um termo ou coluna dos dados (ex.: 'Tad', 'Zst', 'chama fria',
    'microgravidade', 'pressão', 'burn time'). Use sempre que a pessoa perguntar o que algo significa."""
    q = _sem_acento(termo).strip()
    achados = []
    col = ALIASES.get(q)
    if col:
        for r in _consulta("SELECT coluna, rotulo, unidade, explicacao_simples, observacao_tecnica "
                           "FROM dicionario WHERE coluna = ?", (col,)):
            achados.append({"termo": r["rotulo"], "coluna": r["coluna"], "unidade": r["unidade"],
                            "explicacao": r["explicacao_simples"], "observacao": r["observacao_tecnica"]})
    for k, v in GLOSSARIO_EXTRA.items():
        if q and (q in _sem_acento(k) or _sem_acento(k) in q):
            achados.append({"termo": k, "explicacao": v})
    for r in _consulta("SELECT coluna, rotulo, unidade, explicacao_simples, observacao_tecnica FROM dicionario"):
        alvo = _sem_acento(f"{r['coluna']} {r['rotulo']}")
        if q and (q in alvo or r["coluna"].lower() in q.replace(" ", "_")):
            achados.append({"termo": r["rotulo"], "coluna": r["coluna"], "unidade": r["unidade"],
                            "explicacao": r["explicacao_simples"], "observacao": r["observacao_tecnica"]})
    vistos, unicos = set(), []
    for a in achados:                      # tira repetidos (apelido + busca por texto)
        chave = (a["termo"], a.get("coluna"))
        if chave not in vistos:
            vistos.add(chave)
            unicos.append(a)
    achados = unicos
    if not achados:
        return {"erro": f"não achei '{termo}' no glossário.",
                "dica": "Não invente a definição; diga que não está no material e explique só o que for certo."}
    return {"resultados": achados[:4]}


FERRAMENTAS = [resumo_geral, buscar_testes, comparar_grupos, listar_cenarios,
               comparar_com_cenario, detalhes_do_teste, explicar_termo]
