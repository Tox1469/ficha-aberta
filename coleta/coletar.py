"""Baixa dados públicos (TSE, TCU, CGU), cruza por CPF e gera site/dados.json.

Só biblioteca padrão. Uso: python coleta/coletar.py
Os downloads ficam em coleta/cache/ (apague a pasta para baixar de novo).
"""
import csv
import datetime as dt
import gzip
import io
import json
import re
import shutil
import sys
import unicodedata
import urllib.error
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CACHE = RAIZ / "coleta" / "cache"
SAIDA = RAIZ / "site" / "dados.json"
MANUAIS = RAIZ / "dados" / "casos_manuais.json"
VALORES = RAIZ / "dados" / "valores_tcu.json"  # gerado por valores_tcu.py
ANOS = (2018, 2020, 2022, 2024, 2026)
ANOS_GERAIS = (2018, 2022, 2026)  # federal/estadual: um arquivo só; municipais: um por estado
TSE = "https://cdn.tse.jus.br/estatistica/sead/odsele"
TCU = "https://certidoes.apps.tcu.gov.br/api/publico"
CGU = "https://dadosabertos-download.cgu.gov.br/PortalDaTransparencia/saida"
UA = {"User-Agent": "ficha-aberta (github.com/Tox1469/ficha-aberta)"}

csv.field_size_limit(10**8)


# ---------- utilidades ----------

def baixar(url, nome, corpo=None, cabecalho=None):
    """Baixa para o cache. Servidor do governo derruba conexão no meio sem dar erro (o CSV do TCU vinha com 16 MB
    de 360): confere o Content-Length e continua de onde parou com Range."""
    destino = CACHE / nome
    if destino.exists() and destino.stat().st_size > 1000:
        return destino
    destino.parent.mkdir(parents=True, exist_ok=True)
    print("baixando", url, file=sys.stderr)
    cab = dict(UA, **({"Content-Type": "application/json"} if corpo else {}), **(cabecalho or {}))
    tmp = destino.with_suffix(".parcial")
    tmp.unlink(missing_ok=True)
    total = None
    for _ in range(30):
        tem = tmp.stat().st_size if tmp.exists() else 0
        if total is not None and tem >= total:
            break
        extra = {"Range": f"bytes={tem}-"} if tem else {}
        req = urllib.request.Request(url, data=corpo, headers={**cab, **extra})
        try:
            with urllib.request.urlopen(req, timeout=600) as r, open(tmp, "ab" if tem else "wb") as f:
                if tem and r.status != 206:  # servidor ignorou o Range: recomeça
                    f.truncate(0)
                faixa = r.headers.get("Content-Range")  # "bytes 100-999/1000"
                tamanho = r.headers.get("Content-Length")
                total = int(faixa.rsplit("/", 1)[1]) if faixa else (int(tamanho) if tamanho else None)
                while bloco := r.read(1 << 20):
                    f.write(bloco)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code < 500:
                raise
            print("  conexão caiu, continuando:", e, file=sys.stderr)
            continue
        if total is None:  # sem tamanho declarado, não dá para conferir
            break
    if total is not None and tmp.stat().st_size != total:
        raise RuntimeError(f"download incompleto de {url}: {tmp.stat().st_size} de {total} bytes")
    tmp.replace(destino)
    return destino


def ler_csv_zip(caminho, sufixo="_BRASIL.csv"):
    with zipfile.ZipFile(caminho) as z:
        nome = next(n for n in z.namelist() if n.endswith(sufixo))
        with z.open(nome) as f:
            yield from csv.DictReader(io.TextIOWrapper(f, encoding="latin1"), delimiter=";")


# ---------- nenhum CPF sai daqui ----------

# CPF inteiro ou já mascarado ("CPF XXX.111.222-XX", "CPF: 12345678900", "NOME 123.456.789-00", "123.456.789¿00")
CPF_RX = re.compile(r"\(?\s*CPF\s*(?:n[º°o.]*)?\s*[:.]?\s*[\dXx*]{3}\W?[\dXx*]{3}\W?[\dXx*]{3}\W?[\dXx*]{2}(?:\s*\))?"
                    r"|(?<![\d./])\d{3}\.\d{3}\.\d{3}\W\d{2}(?!\d)|(?<![\d./])[Xx*]{3}\.\d{3}\.\d{3}-[Xx*]{2}")


def esconder_cpf(texto):
    """O site não mostra CPF de ninguém, nem o pedaço que o órgão deixa aparecer."""
    return CPF_RX.sub("(CPF omitido)", texto)


def gravar_json(caminho, dados, indent=None):
    """Todo arquivo que vai para o site ou para o repositório passa por aqui: qualquer coisa com cara de CPF sai,
    venha do nome de um fornecedor, de um trecho de decisão ou de onde for."""
    texto = esconder_cpf(json.dumps(dados, ensure_ascii=False, indent=indent,
                                    separators=None if indent is not None else (",", ":")))
    if caminho.suffix == ".gz":  # mtime=0: mesmo conteúdo, mesmo arquivo (o git não vê mudança à toa)
        caminho.write_bytes(gzip.compress(texto.encode(), mtime=0))
    else:
        caminho.write_text(texto, "utf-8", newline="\n")


def so_digitos(s):
    return re.sub(r"\D", "", s or "")


def norm_nome(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z ]", " ", s.upper())).strip()


def data_iso(s):
    s = (s or "").strip()[:10]
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def valido(v):
    return v not in ("", "-1", "-3", "-4", "#NULO", "#NE", "#NULO#")


# ---------- TSE: candidaturas ----------

# ids que o DivulgaCand usa na URL da ficha do candidato
ELEICAO_DIVULGA = {2018: "2022802018", 2020: "2030402020", 2022: "2040602022", 2024: "2045202024", 2026: "20322002026"}
REGIAO = {**dict.fromkeys("AC AM AP PA RO RR TO".split(), "NORTE"),
          **dict.fromkeys("AL BA CE MA PB PE PI RN SE".split(), "NORDESTE"),
          **dict.fromkeys("DF GO MS MT".split(), "CENTROOESTE"),
          **dict.fromkeys("ES MG RJ SP".split(), "SUDESTE"),
          **dict.fromkeys("PR RS SC".split(), "SUL")}


SENADO = "https://legis.senado.leg.br/dadosabertos/senador/lista/atual"
CAMARA = "https://dadosabertos.camara.leg.br/api/v2/deputados"
CARGOS_SENADO = ("SENADOR", "1º SUPLENTE", "2º SUPLENTE")


def em_exercicio():
    """Quem está hoje no Senado (UF + nome completo) e na Câmara (CPF), pelas listas oficiais das duas casas.
    Pega suplente que assumiu e tira quem morreu, renunciou ou foi cassado."""
    destino = CACHE / "em_exercicio.json"
    if destino.exists() and dt.date.fromtimestamp(destino.stat().st_mtime) == dt.date.today():
        return json.loads(destino.read_text("utf-8"))

    def pegar(url):
        req = urllib.request.Request(url, headers={**UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)

    senado = [x["IdentificacaoParlamentar"] for x in pegar(SENADO)["ListaParlamentarEmExercicio"]["Parlamentares"]["Parlamentar"]]
    ids = [d["id"] for d in pegar(f"{CAMARA}?itens=1000")["dados"]]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(4) as pool:
        cpfs = list(pool.map(lambda i: pegar(f"{CAMARA}/{i}")["dados"]["cpf"], ids))
    dados = {"senado": [[x["UfParlamentar"], norm_nome(x["NomeCompletoParlamentar"])] for x in senado],
             "camara": [so_digitos(c).zfill(11) for c in cpfs if c]}
    if len(dados["senado"]) < 70 or len(dados["camara"]) < 450:
        raise RuntimeError(f"lista de parlamentares incompleta: {len(dados['senado'])} / {len(dados['camara'])}")
    CACHE.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(dados), "utf-8")
    return dados


def marcar_no_cargo(pessoas):
    """c["nc"] = está no cargo hoje. Senado e Câmara pela lista oficial; o resto, eleito no mandato atual:
    2024 (prefeito, vice, vereador) e 2022 (presidente, governador, deputado estadual/distrital)."""
    ex = em_exercicio()
    senado, camara = {tuple(x) for x in ex["senado"]}, set(ex["camara"])
    for cs in pessoas.values():
        senado_marcado = False
        for c in sorted(cs, key=lambda c: c["ano"], reverse=True):  # uma vaga só por pessoa: a candidatura mais nova
            if c["cargo"] in CARGOS_SENADO:
                c["nc"] = (not senado_marcado and c["ano"] in (2018, 2022)
                           and (c["uf"], norm_nome(c["nome"])) in senado)
                senado_marcado = senado_marcado or c["nc"]
            elif c["cargo"] == "DEPUTADO FEDERAL":
                c["nc"] = c["ano"] == 2022 and bool(c["cpf"]) and c["cpf"] in camara
            else:
                c["nc"] = c["sit"] == "Eleito" and (c["ano"] == 2024 or c["ano"] == 2022)


