"""Preço pago em compras públicas (PNCP), item por item, para comparar com o preço normal do mesmo produto.

Cada categoria diz o que buscar, como reconhecer o produto na descrição e como agrupar o que é igual
(dipirona 500 mg comprimido com dipirona 500 mg comprimido; ar-condicionado de 12 mil BTUs com o de 12 mil BTUs).
Incremental: guarda as compras já lidas e, nas próximas vezes, só lê as novas (a busca vem da mais nova).
Gera dados/precos.json. Uso: python coleta/precos.py [categoria ...]
"""
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SAIDA = RAIZ / "dados" / "precos.json"
API = "https://pncp.gov.br/api"


def sem_acento(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


# ---------- como reconhecer e agrupar cada produto ----------

def parquinho(desc, un):
    d = sem_acento(desc)
    if re.search(r"natal|ilumina|\bled\b|inflav|miniatura|pedagog|de mesa|boneco|fantasia", d) or \
            re.search(r"^\W*(servico|manuten|reforma|pintura|locacao|aluguel|instala|projeto|recupera)", d):
        return None
    for nome, rx in (("Parque infantil completo", r"parque infantil|playground|parquinho|conjunto de brinquedos"),
                     ("Gangorra", r"\bgangorra"), ("Escorregador", r"escorregador|tobog"),
                     ("Gira-gira", r"gira[- ]?gira|carrossel"), ("Balanço", r"\bbalanco\b")):
        if re.search(rx, d):
            return nome
    return None


def combustivel(desc, un):
    d, u = sem_acento(desc), sem_acento(un or "")
    if not re.search(r"^l\b|^lt|litro", u) and not re.search(r"\blitro", d):
        return None  # só preço por litro
    if re.search(r"lubrific|arla|aditivo para|graxa|oleo (de )?motor|2 tempos|hidraulic", d):
        return None
    for nome, rx in (("Gasolina aditivada", r"gasolina\s+aditivada"), ("Gasolina comum", r"gasolina"),
                     ("Diesel S10", r"diesel\W*s\W*10\b"), ("Diesel S500", r"diesel\W*s\W*500"),
                     ("Etanol", r"\betanol|alcool (etilico )?(hidratado|combust)")):
        if re.search(rx, d):
            return nome
    return None


FORMAS = [("comprimido", r"comprimido|\bcomp\b|\bcp\b|drag"), ("cápsula", r"capsula|\bcaps\b"),
          ("ampola", r"ampola|\bamp\b"), ("frasco", r"frasco|\bfr\b|xarope|suspensao|solucao oral|gotas"),
          ("bisnaga", r"bisnaga|pomada|creme"), ("bolsa", r"\bbolsa\b|sistema fechado")]


def remedio(desc, un):
    d = sem_acento(desc)
    m = re.match(r"\W*([a-z][a-z ,+\-]{3,60}?)\s*,?\s*(\d+(?:[.,]\d+)?)\s*(mg|mcg|g|ml|ui|%)(?:\s*/\s*(\d+(?:[.,]\d+)?)?\s*(ml|g))?", d)
    if not m:
        return None
    nome = re.sub(r"\s+", " ", m.group(1)).strip(" ,-")
    if len(nome) < 4 or re.search(r"seringa|agulha|luva|equipo|cateter|sonda|kit|teste|reagente|fio de sutura", nome):
        return None
    forma = next((f for f, rx in FORMAS if re.search(rx, d)), None)
    if not forma:
        return None
    dose = m.group(2).replace(",", ".") + m.group(3) + (f"/{m.group(4) or ''}{m.group(5)}" if m.group(5) else "")
    if forma in ("frasco", "bisnaga", "bolsa"):
        # frasco de 10 ml e de 20 ml têm preço diferente: o tamanho entra no grupo (sem tamanho, fica de fora)
        tamanhos = re.findall(r"(\d+(?:[.,]\d+)?)\s*(ml|g|l)\b", d[m.end():])
        if not tamanhos:
            return None
        forma = f"{forma} de {tamanhos[-1][0].replace(',', '.')} {tamanhos[-1][1]}"
    return f"{nome.capitalize()} {dose} ({forma})"


def ar_condicionado(desc, un):
    d = sem_acento(desc)
    if not re.search(r"ar[- ]condicionado|split", d) or re.search(r"manuten|instalacao de|limpeza|servico|pec[ao]|controle remoto", d[:60]):
        return None
    m = re.search(r"(\d{1,2})[.\s]?(000)\s*btu", d)
    if not m:
        return None
    btus = int(m.group(1)) * 1000
    if btus not in (7000, 9000, 12000, 18000, 22000, 24000, 30000, 36000, 48000, 60000):
        return None
    return f"Ar-condicionado de {btus // 1000} mil BTUs"


CATEGORIAS = {
    "parquinho": {"nome": "Brinquedo de parquinho", "agrupa": parquinho, "desde": 2021, "por_uf": False,
                  "buscas": ["parque infantil", "playground", "parquinho", "gangorra", "escorregador", "balanço infantil"]},
    "combustivel": {"nome": "Combustível (litro)", "agrupa": combustivel, "desde": 2025, "por_uf": True,
                    "buscas": ["gasolina", "óleo diesel", "etanol combustível"], "max_paginas": 25},
    "remedio": {"nome": "Remédio", "agrupa": remedio, "desde": 2025, "por_uf": False,
                "buscas": ["medicamentos", "aquisição de medicamentos"], "max_paginas": 25},
    "ar": {"nome": "Ar-condicionado", "agrupa": ar_condicionado, "desde": 2024, "por_uf": False,
           "buscas": ["ar condicionado split"], "max_paginas": 25},
}


# ---------- coleta ----------

def pegar(url, tentativas=6):
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ficha-aberta (github.com/Tox1469/ficha-aberta)",
                                                       "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                corpo = r.read()
            return json.loads(corpo) if corpo else []
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []
            time.sleep(2 ** i)
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError):
            time.sleep(2 ** i)  # resposta que não é JSON = limite do servidor: espera e tenta de novo
    return None


