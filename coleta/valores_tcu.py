"""Extrai do texto dos acórdãos do TCU quanto cada candidato foi condenado a devolver (débito) e a multa.

Fonte: dados abertos de jurisprudência do TCU (um CSV por ano com o texto completo dos acórdãos).
Gera dados/valores_tcu.json, que vai para o repositório (texto de acórdão não muda) e é lido pelo coletar.py.
Uso: python coleta/valores_tcu.py            (anos que faltam + ano corrente)
     python coleta/valores_tcu.py 2026 2025  (só esses anos)
"""
import csv
import html
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import coletar  # noqa: E402

SAIDA = coletar.RAIZ / "dados" / "valores_tcu.json"
URL = "https://sites.tcu.gov.br/dados-abertos/jurisprudencia/arquivos/acordao-completo/acordao-completo-{}.csv"
COLEGIADO = {"Plenário": "PL", "Primeira Câmara": "1C", "Segunda Câmara": "2C"}
csv.field_size_limit(10**9)

# item do dispositivo: "9.1.", "9.2.1." (acórdãos antigos usam 8.x)
ITEM = re.compile(r"(?<![\d.,/])([89](?:\.\d{1,2}){1,3})\.?\s*[-–]?\s+(?=[A-Za-zÀ-ú])")  # "9.1." e "8.1. -"
ITEM_LETRA = re.compile(r"(?<![\w)])([a-l])\)\s+(?=[A-Za-zÀ-ú])")  # acórdão antigo: "a) julgar...", "b) autorizar..."
VALOR = r"\d{1,3}(?:\.\d{3})*,\d{2}"
DATA = r"(\d{1,2})º?/(\d{1,2})/(\d{2,4})"
# tabela de débito vem nos dois jeitos: "30/7/2010 3.711.765,53" ou "2.200.000,00 30/12/2008"
# a moeda pode vir entre a data e o valor ("17/8/1992 Cr$ 21.045.703.979,68"): Cr$/CR$/Cz$ não é real
LINHA_DV = re.compile(rf"{DATA}\s+(?:((?:NCz|Cz|CR|Cr|R)\$)\s*)?({VALOR})")
LINHA_VD = re.compile(rf"({VALOR})\s+{DATA}")
CREDITO = re.compile(r"\s*(?:\(\s*C\s*\)|C(?![\wÀ-ú])|Cr[ée]dito)")  # parcela já devolvida: desconta
# cabeçalho de bloco dentro de um item: "Débitos relacionados ao responsável X:", "Débito imputado a Y:"
CABECALHO = re.compile(r"D[ée]bitos?\s+(?:relacionad|imputad|de\s+responsabilidade|solid[áa]ri|atribu[íi]d)"
                       r"|Respons[áa]ve(?:l|is)(?:\s+solid[áa]ri\w*)?\s*:", re.I)
ENTIDADE = re.compile(r"\b(?:Sr|Sra|Srs|Sras|Munic[íi]pio|Prefeitura|empresa|Ltda|EIRELI|Associa\w*|Instituto"
                      r"|Funda[çc][ãa]o|Cons[óo]rcio|C[âa]mara|Secretaria|CPF|CNPJ)\b", re.I)
PALAVRAS_DE_TABELA = {"DATA", "DATAS", "VALOR", "VALORES", "HISTORICO", "ORIGINAL", "ORIGINAIS", "OCORRENCIA",
                      "DEBITO", "DEBITOS", "CREDITO", "TIPO", "PARCELA", "TOTAL", "R", "EM", "QUANTIA", "QUANTIAS"}
# "aplicar-lhe a multa", "condená-lo": o item fala da pessoa citada no item anterior
PRONOME = re.compile(r"\b(?:aplicar|aplicando|imputar|imputando) lhes?\b|\bcondena l[oa]s?\b|\bcondenando [oa]s?\b")  # sobre o texto normalizado
# "CR$ 1.000,00" contém "R$ 1.000,00": real só quando o R$ não vem colado em letra
REAIS = re.compile(rf"(?<![A-Za-z])R\$\s*({VALOR})")
ASPAS_CP1252 = {0x91: "'", 0x92: "'", 0x93: '"', 0x94: '"', 0x96: "-", 0x97: "-"}  # lixo de encoding nos CSVs antigos
MOEDA_ANTIGA = re.compile(r"(?:NCz|Cz|CR|Cr)\$")
# depois do verbo: "condenando-o ao", "condená-los ao", "condenar os responsáveis", "condenar solidariamente"
CONDENA_GRUPO = re.compile(r"conden\w*\s+(?:l?[oa]s?\s+(?:(?:ao|a)\b|solidari)|os responsaveis|solidariamente)")


def num(v):
    return float(v.replace(".", "").replace(",", "."))


def antes_do_real(mes, ano):
    """Parcela de antes de julho de 1994 está em cruzeiro, cruzeiro real ou cruzado: não dá para somar como real."""
    ano = int(ano)
    ano = ano + 1900 if ano < 100 else ano
    return (ano, int(mes)) < (1994, 7)