def linha_todos(c, verificavel):
    """Candidatura no formato das listas completas do site (todos/*.json)."""
    return [c["sq"], c["urna"], c["nome"], c["cargo"], c["uf"], c["ue"], c["sg_ue"], c["partido"], c["sit"],
            int(verificavel), c["ano"], int(no_cargo(c)), c["bens"], c.get("bens_ant")]


def no_cargo(c):
    return c.get("nc", False)


def url_tse(c):
    macapa_2020 = c["ano"] == 2020 and c["uf"] == "AP" and norm_nome(c["ue"]) == "MACAPA"  # votou depois em 2020
    eleicao = "2032002020" if macapa_2020 else ELEICAO_DIVULGA[c["ano"]]
    return (f"https://divulgacandcontas.tse.jus.br/divulga/#/candidato/{REGIAO.get(c['uf'], 'BRASIL')}/"
            f"{c['uf']}/{eleicao}/{c['sq']}/{c['ano']}/{c['sg_ue']}")


SITUACAO = {
    "ELEITO": "Eleito", "ELEITO POR QP": "Eleito", "ELEITO POR MÉDIA": "Eleito",
    "SUPLENTE": "Suplente", "NÃO ELEITO": "Não eleito", "2º TURNO": "2º turno",
}


def carregar_candidaturas():
    """Retorna {sq: candidatura}. CPF de 2024 vem pelo título de eleitor de outras eleições."""
    cands = {}
    for ano in ANOS:
        for x in ler_csv_zip(baixar(f"{TSE}/consulta_cand/consulta_cand_{ano}.zip", f"cand_{ano}.zip")):
            sq = x["SQ_CANDIDATO"]
            turno = int(x["NR_TURNO"] or 1)
            if sq in cands and cands[sq]["turno"] >= turno:
                continue
            cands[sq] = {
                "sq": sq, "ano": ano, "turno": turno, "eleicao": x["CD_ELEICAO"], "sg_ue": x["SG_UE"],
                "nome": x["NM_CANDIDATO"].strip(), "urna": x["NM_URNA_CANDIDATO"].strip(),
                "cpf": so_digitos(x["NR_CPF_CANDIDATO"]).zfill(11) if valido(x["NR_CPF_CANDIDATO"]) else "",
                "titulo": x["NR_TITULO_ELEITORAL_CANDIDATO"] if valido(x["NR_TITULO_ELEITORAL_CANDIDATO"]) else "",
                "cargo": x["DS_CARGO"].strip().upper(), "uf": x["SG_UF"], "ue": x["NM_UE"].strip(),
                "partido": x["SG_PARTIDO"].strip(),
                "sit": SITUACAO.get(x["DS_SIT_TOT_TURNO"].strip(), "Sem resultado"),
            }
        for x in ler_csv_zip(baixar(f"{TSE}/consulta_cand_complementar/consulta_cand_complementar_{ano}.zip",
                                    f"compl_{ano}.zip")):
            c = cands.get(x["SQ_CANDIDATO"])
            if c:
                # 2024+ usa DS_SITUACAO_JULGAMENTO; 2020/2022 deixam ela vazia e usam o detalhe
                julg = x["DS_SITUACAO_JULGAMENTO"].strip()
                c["julg"] = julg if valido(julg) else x["DS_DETALHE_SITUACAO_CAND"].strip()
                c["cass"] = x["DS_SITUACAO_CASSACAO"].strip()
        # patrimônio declarado ao TSE: soma dos bens; -1 = não declarou nenhum bem
        bens = defaultdict(float)
        for x in ler_csv_zip(baixar(f"{TSE}/bem_candidato/bem_candidato_{ano}.zip", f"bens_{ano}.zip")):
            bens[x["SQ_CANDIDATO"]] += float(x["VR_BEM_CANDIDATO"].replace(",", ".") or 0)
        for c in cands.values():
            if c["ano"] == ano:
                c["bens"] = round(bens[c["sq"]]) if c["sq"] in bens else -1
    cpf_por_titulo = {c["titulo"]: c["cpf"] for c in cands.values() if c["cpf"] and c["titulo"]}
    for c in cands.values():
        if not c["cpf"]:
            c["cpf"] = cpf_por_titulo.get(c["titulo"], "")
    return cands


# ---------- TSE: candidaturas barradas ou cassadas ----------

# Motivos sobre conduta. Fica de fora o que é formalidade (documento faltando, partido
# invalidado) e a fraude à cota de gênero, que cassa a chapa inteira do partido de uma vez.
MOTIVOS_TSE = [
    (r"ficha limpa|infraconstitucional", "Candidatura barrada pela Lei da Ficha Limpa"),
    (r"abuso de poder", "Abuso de poder na eleição"),
    (r"compra de voto|captacao ilicita de sufragio", "Compra de voto"),
    (r"gasto ilicito|captacao ou gasto ilicito", "Dinheiro ilegal na campanha"),
    (r"conduta vedada", "Usou o cargo público a favor da campanha"),
    (r"uso indevido de meios", "Uso ilegal de rádio, TV ou internet na campanha"),
    (r"outras fraudes", "Fraude na eleição"),
]


def status_tse(c, cassacao):
    texto = (c.get("cass") if cassacao and valido(c.get("cass", "")) else c.get("julg", "")) or ""
    t = texto.upper()
    no_tse = f" (no TSE: {texto.capitalize()})"
    if "RECURSO" in t or "PENDENTE" in t or "AGUARDANDO" in t:
        return "recurso", "Ainda cabe recurso" + no_tse
    if t.startswith("INDEFERIDO"):
        return "final", "Candidatura negada pela Justiça Eleitoral" + no_tse
    if t.startswith("CASSADO"):
        return "final", "Cassado pela Justiça Eleitoral" + no_tse
    if t.startswith(("DEFERIDO", "NÃO CASSADO", "LEVANTADA")):
        return "revertido", "No fim a candidatura foi liberada: a acusação não se manteve" + no_tse
    return "outro", texto.capitalize() or "Sem informação de julgamento"


def casos_tse(cands):
    casos = defaultdict(list)
    for ano in ANOS:
        vistos = set()
        for x in ler_csv_zip(baixar(f"{TSE}/motivo_cassacao/motivo_cassacao_{ano}.zip", f"motivo_{ano}.zip")):
            c = cands.get(x["SQ_CANDIDATO"])
            motivo = norm_nome(x["DS_MOTIVO"]).lower()
            tipo = next((t for padrao, t in MOTIVOS_TSE if re.search(padrao, motivo)), None)
            if not c or not tipo or (c["sq"], tipo) in vistos:
                continue
            vistos.add((c["sq"], tipo))
            cassacao = "cassa" in x["DS_TP_MOTIVO"].lower()
            st, st_txt = status_tse(c, cassacao)
            proc = x.get("NR_PROCESSO", "") if valido(x.get("NR_PROCESSO", "")) else ""
            casos[c["sq"]].append({
                "f": "TSE", "t": tipo, "s": st, "st": st_txt,
                "d": "", "ano": ano, "o": "Justiça Eleitoral",
                "pr": proc,
                "x": f"{'Cassação' if cassacao else 'Pedido de candidatura'} em {ano} "
                     f"({c['cargo'].title()}, {c['ue'].title()}). Motivo registrado no TSE: {x['DS_MOTIVO'].strip()}",
                "l": [["Prova: a candidatura no TSE", url_tse(c)]],
            })
    return casos


# ---------- TCU ----------

def tcu(endpoint):
    return json.loads(baixar(f"{TCU}/{endpoint}", f"tcu_{endpoint}.json", b"{}").read_text("utf-8"))


