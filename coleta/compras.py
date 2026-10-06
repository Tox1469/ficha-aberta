"""Tudo o que foi comprado pelo Compras.gov.br (governo federal, estados e as prefeituras que usam o sistema), item por item.

Cada item vem com o código do produto no catálogo oficial do governo (CATMAT) e a unidade com o tamanho ("Caixa 100,00 UN",
"Galão 3,60 L"). Código + unidade iguais = mesmo produto na mesma embalagem: dá para comparar qualquer coisa com ela mesma,
sem lista de palavras. O preço normal e o "pago a mais" saem no coletar.py.

Um arquivo por dia de publicação em coleta/cache/compras/AAAA-MM-DD.json.gz (no GitHub fica no cache do Actions; se sumir,
o robô baixa de novo aos poucos). O preço fechado e o código de catálogo só aparecem semanas depois da publicação, por isso
o dia só é lido quando já tem ESPERA dias.
Uso: python coleta/compras.py [dias de janela]
"""
import datetime as dt
import gzip
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import coletar  # noqa: E402
from precos import cnpj_ou_nada  # noqa: E402

API = "https://dadosabertos.compras.gov.br/modulo-contratacoes"
PASTA = coletar.CACHE / "compras"
JANELA = 365  # dias de compra que entram no preço normal
ESPERA = 60  # antes disso a maioria dos itens ainda não tem preço fechado
RELER = 150  # o resultado continua chegando depois: o dia lido cedo é lido de novo, uma vez, nessa idade
MODALIDADES = (3, 5, 6, 7)  # concorrência, pregão, dispensa e inexigibilidade (as únicas que o sistema publica)
PRAZO_MINUTOS = 60  # o resto fica para o dia seguinte


def pegar(caminho, **params):
    """Uma página da API. Ela tem limite de pedidos e responde {"statusCode": 429, "message": "... in 9 seconds"}:
    isso nunca pode virar "dia sem compras", então espera e tenta de novo; resposta sem "resultado" é erro."""
    url = f"{API}/{caminho}?" + urllib.parse.urlencode(params)
    for i in range(10):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=coletar.UA), timeout=180) as r:
                d = json.loads(r.read())
        except urllib.error.HTTPError as e:
            d = {"statusCode": e.code, "message": e.read().decode("utf-8", "ignore")}
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError):
            d = {}
        if isinstance(d.get("resultado"), list):
            return d
        espera = re.search(r"in (\d+) second", str(d.get("message", "")))
        time.sleep(int(espera.group(1)) + 1 if espera else 2 ** min(i, 6))
    raise RuntimeError(f"sem resposta: {url}")


def linhas(caminho, **params):
    pagina, total = 1, 1
    while pagina <= total:
        d = pegar(caminho, pagina=pagina, tamanhoPagina=500, **params)
        total = d.get("totalPaginas") or 0
        yield from d.get("resultado") or []
        pagina += 1


def ler_dia(dia):
    """Compras publicadas no dia (onde, quem comprou) e os itens com preço fechado e código de catálogo."""
    de, ate = dia.isoformat(), (dia + dt.timedelta(days=1)).isoformat()
    compras = {}
    for m in MODALIDADES:
        for x in linhas("1_consultarContratacoes_PNCP_14133", dataPublicacaoPncpInicial=de, dataPublicacaoPncpFinal=ate,
                        codigoModalidade=m):
            compras[x["numeroControlePNCP"]] = [x.get("unidadeOrgaoMunicipioNome") or "", x.get("unidadeOrgaoUfSigla") or "",
                                                x.get("orgaoEntidadeEsferaId") or "", x.get("orgaoEntidadeRazaoSocial") or "",
                                                x.get("modalidadeNome") or ""]
    itens = []
    for x in linhas("2_consultarItensContratacoes_PNCP_14133", dataInclusaoPncpInicial=de, dataInclusaoPncpFinal=ate,
                    materialOuServico="M"):
        preco = x.get("valorUnitarioResultado")
        if not preco or preco <= 0:
            continue
        # desde abril/2026 o governo deixou de preencher o código de catálogo (aqui e no PNCP); a descrição continua
        # sendo gerada pelo catálogo, então descrição idêntica faz o papel do código (o coletar.py agrupa assim)
        itens.append([x.get("codItemCatalogo"), (x.get("unidadeMedida") or "").strip(), preco, x.get("quantidadeResultado") or 0,
                      x.get("nomePdm") or "", (x.get("descricaodetalhada") or x.get("descricaoResumida") or "").strip()[:400],
                      x["numeroControlePNCPCompra"], (x.get("nomeFornecedor") or "").strip(),
                      cnpj_ou_nada(x.get("codFornecedor")),  # vendedor pessoa física: o documento não entra
                      (x.get("dataResultado") or "")[:10], x["idCompraItem"]])
    return {"compras": compras, "itens": itens}


def main():
    janela = int(sys.argv[1]) if len(sys.argv) > 1 else JANELA
    PASTA.mkdir(parents=True, exist_ok=True)
    hoje = dt.date.today()
    dias = [hoje - dt.timedelta(days=n) for n in range(ESPERA, janela + ESPERA)]

    def precisa(dia):
        arq = PASTA / f"{dia}.json.gz"
        if not arq.exists():
            return True
        lido = dt.date.fromtimestamp(arq.stat().st_mtime)
        return (hoje - dia).days >= RELER and (lido - dia).days < RELER
    falta = [d for d in dias if precisa(d)]
    print(f"compras: {len(falta)} dias para ler", file=sys.stderr)
    fim = time.monotonic() + PRAZO_MINUTOS * 60

    def um(dia):
        if time.monotonic() > fim:
            return None
        dados = ler_dia(dia)
        (PASTA / f"{dia}.json.gz").write_bytes(gzip.compress(json.dumps(dados, separators=(",", ":")).encode()))
        return len(dados["itens"])

    with ThreadPoolExecutor(2) as pool:
        for dia, n in zip(falta, pool.map(um, falta)):
            if n is not None:
                print(f"compras {dia}: {n} itens", file=sys.stderr)
    # dia que saiu da janela não serve mais
    for velho in PASTA.glob("*.json.gz"):
        if velho.name[:10] < str(hoje - dt.timedelta(days=janela + ESPERA)):
            velho.unlink()


if __name__ == "__main__":
    main()
