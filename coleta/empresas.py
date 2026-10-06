"""Empresas em que cada político é sócio (Receita Federal, quadro de sócios do CNPJ).

A Receita publica o sócio pessoa física com o nome e só os 6 dígitos do meio do CPF (***123456**). A ligação com o
candidato exige o nome completo igual E esses 6 dígitos (o mesmo método do cadastro de servidores punidos), então
homônimo não se mistura. O CPF serve só para ligar: não vai para o arquivo nem para o site.
Gera dados/empresas_politicos.json.gz.Roda uma vez por mês (a Receita atualiza mensalmente).
Uso: python coleta/empresas.py [AAAA-MM]
"""
import base64
import csv
import io
import re
import sys
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import coletar  # noqa: E402

SAIDA = coletar.RAIZ / "dados" / "empresas_politicos.json.gz"
WEBDAV = "https://arquivos.receitafederal.gov.br/public.php/webdav"
TOKEN = "YggdBLfdninEJX9"  # pasta pública "Dados Abertos CNPJ" (link oficial no dados.gov.br)
QUALIFICACAO = {"49": "Sócio-administrador", "22": "Sócio", "05": "Administrador", "65": "Titular", "16": "Presidente",
                "10": "Diretor", "08": "Conselheiro", "54": "Fundador", "50": "Empresário", "30": "Sócio"}
csv.field_size_limit(10**8)


def ultimo_mes():
    req = urllib.request.Request(f"{WEBDAV}/", method="PROPFIND", headers={**coletar.UA, "Depth": "1", "Authorization": autorizacao()})
    with urllib.request.urlopen(req, timeout=120) as r:
        corpo = r.read().decode("utf-8", "ignore")
    return max(re.findall(r"/webdav/(\d{4}-\d{2})/", corpo))


def autorizacao():
    return "Basic " + base64.b64encode(f"{TOKEN}:".encode()).decode()


def arquivo(mes, nome):
    return coletar.baixar(f"{WEBDAV}/{mes}/{nome}", f"rfb/{mes}/{nome}", cabecalho={"Authorization": autorizacao()})


def matriz(basico):
    """CNPJ completo da matriz (filial 0001) com os dígitos verificadores."""
    n = basico + "0001"
    for pesos in ([5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2], [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]):
        resto = sum(int(a) * b for a, b in zip(n, pesos)) % 11
        n += str(0 if resto < 2 else 11 - resto)
    return n


def linhas(caminho):
    with zipfile.ZipFile(caminho) as z, z.open(z.namelist()[0]) as f:
        yield from csv.reader(io.TextIOWrapper(f, encoding="latin1"), delimiter=";")


def main():
    mes = sys.argv[1] if len(sys.argv) > 1 else ultimo_mes()
    cands = coletar.carregar_candidaturas()
    pessoas = defaultdict(list)
    for c in cands.values():
        if c["cpf"]:
            pessoas[c["cpf"]].append(c)
    # (nome completo, 6 dígitos do meio do CPF) -> CPF da pessoa (só em memória)
    chave = {}
    for cpf, cs in pessoas.items():
        for c in cs:
            chave[(coletar.norm_nome(c["nome"]), cpf[3:9])] = cpf
    socios = defaultdict(list)  # cpf -> [[cnpj_basico, qualificação, desde]]
    for i in range(10):
        for r in linhas(arquivo(mes, f"Socios{i}.zip")):
            if len(r) < 6 or r[1] != "2":  # 2 = sócio pessoa física
                continue
            meio = coletar.so_digitos(r[3])
            cpf = chave.get((coletar.norm_nome(r[2]), meio)) if len(meio) == 6 else None
            if cpf:
                desde = r[5]
                socios[cpf].append([r[0], QUALIFICACAO.get(r[4], "Sócio"),
                                    f"{desde[:4]}-{desde[4:6]}-{desde[6:]}" if len(desde) == 8 else ""])
        print(f"Socios{i}: {len(socios)} políticos sócios até agora", file=sys.stderr)
    basicos = {s[0] for lista in socios.values() for s in lista}
    empresa = {}
    for i in range(10):
        for r in linhas(arquivo(mes, f"Empresas{i}.zip")):
            if r and r[0] in basicos:
                # razão social sem CPF colado (firma antiga) e natureza jurídica (2011 e 2038 = estatal)
                empresa[r[0]] = [re.sub(r"\s*\d{9,}\s*", " ", r[1]).strip().title(), r[2]]
    def item(b, papel, desde):
        nome, natureza = empresa.get(b, ["", ""])
        return [matriz(b), nome, papel, desde, natureza]
    # saída pelos números de candidatura (públicos), nunca pelo CPF:
    # [[números], [[cnpj da matriz, empresa, papel, desde, natureza jurídica]]]
    saida = [[[c["sq"] for c in pessoas[cpf]], [item(*s) for s in lista]] for cpf, lista in socios.items()]
    coletar.gravar_json(SAIDA, {"mes": mes, "pessoas": saida})
    print(f"{len(saida)} políticos sócios de {len(basicos)} empresas (Receita {mes})", file=sys.stderr)


if __name__ == "__main__":
    assert matriz("11222333") == "11222333000181" and matriz("00000000") == "00000000000191"
    main()