def casos_tcu():
    """{cpf: {processo: caso}}. Junta contas irregulares, lista eleitoral e inabilitação do mesmo processo."""
    casos = defaultdict(dict)
    valores = json.loads(VALORES.read_text("utf-8")) if VALORES.exists() else {}

    def caso(x):
        cpf = so_digitos(x["numeroRegistro"])
        if len(cpf) != 11:
            return None
        pr = x["numeroProcessoFormatado"]
        casos[cpf].setdefault(pr, {
            "f": "TCU", "t": "", "s": "final",
            "st": f"Sem mais recurso desde {x.get('dataTransitoEmJulgado', '?')}",
            "d": data_iso(x.get("dataTransitoEmJulgado")), "o": "Tribunal de Contas da União",
            "pr": pr, "ac": x.get("numeroAcordaoFormatado", ""), "san": [],
            "x": f"Cuidava de dinheiro público federal em {(x.get('municipio') or '').title()}/{x.get('uf') or ''}.",
            "l": [["Prova: a decisão do TCU", x["linkDeliberacoesProcesso"]], ["Prova: o processo no TCU", x["linkAcompanhamentoProcesso"]]],
        })
        somar_valor(casos[cpf][pr], x)
        return casos[cpf][pr]

    def somar_valor(c, x):
        """Débito e multa tirados do texto do acórdão (valores_tcu.py), sem contar o mesmo acórdão duas vezes."""
        ac = x.get("numeroAcordaoFormatado")
        if not ac or not valores.get(ac) or ac in c.setdefault("_acs", set()):  # {} = acórdão não achado no CSV
            return
        c["_acs"].add(ac)
        c["vx"] = True  # acórdão lido; sem "deb"/"mul" quer dizer que não houve débito nem multa para a pessoa
        v = valores[ac].get(norm_nome(x["nome"]))
        if v:
            c["deb"] = round(c.get("deb", 0) + v["deb"], 2)
            c["mul"] = round(c.get("mul", 0) + v["mul"], 2)
            c["sol"] = c.get("sol", False) or v["sol"]
            c["tr"] = v["tr"]

    for x in tcu("responsaveis-contas-irregulares"):
        if c := caso(x):
            c["t"] = "Contas reprovadas pelo TCU"
    for x in tcu("responsaveis-fins-eleitorais"):
        if c := caso(x):
            c["t"] = c["t"] or "Contas reprovadas pelo TCU"
            c["san"].append(f"Está na lista de quem não pode se candidatar, enviada pelo TCU ao TSE, até {x['dataFinalFinsEleitorais']}")
    for x in tcu("responsaveis-inabilitados"):
        if c := caso(x):
            c["t"] = c["t"] or "Proibido pelo TCU de ocupar cargo de confiança"
            c["san"].append(f"Proibido de ocupar cargo de confiança no governo federal até {x.get('dataFinalSancao', '?')}")
    for por_processo in casos.values():
        for c in por_processo.values():
            c.pop("_acs", None)
            # o título diz o que aconteceu, em português simples
            if c.get("deb"):
                c["t"] = "Dinheiro público sumiu: condenado pelo TCU a pagar de volta"
            elif c.get("mul") and c["t"] == "Contas reprovadas pelo TCU":
                c["t"] = "Multado pelo TCU por contas reprovadas"
    return casos


# ---------- CGU: CEIS (improbidade e outras sanções) e CEAF (expulsões) ----------

def zip_cgu(base):
    hoje = dt.datetime.now(dt.timezone(dt.timedelta(hours=-3))).date()
    for atras in range(8):
        d = (hoje - dt.timedelta(days=atras)).strftime("%Y%m%d")
        try:
            return baixar(f"{CGU}/{base}/{d}_{base.upper()}.zip", f"{base}.zip"), d
        except urllib.error.HTTPError:
            continue
    raise RuntimeError(f"CGU sem arquivo {base} nos últimos 8 dias")


SANCAO_SIMPLES = {
    "demissão": "Demitido do serviço público federal",
    "cassação de aposentadoria": "Perdeu a aposentadoria de servidor público por punição",
    "destituição": "Destituído de cargo no governo federal",
    "destituição de cargo em comissão": "Destituído de cargo de confiança no governo federal",
    "destituição de função comissionada": "Destituído de função de confiança no governo federal",
    "impedimento/proibição de contratar com prazo determinado": "Proibido de fechar contrato com o governo",
    "proibição de contratar com o poder público": "Proibido de fechar contrato com o governo",
    "declaração de inidoneidade sem prazo determinado": "Declarado inidôneo (não pode contratar com o governo)",
    "declaração de inidoneidade com prazo determinado": "Declarado inidôneo (não pode contratar com o governo)",
    "suspensão": "Suspenso de contratar com o governo",
}


def linha_cgu(x, fonte):
    fund = x["FUNDAMENTAÇÃO LEGAL"].strip()
    improb = "8429" in fund
    transito = data_iso(x["DATA DO TRÂNSITO EM JULGADO"])
    categoria = x["CATEGORIA DA SANÇÃO"].strip()
    tipo = ("Condenado na Justiça por improbidade (mau uso do cargo ou do dinheiro público)" if improb
            else SANCAO_SIMPLES.get(categoria.lower(), f"{categoria} ({'servidor público' if fonte == 'CEAF' else 'sanção'})"))
    return {
        "f": fonte, "t": tipo,
        "s": "final" if transito else "sancao",
        "st": f"Sem mais recurso desde {transito[8:]}/{transito[5:7]}/{transito[:4]}" if transito
              else f"Punição aplicada em {x['DATA INÍCIO SANÇÃO'].strip()} (o órgão não informou se ainda cabe recurso)",
        "d": transito or data_iso(x["DATA INÍCIO SANÇÃO"]),
        "o": x["ÓRGÃO SANCIONADOR"].strip().title(), "pr": x["NÚMERO DO PROCESSO"].strip(),
        "san": [f"{x['CATEGORIA DA SANÇÃO'].strip()}"
                + (f" até {x['DATA FINAL SANÇÃO'].strip()}" if x["DATA FINAL SANÇÃO"].strip() else "")],
        "x": fund[:400] + ("…" if len(fund) > 400 else ""),
        "l": [["Prova: a punição no Portal da Transparência",
               f"https://portaldatransparencia.gov.br/sancoes/consulta/{x['CÓDIGO DA SANÇÃO']}"]],
    }


def casos_cgu(cands):
    """CEIS tem CPF completo. CEAF mascara o CPF (***.123.456-**): casa por nome + 6 dígitos do meio."""
    por_cpf = defaultdict(dict)
    datas = {}
    for base in ("ceis", "ceaf"):
        caminho, datas[base] = zip_cgu(base)
        with zipfile.ZipFile(caminho) as z, z.open(z.namelist()[0]) as f:
            linhas = list(csv.DictReader(io.TextIOWrapper(f, encoding="latin1"), delimiter=";"))
        if base == "ceis":
            for x in linhas:
                cpf = so_digitos(x["CPF OU CNPJ DO SANCIONADO"])
                if x["TIPO DE PESSOA"] == "F" and len(cpf) == 11:
                    juntar(por_cpf[cpf], linha_cgu(x, "CEIS"))
        else:
            chave = {(norm_nome(c["nome"]), c["cpf"][3:9]): c["cpf"] for c in cands.values() if c["cpf"]}
            for x in linhas:
                meio = so_digitos(x["CPF OU CNPJ DO SANCIONADO"])
                cpf = chave.get((norm_nome(x["NOME DO SANCIONADO"]), meio)) if len(meio) == 6 else None
                if cpf:
                    juntar(por_cpf[cpf], linha_cgu(x, "CEAF"))
    return por_cpf, datas


def juntar(casos_da_pessoa, novo):
    """Mesma sanção aparece em várias linhas (uma por tipo de pena): junta pelo processo."""
    k = so_digitos(novo["pr"]) or novo["t"] + novo["d"]
    if k in casos_da_pessoa:
        velho = casos_da_pessoa[k]
        velho["san"] = sorted(set(velho["san"] + novo["san"]))
        if novo["s"] == "final":
            velho.update(s="final", st=novo["st"], d=novo["d"])
        if "improbidade" in novo["t"]:
            velho["t"] = novo["t"]
    else:
        casos_da_pessoa[k] = novo


# ---------- casos enviados pela comunidade (com fonte obrigatória) ----------

def casos_manuais():
    if not MANUAIS.exists():
        return {}
    out = defaultdict(list)
    for c in json.loads(MANUAIS.read_text("utf-8")):
        falta = [k for k in ("sq_candidato", "tipo", "status", "orgao", "processo", "fonte_url", "resumo") if not c.get(k)]
        if falta or c["status"] not in ("final", "recurso", "revertido", "andamento"):
            raise SystemExit(f"caso manual inválido ({falta or c['status']}): {c}")
        out[str(c["sq_candidato"])].append({
            "f": "Comunidade", "t": c["tipo"], "s": c["status"], "st": c.get("status_texto", ""),
            "d": c.get("data", ""), "o": c["orgao"], "pr": c["processo"], "x": c["resumo"],
            "l": [["Fonte", c["fonte_url"]]],
        })
    return out


