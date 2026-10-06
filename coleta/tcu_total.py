"""Tudo que o TCU mandou devolver, de todo mundo (não só candidatos): total por ano, por estado e por cidade.

Lê os mesmos CSVs de acórdãos do valores_tcu.py. Cada linha de tabela de débito conta uma vez por acórdão,
então débito solidário (várias pessoas pelo mesmo dinheiro) não é somado duas vezes.
Incremental: guarda o débito de cada acórdão em dados/tcu_acordaos.json e, nas próximas vezes, só lê o ano corrente
e os anos que ganharam acórdão novo na lista do TCU. Gera dados/tcu_total.json.
Uso: python coleta/tcu_total.py
"""
import csv
import html
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import coletar  # noqa: E402
import valores_tcu as v  # noqa: E402

SAIDA = coletar.RAIZ / "dados" / "tcu_total.json"
POR_ACORDAO = coletar.RAIZ / "dados" / "tcu_acordaos.json"


def debito_do_acordao(texto):
    """Soma de todas as parcelas de débito do acórdão (de todos os condenados), em real, e se houve parcela em
    moeda antiga (não somada)."""
    total, antigo, tipo_do = 0.0, False, {}
    for n, trecho in v.itens(texto):
        baixo = v.plano(trecho).lower()
        pai = n.rsplit(".", 1)[0]
        tipo = "debito" if "conden" in baixo else tipo_do.get(pai)
        tipo_do[n] = tipo
        if tipo != "debito":
            continue
        linhas = v.linhas_tabela(trecho)
        if linhas:
            for _, valor, velho, credito in linhas:
                if velho:
                    antigo = True
                else:
                    total += -valor if credito else valor
        else:
            antes_multa = re.split(r"multa", trecho, flags=re.I)[0]
            antigo = antigo or bool(v.MOEDA_ANTIGA.search(antes_multa))
            total += sum(v.num(x) for x in v.REAIS.findall(antes_multa))
    return max(total, 0.0), antigo


def main():
    # acórdão -> cidades dos responsáveis (o TCU informa município e UF de cada um)
    onde = defaultdict(Counter)
    for x in coletar.tcu("responsaveis-contas-irregulares"):
        ac = x.get("numeroAcordaoFormatado")
        if ac and x.get("uf"):
            onde[ac][(x["uf"], coletar.norm_nome(x.get("municipio") or ""))] += 1
    feitos = json.loads(POR_ACORDAO.read_text("utf-8")) if POR_ACORDAO.exists() else {}
    corrente = max(int(ac.split("/")[1][:4]) for ac in onde)
    anos = sorted({int(ac.split("/")[1][:4]) for ac in onde if ac not in feitos} | {corrente})
    for ano in anos:
        destino = coletar.CACHE / "acordaos" / f"ac{ano}.csv"
        if ano == corrente:
            destino.unlink(missing_ok=True)  # o ano corrente ganha acórdão novo todo dia
        try:
            coletar.baixar(v.URL.format(ano), f"acordaos/ac{ano}.csv")
        except coletar.urllib.error.HTTPError:  # o TCU não publica alguns anos (1990, por exemplo)
            print(f"{ano}: sem arquivo no TCU", file=sys.stderr)
            continue
        with open(destino, encoding="utf-8", newline="") as f:
            leitor = csv.reader(f, delimiter="|")
            col = {c: i for i, c in enumerate(next(leitor))}
            for linha in leitor:
                chave = (f"{linha[col['NUMACORDAO']]}/{linha[col['ANOACORDAO']]}-"
                         f"{v.COLEGIADO.get(linha[col['COLEGIADO']], linha[col['COLEGIADO']])}")
                if chave not in onde or not linha[col["TIPO"]].startswith("ACÓRDÃO"):
                    continue  # antes de 2003 há "Decisão" com o mesmo número
                texto = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", linha[col["ACORDAO"]])))
                deb, _ = debito_do_acordao(texto.translate(v.ASPAS_CP1252))
                feitos[chave] = round(deb)
        print(f"{ano}: lido", file=sys.stderr)
    coletar.gravar_json(POR_ACORDAO, dict(sorted(feitos.items())))

    por_ano, por_uf, por_cidade = Counter(), Counter(), Counter()
    for ac, deb in feitos.items():
        if not deb or ac not in onde:
            continue
        por_ano[int(ac.split("/")[1][:4])] += deb
        (uf, cidade), _ = onde[ac].most_common(1)[0]  # cidade da maioria dos responsáveis
        por_uf[uf] += deb
        if cidade:
            por_cidade[f"{uf}|{cidade}"] += deb
    dados = {
        "total": round(sum(por_ano.values())), "acordaos": sum(1 for ac, d in feitos.items() if d and ac in onde),
        "ano": {a: round(x) for a, x in sorted(por_ano.items())},
        "uf": {u: round(x) for u, x in por_uf.most_common()},
        "cidade": {c: round(x) for c, x in por_cidade.items()},
    }
    coletar.gravar_json(SAIDA, dados)
    print(f"total R$ {dados['total'] / 1e9:.1f} bi em {dados['acordaos']} acórdãos", file=sys.stderr)


if __name__ == "__main__":
    main()