def compras(cat, lidas):
    """Compras com resultado da categoria, da mais nova para a mais velha. Para quando a página inteira já foi lida
    antes, quando passa do ano inicial ou no limite de páginas (categorias enormes, como combustível)."""
    conf, achadas = CATEGORIAS[cat], {}
    for q in conf["buscas"]:
        for pagina in range(1, conf.get("max_paginas", 10 ** 6) + 1):
            url = f"{API}/search/?" + urllib.parse.urlencode({"q": q, "tipos_documento": "edital", "ordenacao": "-data",
                                                               "pagina": pagina, "tam_pagina": 100, "status": "concluido"})
            itens = (pegar(url) or {}).get("items") or []
            if not itens:
                break
            novas = 0
            for x in itens:
                chave = f"{x['orgao_cnpj']}/{x['ano']}/{x['numero_sequencial']}"
                if not x.get("tem_resultado") or int(x.get("ano") or 0) < conf["desde"] or chave in lidas:
                    continue
                novas += 1
                achadas[chave] = {"orgao": x.get("orgao_nome") or "", "mun": x.get("municipio_nome") or "",
                                  "uf": x.get("uf") or "", "esfera": x.get("esfera_nome") or "",
                                  "data": (x.get("data_publicacao_pncp") or "")[:10]}
            if int(itens[-1].get("ano") or 0) < conf["desde"] or (lidas and not novas):
                break
            time.sleep(0.3)
        print(f"[{cat}] busca '{q}': {len(achadas)} compras novas", file=sys.stderr)
    return achadas


def itens_da_compra(cat, chave, info):
    cnpj, ano, seq = chave.split("/")
    base = f"{API}/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens"
    out = []
    for pagina in range(1, 20):
        itens = pegar(f"{base}?pagina={pagina}&tamanhoPagina=500") or []
        for it in itens:
            grupo = CATEGORIAS[cat]["agrupa"](it.get("descricao") or "", it.get("unidadeMedida") or "")
            if not grupo or it.get("materialOuServico") != "M" or not it.get("temResultado"):
                continue
            for r in pegar(f"{base}/{it['numeroItem']}/resultados") or []:
                unit, qtd = r.get("valorUnitarioHomologado"), r.get("quantidadeHomologada") or it.get("quantidade")
                if not unit or unit <= 0 or r.get("situacaoCompraItemResultadoId") not in (1, None):
                    continue
                out.append([cat, grupo, (it.get("descricao") or "").strip()[:300], round(unit, 4), qtd,
                            info["mun"], info["uf"], info["orgao"], info["esfera"], info["data"],
                            (r.get("nomeRazaoSocialFornecedor") or "").strip(),
                            cnpj_ou_nada(r.get("niFornecedor")),  # vendedor pessoa física: o documento não entra
                            f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}"])
        if len(itens) < 500:
            break
    return out


def cnpj_ou_nada(s):
    """Só CNPJ de empresa (14 dígitos). CPF de quem vende como pessoa física não é guardado."""
    d = re.sub(r"\D", "", s or "")
    return d if len(d) == 14 else ""


def salvar(dados):
    sys.path.insert(0, str(Path(__file__).parent))
    import coletar  # a trava de CPF mora lá
    coletar.gravar_json(SAIDA, dados)


def main():
    dados = json.loads(SAIDA.read_text("utf-8")) if SAIDA.exists() else {"lidas": {}, "itens": []}
    for cat in sys.argv[1:] or list(CATEGORIAS):
        lidas = set(dados["lidas"].get(cat, []))
        lista = compras(cat, lidas)
        print(f"[{cat}] {len(lista)} compras novas para ler", file=sys.stderr)
        feitos = 0
        with ThreadPoolExecutor(3) as pool:
            for chave, itens in zip(lista, pool.map(lambda k: itens_da_compra(cat, k, lista[k]), lista)):
                dados["itens"] += itens
                lidas.add(chave)
                feitos += 1
                if feitos % 100 == 0:
                    dados["lidas"][cat] = sorted(lidas)
                    salvar(dados)
                    print(f"[{cat}] {feitos}/{len(lista)} compras, {sum(1 for i in dados['itens'] if i[0] == cat)} itens",
                          file=sys.stderr)
        dados["lidas"][cat] = sorted(lidas)
        salvar(dados)
        print(f"[{cat}] pronto: {sum(1 for i in dados['itens'] if i[0] == cat)} itens", file=sys.stderr)


if __name__ == "__main__":
    main()