# ---------- montagem ----------

def montar():
    cands = carregar_candidaturas()
    tse = casos_tse(cands)
    tcu_ = casos_tcu()
    cgu, datas_cgu = casos_cgu(cands)
    manuais = casos_manuais()

    # agrupa candidaturas por pessoa: CPF quando há; senão a própria candidatura
    pessoas = defaultdict(list)
    for c in cands.values():
        pessoas[c["cpf"] or "sq" + c["sq"]].append(c)
    marcar_no_cargo(pessoas)

    saida, totais, sem_registro = [], defaultdict(int), defaultdict(list)
    for chave, cs in pessoas.items():
        cs.sort(key=lambda c: c["ano"], reverse=True)
        verificavel = not chave.startswith("sq")
        for i, c in enumerate(cs):  # declaração anterior de bens, para mostrar quanto o patrimônio cresceu
            c["bens_ant"] = next(([o["ano"], o["bens"]] for o in cs[i + 1:] if o["bens"] >= 0), None)
        for c in cs:
            if verificavel:
                totais[f"{c['ano']}|{c['partido']}|{c['cargo']}|{c['uf']}|{c['sit']}"] += 1
        casos = []
        if verificavel:
            casos += list(tcu_.get(chave, {}).values()) + list(cgu.get(chave, {}).values())
        for c in cs:
            casos += tse.get(c["sq"], []) + manuais.get(c["sq"], [])
        if not casos:
            for c in cs:  # entra na lista completa da eleição, carregada sob demanda pelo site
                arquivo = str(c["ano"]) if c["ano"] in ANOS_GERAIS else f"{c['ano']}-{c['uf']}"
                linha = linha_todos(c, verificavel)
                sem_registro[arquivo].append(linha)
                if no_cargo(c):
                    sem_registro["no-cargo"].append(linha)
            continue
        for k in casos:
            k["ano"] = k.get("ano") or (int(k["d"][:4]) if k["d"] else None)
        casos.sort(key=lambda k: (k["d"] or str(k["ano"] or "")), reverse=True)
        atual = cs[0]
        saida.append({
            "i": atual["sq"], "n": atual["nome"].title(), "u": atual["urna"].title(), "v": verificavel,
            "c": [[c["ano"], c["cargo"], c["uf"], c["ue"].title(), c["partido"], c["sit"], url_tse(c), c["sq"],
                   int(no_cargo(c)), c["bens"]]
                  for c in cs],
            "k": casos,
        })

    fontes = [
        {"nome": "TSE: candidaturas 2018, 2020, 2022, 2024 e 2026", "url": "https://dadosabertos.tse.jus.br/"},
        {"nome": "TSE: motivos de indeferimento e cassação", "url": "https://dadosabertos.tse.jus.br/"},
        {"nome": "TSE: bens declarados pelos candidatos", "url": "https://dadosabertos.tse.jus.br/"},
        {"nome": "Portal da Transparência: emendas parlamentares por favorecido",
         "url": "https://portaldatransparencia.gov.br/download-de-dados/emendas-parlamentares"},
        {"nome": "TSE: receitas de campanha dos candidatos (prestação de contas)", "url": "https://dadosabertos.tse.jus.br/"},
        {"nome": "PNCP: preço pago em compras públicas (parquinho, remédio, combustível, ar-condicionado)",
         "url": "https://pncp.gov.br/"},
        {"nome": "CGU: acordos de leniência", "url": "https://portaldatransparencia.gov.br/download-de-dados/acordos-leniencia"},
        {"nome": "Câmara dos Deputados: cota parlamentar (notas reembolsadas)",
         "url": "https://www.camara.leg.br/transparencia/gastos-parlamentares"},
        {"nome": "TCU: contas julgadas irregulares, lista eleitoral e inabilitados",
         "url": "https://sites.tcu.gov.br/dados-abertos/webservices-tcu/"},
        {"nome": "TCU: texto completo dos acórdãos (valores de débito e multa)",
         "url": "https://sites.tcu.gov.br/dados-abertos/jurisprudencia/"},
        {"nome": f"CGU: CEIS (arquivo de {datas_cgu['ceis']})", "url": "https://portaldatransparencia.gov.br/download-de-dados/ceis"},
        {"nome": f"CGU: CEAF (arquivo de {datas_cgu['ceaf']})", "url": "https://portaldatransparencia.gov.br/download-de-dados/ceaf"},
    ]
    dados = {
        "gerado": dt.datetime.now(dt.timezone(dt.timedelta(hours=-3))).strftime("%d/%m/%Y %H:%M"),
        "fontes": fontes, "totais": totais, "p": saida,
    }
    SAIDA.parent.mkdir(exist_ok=True)
    gravar_json(SAIDA, dados)
    pasta = SAIDA.parent / "todos"
    shutil.rmtree(pasta, ignore_errors=True)
    pasta.mkdir()
    for arquivo, linhas in sem_registro.items():
        gravar_json(pasta / f"{arquivo}.json", linhas)
    print(f"{sum(map(len, sem_registro.values()))} candidaturas sem registro em {len(sem_registro)} arquivos",
          file=sys.stderr)
    carregar_socios()
    montar_prefeituras(cands, saida)
    montar_precos(cands)
    montar_emendas(pessoas, saida)
    montar_cota(pessoas)
    campanha = montar_campanha(pessoas)
    montar_panorama(campanha, montar_empresas(pessoas))
    print(f"{len(saida)} pessoas com registro, {sum(len(p['k']) for p in saida)} casos, "
          f"{SAIDA.stat().st_size / 1e6:.1f} MB "
          f"({len(gzip.compress(SAIDA.read_bytes())) / 1e6:.1f} MB gzip)", file=sys.stderr)


# ---------- empresas em que o próprio político é sócio (Receita Federal, lidas pelo empresas.py) ----------

EMPRESAS_POLITICOS = RAIZ / "dados" / "empresas_politicos.json.gz"
SOCIOS = {}  # começo do CNPJ (8 dígitos, vale para matriz e filiais) -> {número da candidatura: sócio desde AAAA-MM-DD}
# pessoa -> (onde, fornecedor, cnpj) -> [valor, primeira data, última data, prova]
PROPRIA = defaultdict(lambda: defaultdict(lambda: [0.0, "9999-99-99", "0000-00-00", ""]))


def tem_dono(natureza):
    """Só empresa privada entra no cruzamento do dinheiro (natureza jurídica 2xxx). Ficam de fora o que não tem dono:
    órgão público (1xxx), estatal (2011, 2038), cooperativa (2143) e associação, fundação, Santa Casa (3xxx).
    Presidente de hospital filantrópico não embolsa a verba que o hospital recebe."""
    return natureza[:1] == "2" and natureza not in ("2011", "2038", "2143")


def carregar_socios():
    if EMPRESAS_POLITICOS.exists():
        for sqs, empresas in json.loads(gzip.decompress(EMPRESAS_POLITICOS.read_bytes()))["pessoas"]:
            for cnpj, _, _, desde, natureza in empresas:
                if tem_dono(natureza):
                    SOCIOS.setdefault(cnpj[:8], {}).update((sq, desde or "9999") for sq in sqs)


def pago_a_propria(chave, cs, cnpj, data, fornecedor, valor, onde, prova=""):
    """Anota dinheiro público pago a empresa em que o próprio político JÁ era sócio na data do pagamento.
    A Receita só mostra o quadro de sócios de hoje: quem saiu antes não aparece (fica de fora, não acusa à toa)."""
    desde = SOCIOS.get(cnpj[:8]) if len(cnpj) == 14 and data else None
    if desde and any(desde.get(c["sq"], "9999") <= data for c in cs):
        p = PROPRIA[chave][(onde, fornecedor, cnpj)]
        p[0] += valor
        p[1], p[2] = min(p[1], data), max(p[2], data)
        p[3] = p[3] or prova
        return p


