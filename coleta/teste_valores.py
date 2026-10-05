"""Teste do extrator de valores com trechos reais de acórdãos conferidos à mão. Uso: python coleta/teste_valores.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from valores_tcu import extrair  # noqa: E402

# Acórdão 391/2026-1C: débito individual + solidário em subitens, multa em subitem sem verbo
t = ("ACORDAM em: 9.1. julgar irregulares as contas do Sr. Marcelo Lima de Farias e da sociedade empresária Petlas; "
     "9.2. condenar os responsáveis ao pagamento das quantias abaixo indicadas: "
     "9.2.1. Débito de responsabilidade do Sr. Marcelo Lima de Farias, individualmente: Data de ocorrência "
     "Valor histórico (R$) 23/6/2014 562.726,62 9.2.2. Débito solidário entre o Sr. Marcelo Lima de Farias e a "
     "empresa Petlas: Data de ocorrência Valor histórico (R$) 23/6/2014 197.714,76 4/12/2014 760.441,38 "
     "9.3. fixar o prazo de 15 (quinze) dias; 9.4. aplicar as seguintes multas individuais, com fulcro no art. 57: "
     "9.4.1. Sr. Marcelo Lima de Farias: R$ 1.400.000,00; e 9.4.2. empresa Petlas: R$ 900.000,00; "
     "9.5. fixar o prazo")
v = extrair(t, "MARCELO LIMA DE FARIAS")
assert (v["deb"], v["mul"], v["sol"]) == (1520882.76, 1400000.0, True), v

# Acórdão 1674/2026-1C: data "1º/1/2020" e coluna "Débito"; uma linha "Crédito" tem que ser descontada
t = ("9.1. julgar irregulares as contas dos srs. José Waldoli Filgueira Valente e Victor Correa Cassiano, condenando o "
     "sr. José Waldoli Filgueira Valente ao pagamento das importâncias a seguir: Data de ocorrência Valor histórico (R$) "
     "Tipo da parcela 1º/1/2020 59.802,87 Débito 18/2/2020 254.782,00 Débito 20/2/2020 1.000,00 Crédito "
     "9.2. aplicar ao sr. José Waldoli Filgueira Valente a multa prevista no art. 57, no valor de R$ 3.000.000,00 "
     "(três milhões de reais); 9.3. aplicar ao sr. Victor Correa Cassiano a multa prevista no art. 58, no valor de "
     "R$ 20.000,00 (vinte mil reais);")
v = extrair(t, "JOSE WALDOLI FILGUEIRA VALENTE")
assert (v["deb"], v["mul"]) == (313584.87, 3000000.0), v
v = extrair(t, "VICTOR CORREA CASSIANO")
assert (v["deb"], v["mul"]) == (0.0, 20000.0), v  # citado no 9.1, mas o débito é só do outro

# Acórdão 2815/2026-1C: "aplicar-lhe" com hífen não ASCII, nome só no item anterior
t = ("9.3. julgar irregulares as contas de Antônio Diniz Braga Neto, nos termos dos arts. 1º; "
     "9.4. aplicar‑lhe a multa prevista no art. 58, inciso I, no valor de R$ 15.000,00 (quinze mil reais); "
     "9.5. autorizar, caso requerido, o pagamento da importância devida em até 36 parcelas;")
v = extrair(t, "ANTONIO DINIZ BRAGA NETO")
assert (v["deb"], v["mul"]) == (0.0, 15000.0), v

# Acórdão 1841/2003-1C: valor em cruzeiro real não pode virar real ("CR$" contém "R$")
t = ("9.1. julgar as presentes contas irregulares, condenar o Sr. Paulo Celso Fonseca Marinho ao pagamento da "
     "quantia de CR$ 63.112.500,00 (sessenta e três milhões de cruzeiros reais), atualizada a partir de 03/09/1993;")
v = extrair(t, "PAULO CELSO FONSECA MARINHO")
assert (v["deb"], v["ant"]) == (0.0, True), v

# Tabela com parcela de antes do Real e outra depois: só a de depois entra
t = ("8.1. julgar as contas irregulares e condenar o Sr. José Alcure de Oliveira ao pagamento das quantias: "
     "Data Valor 10/12/1993 2.000.000,00 15/8/1995 12.500,00 8.2. autorizar a cobrança judicial")
v = extrair(t, "JOSE ALCURE DE OLIVEIRA")
assert (v["deb"], v["ant"]) == (12500.0, True), v

# Ninguém mais citado: não pode pegar débito de outra pessoa
assert extrair(t, "FULANO DE TAL") is None

# Duas multas no mesmo item: cada um fica com a sua
t = "9.3. aplicar ao Sr. João da Silva a multa de R$ 10.000,00 e ao Sr. Pedro Souza a multa de R$ 5.000,00;"
assert extrair(t, "JOAO DA SILVA")["mul"] == 10000.0
assert extrair(t, "PEDRO SOUZA")["mul"] == 5000.0

# "condenando o administrador" é artigo, não pronome: quem não foi nomeado depois do verbo fica de fora
t = ("9.1. julgar irregulares as contas de Ana Lima e Rui Costa, condenando o administrador Rui Costa ao pagamento "
     "de R$ 80.000,00;")
assert extrair(t, "ANA LIMA") is None
assert extrair(t, "RUI COSTA")["deb"] == 80000.0

# "condenando-os" pega todos os citados
t = "9.1. julgar irregulares as contas de Ana Lima e Rui Costa, condenando-os ao pagamento de R$ 80.000,00;"
assert extrair(t, "ANA LIMA")["deb"] == 80000.0

print("ok")

# Acórdão 2191/2022-2C: o subitem 9.2.2 é débito do município, não do Sandro
t = ("9.2. julgar irregulares as contas do Sr. Sandro Matos Pereira e do Município de São João de Meriti/RJ, "
     "condenando os responsáveis abaixo ao pagamento das quantias originais a seguir discriminadas: "
     "9.2.1. Sr. Sandro Matos Pereira: Data Valor (R$) 30/7/2010 3.711.765,53 "
     "9.2.2. Município de São João de Meriti/RJ: Data Valor (R$) 28/9/2016 6.848.392,63 "
     "9.3. aplicar ao Sr. Sandro Matos Pereira a multa prevista no art. 57, no valor de R$ 300.000,00;")
v = extrair(t, "SANDRO MATOS PEREIRA")
assert (v["deb"], v["mul"]) == (3711765.53, 300000.0), v

# Acórdão 18929/2021-1C: tabela com o valor antes da data; multa em tabela com o nome
t = ("9.1. julgar irregulares as contas do sr. José Camilo Zito dos Santos Filho, condenando-o ao pagamento das "
     "quantias abaixo relacionadas: VALOR ORIGINAL (R$) DATA DA OCORRÊNCIA 2.200.000,00 30/12/2008 3.300.000,00 "
     "11/3/2009 3.300.000,00 17/9/2009 2.199.999,99 20/10/2010 9.2. fixar o prazo de 15 (quinze) dias; "
     "9.3. aplicar ao responsável abaixo arrolados a pena de multa prevista no art. 57: Responsável Valor (R$) "
     "José Camilo Zito dos Santos Filho R$ 10.500.000,00 9.4. julgar irregulares as contas dos srs. Alexandre "
     "Aguiar Cardoso e de Washington Reis de Oliveira; 9.5. aplicar aos responsáveis abaixo arrolados a pena de "
     "multa prevista no art. 58: Responsável Valor (R$) Alexandre Aguiar Cardoso 40.000,00 Washington Reis de "
     "Oliveira 40.000,00 9.6. fixar o prazo")
v = extrair(t, "JOSE CAMILO ZITO DOS SANTOS FILHO")
assert (v["deb"], v["mul"]) == (10999999.99, 10500000.0), v
v = extrair(t, "WASHINGTON REIS DE OLIVEIRA")
assert (v["deb"], v["mul"]) == (0.0, 40000.0), v

# Acórdão 4916/2024-2C: um item condena 7 pessoas e separa os débitos por bloco; cada um fica com o seu
t = ("9.5. julgar irregulares as contas dos responsáveis Rivalmar Luís Gonçalves Moraes, Rosileia Mendes Oliveira, "
     "Rivalgenia Conceição Goncalves Moraes e Benito Coelho Filho, condenando-o ao pagamento das importâncias a "
     "seguir especificadas: Débitos relacionados ao responsável Rivalmar Luís Gonçalves Moraes (CPF: XXX.111.111-XX), "
     "em solidariedade com Rosileia Mendes Oliveira (CPF: XXX.222.222-XX): Data de ocorrência Valor histórico (R$) "
     "12/1/2012 110.250,00 12/1/2012 10.000,00 Débitos relacionados à responsável Rivalgenia Conceição Goncalves "
     "Moraes (CPF: XXX.111.222-XX): Data de ocorrência Valor histórico (R$) 3/4/2012 50.000,00 "
     "9.6. aplicar multa")
assert extrair(t, "RIVALGENIA CONCEICAO GONCALVES MORAES")["deb"] == 50000.0
assert extrair(t, "ROSILEIA MENDES OLIVEIRA")["deb"] == 120250.0
assert extrair(t, "BENITO COELHO FILHO") is None
print("ok, casos extras")

# Acórdão 1492/2006-1C: parcela de 1992 em cruzeiros com "Cr$" entre a data e o valor não pode virar R$ 21 bilhões
t = ("9.1. julgar as presentes contas irregulares e em débito Jadilson Nogueira de Barros e José Renato Torres de "
     "Almeida, condenando-os, solidariamente, ao pagamento das importâncias abaixo indicadas: Data Valor original do "
     "débito 17/8/1992 Cr$ 21.045.703.979,68 10/3/1997 R$ 7.335,10 11/3/1997 R$ 7.625,85 12/3/1997 R$ 6.419,72 "
     "25/3/1997 R$ 4.292,38 9.2. autorizar a cobrança")
v = extrair(t, "JADILSON NOGUEIRA DE BARROS")
assert (v["deb"], v["ant"]) == (25673.05, True), v
print("ok, cruzeiro")

# Acórdãos antigos: itens "a)" e "8.1. -", valor em texto ("importância de R$ 35.000,00") e moeda antiga
t = ("ACORDAM em: a) julgar irregulares as presentes contas e em débito o Sr. Deraldo Sena Bellas, condenando-o ao "
     "pagamento da importância de R$ 35.000,00 (trinta e cinco mil reais), calculada a partir de 30/01/97; "
     "b) autorizar a cobrança judicial")
assert extrair(t, "DERALDO SENA BELLAS")["deb"] == 35000.0
t = ("em: 8.1. - julgar irregulares as presentes contas, e condenar o Sr. José Ricardo Queiroz Maciel ao pagamento da "
     "quantia de Cz$ 513.346,63 (quinhentos e treze mil cruzados); 8.2. autorizar")
v = extrair(t, "JOSE RICARDO QUEIROZ MACIEL")
assert (v["deb"], v["ant"]) == (0.0, True), v
print("ok, antigos")