def itens(texto):
    """[(número, trecho)] do dispositivo do acórdão."""
    marcas = list(ITEM.finditer(texto)) or list(ITEM_LETRA.finditer(texto))
    return [(m.group(1), texto[m.start():(marcas[i + 1].start() if i + 1 < len(marcas) else len(texto))])
            for i, m in enumerate(marcas)]


def plano(s):
    """Maiúsculas sem acento e sem pontuação, com o MESMO tamanho do texto: as posições continuam valendo."""
    out = []
    for ch in s:
        a = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()[:1].upper()
        out.append(a if a.isalpha() else " ")
    return "".join(out)


def junto(s):
    return re.sub(r"\s+", " ", plano(s))


def nomeia_alguem(cabeca):
    """O começo do bloco fala de uma pessoa ou entidade? ("Município de X:", "Sr. Fulano:", "Fulano de Tal:")."""
    if ENTIDADE.search(cabeca):
        return True
    seguidas = 0
    for palavra in re.findall(r"[A-Za-zÀ-ú]+", cabeca):
        if palavra.lower() in ("da", "de", "do", "das", "dos", "e"):
            continue  # "Maria da Silva": conector não quebra o nome
        maiuscula = palavra[0].isupper() and plano(palavra).strip() not in PALAVRAS_DE_TABELA
        seguidas = seguidas + 1 if maiuscula else 0
        if seguidas >= 2:
            return True
    return False


def linhas_tabela(trecho):
    """[(posição, valor, antes_do_real, crédito)] das linhas da tabela de débito, na ordem que a tabela usa."""
    dv, vd = list(LINHA_DV.finditer(trecho)), list(LINHA_VD.finditer(trecho))
    if len(vd) > len(dv):  # valor antes da data
        return [(m.start(), num(m.group(1)), antes_do_real(m.group(3), m.group(4)), bool(CREDITO.match(trecho, m.end())))
                for m in vd]
    return [(m.start(), num(m.group(5)), antes_do_real(m.group(2), m.group(3)) or (m.group(4) or "R$") != "R$",
             bool(CREDITO.match(trecho, m.end()))) for m in dv]


def linhas_da_pessoa(trecho, linhas, alvo):
    """Dentro de um item com vários blocos ("Débitos de A: ...; Débitos de B: ..."), só as linhas do bloco de quem
    interessa. Bloco sem nome no cabeçalho vale para todos os condenados no item."""
    cabecalhos = [m.start() for m in CABECALHO.finditer(trecho)]
    if not cabecalhos:
        return linhas
    escolhidas = [l for l in linhas if l[0] < cabecalhos[0]]
    for i, ini in enumerate(cabecalhos):
        fim = cabecalhos[i + 1] if i + 1 < len(cabecalhos) else len(trecho)
        bloco = [l for l in linhas if ini <= l[0] < fim]
        cabeca = trecho[ini:bloco[0][0] if bloco else fim]
        if alvo in junto(cabeca) or not nomeia_alguem(cabeca.split(":")[0]):
            escolhidas += bloco
    return escolhidas


def condena_alvo(baixo, alvo):
    """'Julgar irregulares as contas de A e B, condenando A': B está no item mas não foi condenado a pagar.
    Vale se o nome vem depois do verbo condenar, ou se o verbo pega todo mundo (plural, pronome)."""
    i = baixo.find("conden")
    return i < 0 or alvo in baixo[i:] or bool(CONDENA_GRUPO.match(re.sub(r"\s+", " ", baixo[i:])))