def montar_empresas(pessoas):
    """Empresas de cada político e o dinheiro público que foi para elas. Em pedaços (pelos 2 últimos dígitos do número
    da candidatura) para o site baixar só o da ficha aberta; mais a lista de quem pagou a própria empresa."""
    if not EMPRESAS_POLITICOS.exists():
        return None
    dados = json.loads(gzip.decompress(EMPRESAS_POLITICOS.read_bytes()))
    chave_de = {c["sq"]: chave for chave, cs in pessoas.items() for c in cs}
    pedacos, lista = defaultdict(dict), []
    for sqs, empresas in dados["pessoas"]:
        chave = chave_de.get(sqs[0])
        if not chave:
            continue
        pag = sorted(([onde, forn.title(), cnpj, round(v), de, ate, prova]
                      for (onde, forn, cnpj), (v, de, ate, prova) in PROPRIA[chave].items()), key=lambda p: -p[3])
        item = {"e": [e[:4] for e in empresas], "p": pag}
        for sq in sqs:
            pedacos[sq[-2:]][sq] = item
        if pag:
            c = max(pessoas[chave], key=lambda c: c["ano"])
            lista.append([linha_todos(c, not chave.startswith("sq")), sum(p[3] for p in pag),
                          sorted({p[0].split("|")[0] for p in pag})])
    pasta = SAIDA.parent / "empresas"
    shutil.rmtree(pasta, ignore_errors=True)
    pasta.mkdir()
    for pedaco, d in pedacos.items():
        gravar_json(pasta / f"{pedaco}.json", d)
    lista.sort(key=lambda x: -x[1])
    gravar_json(pasta / "lista.json", {"mes": dados["mes"], "lista": lista})
    total = sum(x[1] for x in lista)
    print(f"empresas: {len(dados['pessoas'])} políticos sócios; R$ {total / 1e6:.1f} mi de dinheiro público "
          f"para empresas de {len(lista)} deles", file=sys.stderr)
    return {"total": total, "pessoas": len(lista), "mes": dados["mes"]}


# ---------- emendas parlamentares: quem mandou, para onde e quem recebeu ----------

EMENDAS = "https://portaldatransparencia.gov.br/download-de-dados/emendas-parlamentares/UNICO"
CARGOS_CONGRESSO = ("DEPUTADO FEDERAL", "SENADOR", "1º SUPLENTE", "2º SUPLENTE")
VALE_PARA_FEDERAL = ("Em todos os Poderes da Esfera do órgão sancionador", "Na Esfera e no Poder do órgão sancionador")


def punicao_simples(categoria):
    c = categoria.lower()
    if "inidoneidade" in c:
        return "declarada inidônea (proibida de fazer negócio com qualquer governo)"
    if "suspens" in c:
        return "suspensa de vender para o governo"
    return "proibida de vender para o governo federal"


def empresas_punidas():
    """CNPJ -> [(início AAAAMM, fim AAAAMM, descrição, código)] das punições do CEIS que valem para dinheiro federal:
    as de todas as esferas e as dadas por órgão federal para todo o governo federal."""
    caminho, _ = zip_cgu("ceis")
    out = defaultdict(list)
    with zipfile.ZipFile(caminho) as z, z.open(z.namelist()[0]) as f:
        for x in csv.DictReader(io.TextIOWrapper(f, encoding="latin1"), delimiter=";"):
            # inidoneidade vale para qualquer governo (Lei 14.133, art. 156 §5º); impedimento e suspensão só valem no
            # governo que puniu, então só contam se o órgão for federal (o cadastro às vezes marca "todas as esferas")
            abr, federal = x["ABRAGÊNCIA DA SANÇÃO"], x["ESFERA ÓRGÃO SANCIONADOR"].strip().upper() == "FEDERAL"
            inidonea = "inidoneidade" in x["CATEGORIA DA SANÇÃO"].lower()
            vale = (inidonea and (federal or abr == "Todas as Esferas em todos os Poderes")) or (
                federal and (abr in VALE_PARA_FEDERAL or abr == "Todas as Esferas em todos os Poderes"))
            ini, fim = data_iso(x["DATA INÍCIO SANÇÃO"]), data_iso(x["DATA FINAL SANÇÃO"])
            if x["TIPO DE PESSOA"] != "J" or not vale or not ini:
                continue
            out[so_digitos(x["CPF OU CNPJ DO SANCIONADO"]).zfill(14)].append((
                ini[:7].replace("-", ""), fim[:7].replace("-", "") if fim else "999999",
                f"{punicao_simples(x['CATEGORIA DA SANÇÃO'])}, por decisão de {x['ÓRGÃO SANCIONADOR'].strip().title()}",
                f"{ini[8:]}/{ini[5:7]}/{ini[:4]}" + (f" a {fim[8:]}/{fim[5:7]}/{fim[:4]}" if fim else " sem data para acabar"),
                x["CÓDIGO DA SANÇÃO"]))
    return out


def montar_emendas(pessoas, saida):
    """Pagamentos de emendas desde 2019 (Portal da Transparência, por favorecido): por parlamentar e por cidade,
    e pagamento a empresa que estava proibida de contratar com o governo federal no mês do pagamento."""
    caminho = baixar(EMENDAS, "emendas.zip")
    # o autor da emenda vem pelo nome parlamentar: liga ao nome de urna de quem disputou o Congresso em 2018/2022
    por_urna = defaultdict(set)
    for chave, cs in pessoas.items():
        for c in cs:
            if c["cargo"] in CARGOS_CONGRESSO and c["ano"] in (2018, 2022) and c["sit"] in ("Eleito", "Suplente"):
                por_urna[norm_nome(c["urna"])].add(chave)
    punidas = empresas_punidas()
    autores, cidades, alertas, de_socio = {}, {}, defaultdict(lambda: [0.0, "999999", "000000"]), []
    with zipfile.ZipFile(caminho) as z, z.open("EmendasParlamentares_PorFavorecido.csv") as f:
        for x in csv.DictReader(io.TextIOWrapper(f, encoding="latin1"), delimiter=";"):
            mes, v = x["Ano/Mês"], float(x["Valor Recebido"].replace(".", "").replace(",", ".") or 0)
            if mes < "201901" or v <= 0:
                continue
            cod, fav = x["Código do Autor da Emenda"], x["Favorecido"].strip()
            cidade = f"{x['UF Favorecido']}|{norm_nome(x['Município Favorecido'])}"
            a = autores.setdefault(cod, {"n": x["Nome do Autor da Emenda"].strip(), "t": 0.0, "pix": 0.0,
                                         "de": mes, "ate": mes, "cid": defaultdict(float), "fav": defaultdict(float)})
            a["t"] += v
            a["de"], a["ate"] = min(a["de"], mes), max(a["ate"], mes)
            if "Especiais" in x["Tipo de Emenda"]:  # "emenda Pix": vai para o caixa da prefeitura sem dizer o que fazer
                a["pix"] += v
            a["cid"][f"{x['Município Favorecido'].title()}/{x['UF Favorecido']}"] += v
            a["fav"][fav] += v
            c = cidades.setdefault(cidade, {"t": 0.0, "aut": defaultdict(float), "fav": defaultdict(float)})
            c["t"] += v
            c["aut"][cod] += v
            c["fav"][fav] += v
            cnpj = so_digitos(x["Código do Favorecido"])
            if cnpj[:8] in SOCIOS and len(cnpj) == 14:  # verba para empresa ou entidade de político: confere o autor depois
                de_socio.append((cod, cnpj, f"{mes[:4]}-{mes[4:]}-01", fav, v))
            for ini, fim, desc, periodo, cod_san in (punidas.get(cnpj, ()) if len(cnpj) == 14 else ()):
                if ini < mes <= fim:  # pago depois do mês em que a punição começou
                    al = alertas[(cod, cnpj, cod_san, fav, desc, periodo, cidade)]
                    al[0] += v
                    al[1], al[2] = min(al[1], mes), max(al[2], mes)
                    break

    def topo(d, n):
        return [[k, round(v)] for k, v in sorted(d.items(), key=lambda kv: -kv[1])[:n]]

    def mes_br(m):
        return f"{m[4:]}/{m[:4]}"

    sq_de = {}
    for cod, a in autores.items():
        chaves = por_urna.get(norm_nome(a["n"]), set())
        a["chave"] = next(iter(chaves)) if len(chaves) == 1 else None  # homônimo: não liga
        a["sqs"] = [c["sq"] for c in pessoas[a["chave"]]] if a["chave"] else []
        for sq in a["sqs"]:
            sq_de[sq] = cod
    # verba para empresa ou entidade de político: do próprio autor, ou de outro político que já foi eleito
    chave_de = {c["sq"]: chave for chave, cs in pessoas.items() for c in cs}
    for cod, cnpj, data, fav, v in de_socio:
        for dono in {chave_de[sq] for sq in SOCIOS[cnpj[:8]] if sq in chave_de}:
            if dono == autores[cod]["chave"]:
                pago_a_propria(dono, pessoas[dono], cnpj, data, fav, v, "emenda")
            elif any(c["sit"] == "Eleito" for c in pessoas[dono]):
                pago_a_propria(dono, pessoas[dono], cnpj, data, fav, v, f"emenda_de|{autores[cod]['n']}")
    lista_alertas = [[fav, cnpj, round(v), f"{mes_br(de)} a {mes_br(ate)}" if de != ate else mes_br(de), desc, periodo,
                      cod, cidade, f"https://portaldatransparencia.gov.br/sancoes/consulta/{cod_san}"]
                     for (cod, cnpj, cod_san, fav, desc, periodo, cidade), (v, de, ate) in alertas.items()]
    lista_alertas.sort(key=lambda a: -a[2])
    dados = {
        "autores": {cod: {"n": a["n"], "t": round(a["t"]), "pix": round(a["pix"]), "de": mes_br(a["de"]),
                          "ate": mes_br(a["ate"]), "cid": topo(a["cid"], 10), "fav": topo(a["fav"], 8), "sqs": a["sqs"]}
                    for cod, a in autores.items()},
        "cidades": {k: {"t": round(c["t"]), "aut": topo(c["aut"], 10), "fav": topo(c["fav"], 8)} for k, c in cidades.items()},
        "alertas": lista_alertas,
        "sq": sq_de,
    }
    gravar_json(SAIDA.parent / "emendas.json", dados)
    ligados = sum(1 for a in autores.values() if a["sqs"])
    print(f"emendas: {len(autores)} autores ({ligados} ligados a candidatos), {len(cidades)} cidades, "
          f"{len(lista_alertas)} pagamentos a empresa punida", file=sys.stderr)


