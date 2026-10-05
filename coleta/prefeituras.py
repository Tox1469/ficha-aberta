"""Quanto cada prefeitura recebeu e gastou, por área, a partir das contas anuais entregues ao Tesouro (SICONFI/DCA).

Gera dados/prefeituras.json (vai para o repositório: conta anual muda uma vez por ano).
Uso: python coleta/prefeituras.py [ano]   (padrão: ano passado; cidade que não entregou cai para o ano anterior)
"""
import datetime as dt
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SAIDA = RAIZ / "dados" / "prefeituras.json"
API = "https://apidatalake.tesouro.gov.br/ords/siconfi/tt"
FUNCAO = re.compile(r"^(\d{2}) - (.+)$")  # "10 - Saúde"; subfunções ("10.301 - ...") ficam de fora


class FalhaApi(Exception):
    pass


def get(caminho, **params):
    """Lista de itens; [] quando a cidade não entregou. Falha da API vira exceção (não pode virar 'não entregou')."""
    url = f"{API}/{caminho}?{urllib.parse.urlencode(params)}"
    for tentativa in range(8):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ficha-aberta"}), timeout=120) as r:
                return json.load(r)["items"]
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code not in (429, 500, 502, 503, 504):
                raise FalhaApi(f"{url}: {e}")
            time.sleep(min(60, 3 * 2 ** tentativa))  # 429 do Tesouro: espera e tenta de novo
    raise FalhaApi(url)


def conta_da_cidade(ente, ano):
    for exercicio in (ano, ano - 1):
        despesa = get("dca", an_exercicio=exercicio, no_anexo="DCA-Anexo I-E", id_ente=ente["cod_ibge"])
        if despesa:
            break
    else:
        return None
    receita = get("dca", an_exercicio=exercicio, no_anexo="DCA-Anexo I-C", id_ente=ente["cod_ibge"])
    funcoes, total = {}, 0.0
    for x in despesa:
        if x["coluna"] != "Despesas Liquidadas" or x["cod_conta"] != "TotalDespesas":
            continue
        if x["conta"] == "Despesas Exceto Intraorçamentárias":
            total = x["valor"]
        elif m := FUNCAO.match(x["conta"]):
            funcoes[m.group(2)] = round(x["valor"])
    brutas = [x for x in receita if x["coluna"].startswith("Receitas Brutas Realizadas")]
    rec_total = next((x["valor"] for x in brutas if x["cod_conta"] == "TotalReceitas"), 0)
    # royalties e compensações (petróleo, minério, energia): explicam muita cidade pequena com orçamento enorme
    royalties = sum(x["valor"] for x in brutas
                    if re.match(r"RO1\.7\.\d\.\d\.5\d\.0\.0$", x["cod_conta"])  # só o nível de cima: subconta somaria 2x
                    and re.search(r"royalt|compensa", x["conta"], re.I))
    return {
        "ibge": ente["cod_ibge"], "nome": ente["ente"], "uf": ente["uf"], "pop": despesa[0].get("populacao") or ente["populacao"],
        "ano": exercicio, "gasto": round(total), "receita": round(rec_total), "royalties": round(royalties), "areas": funcoes,
    }


def main():
    ano = int(sys.argv[1]) if len(sys.argv) > 1 else dt.date.today().year - 1
    entes = [e for e in get("entes") if e["esfera"] == "M"]
    print(f"{len(entes)} municípios, contas de {ano}", file=sys.stderr)
    feitas, faltam = {}, entes
    for rodada in range(4):  # quem falhou por limite da API tenta de novo, mais devagar
        def uma(e):
            try:
                return e["cod_ibge"], conta_da_cidade(e, ano)
            except FalhaApi as erro:
                return e["cod_ibge"], erro
        with ThreadPoolExecutor(3 if rodada == 0 else 1) as pool:
            resultados = list(pool.map(uma, faltam))
        feitas.update((k, v) for k, v in resultados if not isinstance(v, FalhaApi))
        faltam = [e for e in faltam if e["cod_ibge"] not in feitas]
        print(f"rodada {rodada + 1}: {len(feitas)} prontas, {len(faltam)} para tentar de novo", file=sys.stderr)
        if not faltam:
            break
    if faltam:
        raise SystemExit(f"{len(faltam)} cidades sem resposta da API; rode de novo mais tarde")
    cidades = [c for c in feitas.values() if c and c["gasto"]]
    SAIDA.parent.mkdir(exist_ok=True)
    SAIDA.write_text(json.dumps(sorted(cidades, key=lambda c: c["ibge"]), ensure_ascii=False, separators=(",", ":")),
                     "utf-8", newline="\n")
    print(f"{len(cidades)} prefeituras com conta entregue", file=sys.stderr)


if __name__ == "__main__":
    main()