def extrair(texto, nome):
    """Débito e multa de uma pessoa num acórdão. O item precisa citar o nome dela (ou ser subitem de um que cita)."""
    alvo = coletar.norm_nome(nome)
    alvo_b = alvo.lower()
    tipo_do, cita, anterior = {}, {}, False
    deb = mul = 0.0
    solidario, antigo, trechos = False, False, []
    for n, trecho in itens(texto):
        pl = plano(trecho)
        baixo = re.sub(r"\s+", " ", pl.lower())
        pai = n.rsplit(".", 1)[0]
        tipo = ("debito" if "conden" in baixo else "multa" if "multa" in baixo and "aplic" in baixo
                else tipo_do.get(pai))
        tipo_do[n] = tipo
        nomeado = alvo in junto(trecho)
        # subitem herda o nome do item de cima, a não ser que ele mesmo diga de quem é ("9.2.2. Município de X:")
        cabeca = re.sub(r"^[\d.]+\s*", "", trecho)
        primeira = min([m.start() for m in (LINHA_DV.search(cabeca), LINHA_VD.search(cabeca), REAIS.search(cabeca)) if m] or [250])
        herda = cita.get(pai, False) and not nomeia_alguem(cabeca[:primeira].split(":")[0][:250])
        cita[n] = nomeado or herda or (anterior and bool(PRONOME.search(baixo)))
        anterior = cita[n]
        if not tipo or not cita[n]:
            continue
        if tipo == "debito" and nomeado and not condena_alvo(baixo, alvo_b):
            continue
        if tipo == "debito":
            linhas = linhas_da_pessoa(trecho, linhas_tabela(trecho), alvo)
            v = 0.0
            if linhas:
                for _, valor, velho, credito in linhas:
                    if velho:
                        antigo = True
                    else:
                        v += -valor if credito else valor
            elif not LINHA_DV.search(trecho) and not LINHA_VD.search(trecho):  # "ao pagamento da quantia de R$ 50.000,00"
                antes_multa = re.split(r"multa", trecho, flags=re.I)[0]
                antigo = antigo or bool(MOEDA_ANTIGA.search(antes_multa))
                v = sum(num(x) for x in REAIS.findall(antes_multa))
            if antigo and not v:
                trechos.append(trecho[:600])
            # o item que condena e também aplica multa no mesmo parágrafo
            if "multa" in baixo and "aplic" in baixo:
                depois = re.split(r"multa", trecho, maxsplit=1, flags=re.I)[1]
                mul += sum(num(x) for x in REAIS.findall(depois)[:1])
            if v > 0:
                deb += v
                solidario = solidario or "solidari" in baixo
                trechos.append(trecho[:600])
        elif tipo == "multa":
            # "aplicar ao sr. A a multa de R$ 10.000,00 e ao sr. B a de R$ 5.000,00": a primeira depois do nome.
            # Tabela de multas sem "R$" ("Fulano 40.000,00"): o primeiro valor depois do nome.
            pos = re.search(r"\s+".join(map(re.escape, alvo.split())), pl)
            depois = pos.end() if pos else 0
            achados = [m.group(1) for m in REAIS.finditer(trecho) if m.start() >= depois]
            if not achados and pos:
                achados = re.findall(rf"(?<![\d.,])({VALOR})", trecho[depois:])[:1]
            if achados:
                mul += num(achados[0])
                trechos.append(trecho[:300])
    if not deb and not mul and not antigo:
        return None
    return {"deb": round(deb, 2), "mul": round(mul, 2), "sol": solidario, "ant": antigo,
            "tr": esconder_cpf(" […] ".join(trechos)[:900])}


esconder_cpf = coletar.esconder_cpf  # a trava de CPF mora no coletar.py


def necessarios():
    """{acórdão: {nome}} de quem é candidato."""
    cands = coletar.carregar_candidaturas()
    cpfs = {c["cpf"] for c in cands.values() if c["cpf"]}
    precisa = defaultdict(set)
    for ep in ("responsaveis-contas-irregulares", "responsaveis-fins-eleitorais", "responsaveis-inabilitados"):
        for x in coletar.tcu(ep):
            ac = x.get("numeroAcordaoFormatado")
            if ac and coletar.so_digitos(x["numeroRegistro"]) in cpfs:
                precisa[ac].add(x["nome"])
    return precisa


def processar_ano(ano, precisa, saida, corrente):
    destino = coletar.CACHE / "acordaos" / f"ac{ano}.csv"
    if ano == corrente:
        destino.unlink(missing_ok=True)  # o arquivo do ano corrente ganha acórdão novo todo dia
    coletar.baixar(URL.format(ano), f"acordaos/ac{ano}.csv")
    achados = 0
    with open(destino, encoding="utf-8", newline="") as f:
        leitor = csv.reader(f, delimiter="|")
        col = {c: i for i, c in enumerate(next(leitor))}
        for linha in leitor:
            chave = (f"{linha[col['NUMACORDAO']]}/{linha[col['ANOACORDAO']]}-"
                     f"{COLEGIADO.get(linha[col['COLEGIADO']], linha[col['COLEGIADO']])}")
            if chave not in precisa:
                continue
            if not linha[col["TIPO"]].startswith("ACÓRDÃO"):  # antes de 2003 há "Decisão" com o mesmo número
                continue
            texto = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", linha[col["ACORDAO"]])))
            texto = texto.translate(ASPAS_CP1252)
            saida[chave] = {coletar.norm_nome(nome): extrair(texto, nome) for nome in precisa[chave]}
            achados += 1
    for chave in precisa:
        saida.setdefault(chave, {})  # procurado e não achado no arquivo: não baixa o ano de novo por causa dele
    return achados


def main():
    saida = json.loads(SAIDA.read_text("utf-8")) if SAIDA.exists() else {}
    feitos = set(saida.pop("_anos", []))
    precisa = necessarios()
    anos_precisos = sorted({int(ac.split("/")[1][:4]) for ac in precisa})
    corrente = max(anos_precisos)
    # ano corrente sempre (sai acórdão novo todo dia) + anos com acórdão que entrou na lista e ainda não foi lido
    faltando = {int(ac.split("/")[1][:4]) for ac in precisa if ac not in saida}
    anos = [int(a) for a in sys.argv[1:]] or sorted(faltando | {corrente})
    for ano in anos:
        n = processar_ano(ano, {k: v for k, v in precisa.items() if f"/{ano}-" in k}, saida, corrente)
        feitos.add(ano)
        print(f"{ano}: {n} acórdãos", file=sys.stderr)
        SAIDA.parent.mkdir(exist_ok=True)
        coletar.gravar_json(SAIDA, {"_anos": sorted(feitos), **dict(sorted(saida.items()))}, indent=0)


if __name__ == "__main__":
    main()