# ---------- cota parlamentar da Câmara: o que cada deputado gastou e pediu reembolso ----------

COTA = "https://www.camara.leg.br/cotas/Ano-{}.csv.zip"
ANOS_COTA = range(2019, dt.date.today().year + 1)


def montar_cota(pessoas):
    """Notas da cota parlamentar (CEAP) por CPF do deputado: total, categorias, fornecedores e nota paga a empresa
    que estava proibida de contratar com o governo federal. O CPF só serve para ligar; não vai para o site."""
    por_cpf = {chave: cs for chave, cs in pessoas.items() if not chave.startswith("sq")}
    punidas = empresas_punidas()
    linhas = []
    for ano in ANOS_COTA:
        nome = f"cota_{ano}.zip"
        if ano == dt.date.today().year:
            (CACHE / nome).unlink(missing_ok=True)  # o ano corrente ganha nota nova todo dia
        with zipfile.ZipFile(baixar(COTA.format(ano), nome)) as z, z.open(z.namelist()[0]) as f:
            linhas += [(ano, x) for x in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"), delimiter=";")
                       if x["ideCadastro"]]
    # o arquivo da Câmara vem com o CPF em branco: o CPF sai da API pelo código do deputado (guardado em cache)
    cache_cpf = CACHE / "camara_cpf.json"
    cpf_de = json.loads(cache_cpf.read_text("utf-8")) if cache_cpf.exists() else {}
    faltam = sorted({x["ideCadastro"] for _, x in linhas} - set(cpf_de))
    if faltam:
        from concurrent.futures import ThreadPoolExecutor

        def cpf_do(i):
            req = urllib.request.Request(f"{CAMARA}/{i}", headers={**UA, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return i, so_digitos(json.load(r)["dados"].get("cpf") or "").zfill(11)
            except (urllib.error.URLError, TimeoutError, KeyError):
                return i, None
        with ThreadPoolExecutor(4) as pool:
            cpf_de.update((i, c) for i, c in pool.map(cpf_do, faltam) if c)
        cache_cpf.write_text(json.dumps(cpf_de), "utf-8")
    dep = {}
    for ano, x in linhas:
        cpf = cpf_de.get(x["ideCadastro"], "")
        if cpf not in por_cpf:
            continue
        v = float(x["vlrLiquido"] or 0)
        d = dep.setdefault(cpf, {"t": 0.0, "n": 0, "de": ano, "ate": ano, "cat": defaultdict(float),
                                 "forn": defaultdict(float), "al": defaultdict(lambda: [0.0, "999999", "000000", ""])})
        d["t"] += v
        d["n"] += 1
        d["de"], d["ate"] = min(d["de"], ano), max(d["ate"], ano)
        d["cat"][x["txtDescricao"].strip().capitalize()] += v
        forn = x["txtFornecedor"].strip()
        d["forn"][forn] += v
        cnpj, mes = so_digitos(x["txtCNPJCPF"]), (x["datEmissao"] or "")[:7].replace("-", "")
        pago_a_propria(cpf, por_cpf[cpf], cnpj, (x["datEmissao"] or "")[:10], forn, v, "cota", x["urlDocumento"])
        for ini, fim, desc, periodo, cod_san in (punidas.get(cnpj, ()) if len(cnpj) == 14 and mes else ()):
            if ini < mes <= fim:
                al = d["al"][(forn, cnpj, desc, periodo, cod_san)]
                al[0] += v
                al[1], al[2] = min(al[1], mes), max(al[2], mes)
                al[3] = al[3] or x["urlDocumento"]
                break

    def topo(dd, n):
        return [[k, round(v)] for k, v in sorted(dd.items(), key=lambda kv: -kv[1])[:n]]

    def mes_br(m):
        return f"{m[4:]}/{m[:4]}"

    saida, por_sq = [], {}
    for cpf, d in dep.items():
        i = len(saida)
        alertas = [[forn, round(v), f"{mes_br(a)} a {mes_br(b)}" if a != b else mes_br(a), desc, periodo,
                    f"https://portaldatransparencia.gov.br/sancoes/consulta/{cod}", nota]
                   for (forn, cnpj, desc, periodo, cod), (v, a, b, nota) in d["al"].items()]
        saida.append({"t": round(d["t"]), "n": d["n"], "de": d["de"], "ate": d["ate"], "cat": topo(d["cat"], 6),
                      "forn": topo(d["forn"], 8), "al": sorted(alertas, key=lambda a: -a[1])})
        for c in por_cpf[cpf]:
            por_sq[c["sq"]] = i
    n_dep = len(saida)
    cota_senado(pessoas, punidas, saida, por_sq, topo, mes_br)
    gravar_json(SAIDA.parent / "cota.json", {"dep": saida, "sq": por_sq})
    print(f"cota: {n_dep} deputados e {len(saida) - n_dep} senadores, "
          f"{sum(len(d['al']) for d in saida)} notas pagas a empresa punida", file=sys.stderr)


CEAPS = "https://www.senado.leg.br/transparencia/LAI/verba/despesa_ceaps_{}.csv"


def cota_senado(pessoas, punidas, saida, por_sq, topo, mes_br):
    """Gastos do mandato dos senadores (CEAPS). O arquivo traz o nome parlamentar; a lista oficial do Senado dá o
    nome completo e a UF, que ligam à candidatura. Quem já saiu do Senado não está na lista e fica sem ligar."""
    req = urllib.request.Request(SENADO, headers={**UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        lista = [x["IdentificacaoParlamentar"] for x in json.load(r)["ListaParlamentarEmExercicio"]["Parlamentares"]["Parlamentar"]]
    nome_completo = {norm_nome(x["NomeParlamentar"]): (x["UfParlamentar"], norm_nome(x["NomeCompletoParlamentar"])) for x in lista}
    chave_de = {}
    for chave, cs in pessoas.items():
        for c in cs:
            if c["cargo"] in CARGOS_SENADO and c["ano"] in (2018, 2022):
                chave_de[(c["uf"], norm_nome(c["nome"]))] = chave
    sen = {}
    for ano in ANOS_COTA:
        nome = f"ceaps_{ano}.csv"
        if ano == dt.date.today().year:
            (CACHE / nome).unlink(missing_ok=True)
        with open(baixar(CEAPS.format(ano), nome), encoding="latin1", newline="") as f:
            next(f)  # primeira linha: "ULTIMA ATUALIZACAO"
            for x in csv.DictReader(f, delimiter=";"):
                chave = chave_de.get(nome_completo.get(norm_nome(x["SENADOR"]), ("", "")))
                if not chave:
                    continue
                v = float((x["VALOR_REEMBOLSADO"] or "0").replace(".", "").replace(",", "."))
                d = sen.setdefault(chave, {"t": 0.0, "n": 0, "de": ano, "ate": ano, "cat": defaultdict(float),
                                           "forn": defaultdict(float), "al": defaultdict(lambda: [0.0, "999999", "000000", ""])})
                d["t"] += v
                d["n"] += 1
                d["de"], d["ate"] = min(d["de"], ano), max(d["ate"], ano)
                d["cat"][x["TIPO_DESPESA"].split(",")[0].strip().capitalize()] += v
                forn = x["FORNECEDOR"].strip()
                d["forn"][forn] += v
                cnpj, data = so_digitos(x["CNPJ_CPF"]), data_iso(x["DATA"])
                mes = data[:7].replace("-", "")
                pago_a_propria(chave, pessoas[chave], cnpj, data, forn, v, "cota")
                for ini, fim, desc, periodo, cod_san in (punidas.get(cnpj, ()) if len(cnpj) == 14 and mes else ()):
                    if ini < mes <= fim:
                        al = d["al"][(forn, cnpj, desc, periodo, cod_san)]
                        al[0] += v
                        al[1], al[2] = min(al[1], mes), max(al[2], mes)
                        break
    for chave, d in sen.items():
        i = len(saida)
        alertas = [[forn, round(v), f"{mes_br(a)} a {mes_br(b)}" if a != b else mes_br(a), desc, periodo,
                    f"https://portaldatransparencia.gov.br/sancoes/consulta/{cod}", ""]
                   for (forn, cnpj, desc, periodo, cod), (v, a, b, _) in d["al"].items()]
        saida.append({"casa": "o Senado", "t": round(d["t"]), "n": d["n"], "de": d["de"], "ate": d["ate"],
                      "cat": topo(d["cat"], 6), "forn": topo(d["forn"], 8), "al": sorted(alertas, key=lambda a: -a[1])})
        for c in pessoas[chave]:
            por_sq[c["sq"]] = i


# ---------- dinheiro de campanha e panorama geral ----------

CONTAS = "https://cdn.tse.jus.br/estatistica/sead/odsele/prestacao_contas/prestacao_de_contas_eleitorais_candidatos_{}.zip"
ANO_CAMPANHA = 2026


def campanha_propria(pessoas, caminho):
    """Gasto de campanha pago com fundo eleitoral ou partidário (dinheiro público) a empresa do próprio candidato.
    O que foi pago com doação de pessoa ou empresa não é dinheiro público e fica de fora."""
    chave_de = {c["sq"]: chave for chave, cs in pessoas.items() for c in cs}
    achadas = {}  # número da despesa -> (pessoa, cnpj, fornecedor)
    for x in ler_csv_zip(caminho, f"despesas_contratadas_candidatos_{ANO_CAMPANHA}_BRASIL.csv"):
        cnpj, chave = so_digitos(x["NR_CPF_CNPJ_FORNECEDOR"]), chave_de.get(x["SQ_CANDIDATO"])
        if chave and cnpj[:8] in SOCIOS:
            achadas[x["SQ_DESPESA"]] = (chave, cnpj, x["NM_FORNECEDOR"].strip())
    for x in ler_csv_zip(caminho, f"despesas_pagas_candidatos_{ANO_CAMPANHA}_BRASIL.csv"):
        if (a := achadas.get(x["SQ_DESPESA"])) and "FUNDO" in x["DS_FONTE_DESPESA"].upper():
            chave, cnpj, forn = a
            pago_a_propria(chave, pessoas[chave], cnpj, data_iso(x["DT_PAGTO_DESPESA"]), forn,
                           float(x["VR_PAGTO_DESPESA"].replace(",", ".") or 0), "campanha")


def montar_campanha(pessoas):
    """Quanto cada candidato recebeu na campanha e quanto disso foi dinheiro público (fundo eleitoral e partidário)."""
    nome = f"contas_{ANO_CAMPANHA}.zip"
    (CACHE / nome).unlink(missing_ok=True)  # a prestação de contas é atualizada durante a campanha
    caminho = baixar(CONTAS.format(ANO_CAMPANHA), nome)
    if SOCIOS:
        campanha_propria(pessoas, caminho)
    por_sq, fontes = defaultdict(lambda: [0.0, 0.0, 0.0]), defaultdict(float)
    for x in ler_csv_zip(caminho, "receitas_candidatos_%d_BRASIL.csv" % ANO_CAMPANHA):
        v = float(x["VR_RECEITA"].replace(",", ".") or 0)
        fonte = x["DS_FONTE_RECEITA"]
        r = por_sq[x["SQ_CANDIDATO"]]
        r[0] += v
        if fonte == "FUNDO ESPECIAL":
            r[1] += v
        elif "PARTID" in fonte:
            r[2] += v
        fontes[fonte] += v
    gravar_json(SAIDA.parent / "campanha.json",
                {"ano": ANO_CAMPANHA, "sq": {sq: [round(a) for a in r] for sq, r in por_sq.items()}})
    print(f"campanha {ANO_CAMPANHA}: {len(por_sq)} candidatos, fundo eleitoral R$ {fontes['FUNDO ESPECIAL'] / 1e9:.2f} bi",
          file=sys.stderr)
    return {"ano": ANO_CAMPANHA, "total": round(sum(fontes.values())), "fefc": round(fontes["FUNDO ESPECIAL"]),
            "fp": round(fontes["FUNDO PARTIDARIO"])}


# nome pelo qual o grupo é conhecido (o acordo vem em nome de várias empresas do grupo, às vezes com outro nome)
GRUPOS_CONHECIDOS = [("odebrecht", "Odebrecht"), ("odb ", "Odebrecht"), ("coesa", "OAS (hoje Coesa)"),
                     ("andrade gutierrez", "Andrade Gutierrez"), ("camargo corr", "Camargo Corrêa"), ("braskem", "Braskem"),
                     ("utc ", "UTC"), ("mullen", "MullenLowe"), ("sbm ", "SBM Offshore"), ("jurong", "Jurong")]


def nome_conhecido(nomes):
    juntos = " ".join(n.lower() for n in nomes) + " "
    return next((bonito for chave, bonito in GRUPOS_CONHECIDOS if chave in juntos), nomes[0])


def acordos_leniencia():
    """Acordos em que empresas admitiram corrupção (CGU/AGU). O valor está no texto: 'valor total de R$ X'."""
    hoje = dt.datetime.now(dt.timezone(dt.timedelta(hours=-3))).date()
    for atras in range(8):
        d = (hoje - dt.timedelta(days=atras)).strftime("%Y%m%d")
        try:
            caminho = baixar(f"https://portaldatransparencia.gov.br/download-de-dados/acordos-leniencia/{d}", "leniencia.zip")
            break
        except urllib.error.HTTPError:
            continue
    else:
        return None
    acordos = {}  # o mesmo acordo aparece uma vez para cada empresa do grupo: conta uma vez só, pelo processo
    with zipfile.ZipFile(caminho) as z:
        nome = next(n for n in z.namelist() if n.endswith("_Acordos.csv"))
        for x in csv.DictReader(io.TextIOWrapper(z.open(nome), encoding="latin1"), delimiter=";"):
            m = re.search(r"valor total de R\$\s*([\d.]+,\d{2})", x["TERMOS DO ACORDO"])
            col = lambda inicio: next((v for k, v in x.items() if k.startswith(inicio)), "")  # nomes com acento quebrado
            if m:
                valor = round(float(m.group(1).replace(".", "").replace(",", ".")))
                a = acordos.setdefault((col("NÚMERO DO PROCESSO") or col("N"), valor),
                                       [[], valor, col("DATA DE IN"), col("SITUA")])
                a[0].append(col("RAZ").strip().title() or col("CNPJ"))
    lista = sorted(([nome_conhecido(nomes), v, d, sit] for nomes, v, d, sit in acordos.values()), key=lambda a: -a[1])
    return {"total": sum(a[1] for a in lista), "n": len(lista), "maiores": lista[:15]}


def divida_publica():
    """Dívida bruta do governo geral (Banco Central, SGS 13761 em R$ milhões e 13762 em % do PIB) e quanto ela cresceu
    por segundo, em média, nos últimos 12 meses (para o contador do site)."""
    def serie(n, quantos):
        req = urllib.request.Request(f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{n}/dados/ultimos/{quantos}?formato=json",
                                     headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            return sorted(json.load(r), key=lambda x: dt.datetime.strptime(x["data"], "%d/%m/%Y"))
    try:
        valores, pib = serie(13761, 13), serie(13762, 1)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    atual, ano_antes = float(valores[-1]["valor"]) * 1e6, float(valores[0]["valor"]) * 1e6
    return {"valor": round(atual), "data": valores[-1]["data"], "pib": float(pib[-1]["valor"]),
            "por_segundo": round((atual - ano_antes) / (365 * 86400))}


CORRUPCAO_PIB = (1.38, 2.3)  # % do PIB por ano: estudo da FIESP "Corrupção: custos econômicos e propostas de combate" (2010)


def pib_periodos():
    """PIB em valores correntes (Banco Central, SGS 4380, R$ milhões por mês) somado nos últimos 12, 36 e 120 meses:
    base da estimativa de corrupção do site."""
    hoje = dt.date.today()
    url = (f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.4380/dados?formato=json"
           f"&dataInicial=01/01/{hoje.year - 11}&dataFinal={hoje:%d/%m/%Y}")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
            meses = sorted(json.load(r), key=lambda x: x["data"][6:] + x["data"][3:5])
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    v = [float(x["valor"]) * 1e6 for x in meses]
    return {"ate": meses[-1]["data"][3:], "m12": round(sum(v[-12:])), "m36": round(sum(v[-36:])),
            "m120": round(sum(v[-120:]))}


def montar_panorama(campanha, empresas):
    tcu = RAIZ / "dados" / "tcu_total.json"
    emendas = json.loads((SAIDA.parent / "emendas.json").read_text("utf-8"))
    dados = {
        "campanha": campanha,
        "empresas": empresas,
        "leniencia": acordos_leniencia(),
        "emendas": {"total": sum(a["t"] for a in emendas["autores"].values()),
                    "pix": sum(a["pix"] for a in emendas["autores"].values())},
        "tcu": json.loads(tcu.read_text("utf-8")) if tcu.exists() else None,
        "divida": divida_publica(),
        "corrupcao": (pib := pib_periodos()) and {"pct": CORRUPCAO_PIB, **pib},
    }
    gravar_json(SAIDA.parent / "panorama.json", dados)


# ---------- preço pago a mais: cada produto contra o preço normal do mesmo produto ----------

ACIMA_DO_NORMAL = 2.0  # só conta "pago a mais" quando passou do dobro da mediana; abaixo disso é variação de modelo
MINIMO_DE_COMPRAS = 15  # sem pelo menos isso de compras do mesmo produto, não dá para dizer o que é normal
NOMES_CATEGORIAS = {"parquinho": "Brinquedo de parquinho", "combustivel": "Combustível", "remedio": "Remédio",
                    "ar": "Ar-condicionado"}
POR_ESTADO = {"combustivel"}  # combustível muda de preço por estado: o normal é o do estado


def montar_precos(cands):
    """Preço normal (mediana) de cada produto e quanto cada compra pagou a mais. O prefeito é o da época:
    compra até 2024 = eleito em 2020; de 2025 em diante = eleito em 2024."""
    origem = RAIZ / "dados" / "precos.json"
    if not origem.exists():
        return
    # o mesmo item pode vir repetido (dois lotes iguais na mesma compra): conta uma vez só
    itens = list({(it[12], it[2], it[3], it[10]): it for it in json.loads(origem.read_text("utf-8"))["itens"]}.values())

    def grupo(it):
        return f"{it[0]}|{it[1]}|{it[6] if it[0] in POR_ESTADO else ''}"
    precos = defaultdict(list)
    for it in itens:
        precos[grupo(it)].append(it[3])
    normal = {g: sorted(vs)[len(vs) // 2] for g, vs in precos.items() if len(vs) >= MINIMO_DE_COMPRAS}
    prefeito = {}
    for c in cands.values():
        if c["cargo"] == "PREFEITO" and c["sit"] == "Eleito" and c["ano"] in (2020, 2024):
            prefeito[(c["ano"], c["uf"], norm_nome(c["ue"]))] = [c["urna"].title(), c["partido"], c["sq"]]
    marcados, cidades, contagem = [], defaultdict(lambda: [0.0, 0, 0]), defaultdict(int)
    for it in itens:
        cat, prod, desc, unit, qtd, mun, uf, orgao, esfera, data, forn, cnpj, url = it
        if esfera == "Municipal" and cnpj and cnpj[:8] in SOCIOS:
            # a prefeitura comprou de empresa de quem estava no cargo na cidade (prefeito, vice ou vereador)
            for sq in SOCIOS[cnpj[:8]]:
                c = cands.get(sq)
                if (c and c["sit"] == "Eleito" and c["ano"] == (2024 if data >= "2025" else 2020 if data >= "2021" else 0)
                        and c["uf"] == uf and norm_nome(c["ue"]) == norm_nome(mun)):
                    pago_a_propria(c["cpf"] or "sq" + sq, [c], cnpj, data, forn, unit * (qtd or 1),
                                   f"prefeitura|{mun.title()}/{uf}|{c['cargo'].capitalize()}", url)
        g = grupo(it)
        if g not in normal or not mun:
            continue
        contagem[cat] += 1
        qtd = qtd or 1
        vezes = unit / normal[g]
        a_mais = (unit - normal[g]) * qtd if vezes >= ACIMA_DO_NORMAL else 0.0
        chave = f"{uf}|{norm_nome(mun)}"
        cidades[chave][2] += 1
        if not a_mais:
            continue
        cidades[chave][0] += a_mais
        cidades[chave][1] += 1
        pref = prefeito.get((2024 if data >= "2025" else 2020, uf, norm_nome(mun))) if esfera == "Municipal" else None
        marcados.append([cat, prod, desc[:260], unit, qtd, round(vezes, 1), round(a_mais), normal[g], len(precos[g]),
                         mun.title(), uf, orgao.title(), data, forn.title(), cnpj, url, pref])
    marcados.sort(key=lambda m: -m[6])
    dados = {"cats": NOMES_CATEGORIAS, "contagem": contagem, "acima": ACIMA_DO_NORMAL, "itens": marcados,
             "cidades": {k: [round(v[0]), v[1], v[2]] for k, v in cidades.items() if v[0]}}
    gravar_json(SAIDA.parent / "precos.json", dados)
    print(f"preços: {sum(contagem.values())} itens comparáveis, {len(marcados)} acima de {ACIMA_DO_NORMAL:g}x o normal, "
          f"R$ {sum(m[6] for m in marcados) / 1e6:.1f} mi a mais", file=sys.stderr)


def resumo(pessoa):
    """Condenações confirmadas e dinheiro que sumiu segundo o TCU (só decisão final)."""
    ks = pessoa["k"] if pessoa else []
    return (sum(k["s"] in ("final", "sancao") for k in ks),
            round(sum(k.get("deb", 0) for k in ks if k["s"] == "final")))


def montar_prefeituras(cands, saida):
    """Contas da prefeitura (prefeituras.py) + quem está no cargo hoje: prefeito e vereadores eleitos em 2024."""
    origem = RAIZ / "dados" / "prefeituras.json"
    if not origem.exists():
        return
    por_sq = {c[7]: p for p in saida for c in p["c"]}
    no_cargo_cidade = defaultdict(list)
    for c in cands.values():
        if c["ano"] == 2024 and c["sit"] == "Eleito":
            no_cargo_cidade[(c["uf"], norm_nome(c["ue"]))].append(c)
    cidades = json.loads(origem.read_text("utf-8"))
    sem_par = 0
    for cid in cidades:
        eleitos = no_cargo_cidade.get((cid["uf"], norm_nome(cid["nome"])), [])
        sem_par += not eleitos
        pref = next((c for c in eleitos if c["cargo"] == "PREFEITO"), None)
        if pref:
            conf, deb = resumo(por_sq.get(pref["sq"]))
            cid["prefeito"] = [pref["sq"], pref["urna"].title(), pref["partido"], conf, deb]
        # vereador e vice com condenação confirmada: só os que têm, para a ficha da cidade
        cid["outros"] = []
        for c in eleitos:
            if c["cargo"] != "PREFEITO" and (p := por_sq.get(c["sq"])):
                conf, deb = resumo(p)
                if conf:
                    cid["outros"].append([c["sq"], c["urna"].title(), c["cargo"].capitalize(), c["partido"], conf, deb])
    gravar_json(SAIDA.parent / "prefeituras.json", cidades)
    print(f"{len(cidades)} prefeituras ({sem_par} sem par no TSE pelo nome)", file=sys.stderr)


if __name__ == "__main__":
    montar()
