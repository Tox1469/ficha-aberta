"""Confere o reconhecimento de produto com descrições reais do PNCP que já enganaram o detector.
Uso: python coleta/teste_precos.py"""
from precos import combustivel, parquinho

# combustível: só o próprio litro; carro bicombustível, máquina a gasolina e óleo de motor ficam de fora
assert combustivel("ÓLEO DIESEL S-10.", "LITRO") == "Diesel S10"
assert combustivel("[COTA RESERVADA ME/EPP] - OLEO DIESEL S10", "L") == "Diesel S10"
assert combustivel("COMBUSTIVEL TIPO OLEO DIESEL S500", "LT") == "Diesel S500"
assert combustivel("Gasolina uso: para automotivos, classificação: comum", "Litro") == "Gasolina comum"
assert combustivel("VEÍCULO DE PASSEIO C/CARROCERIA SUV 25/26 BICOMBUSTÍVEL (ÁLCOOL E GASOLINA), tanque 50 litros", "UN") is None
assert combustivel("Gasolina - veículo pick-up cabine dupla", "UN") is None  # sem litro na unidade nem no texto
assert combustivel("GERADOR DE ENERGIA A GASOLINA, MONOFASICO, 50 litros", "UN") is None
assert combustivel("PULVERIZADOR COSTAL A GASOLINA CAP 12 LITROS", "UN") is None
assert combustivel("OLEO 20W50 PARA MOTORES FLEX GASOLINA ETANOL", "LITRO") is None

# parquinho: só brinquedo avulso com o nome logo no começo
assert parquinho("01-CARROSSEL 8 LUGARES Fabricado com tubos de aço carbono", "UN") == "Gira-gira de ferro, 8 lugares"
assert parquinho("Brinquedo Em Geral material: aço carbono, tipo: gangorra, cor: colorida", "UN") == "Gangorra de ferro"
assert parquinho("Misturador capacidade 1: cerca de 25 tubos ou frascos, tipo: homogeneizador tipo gangorra", "UN") is None
assert parquinho("Batedeira planetária industrial 12 litros; estrutura em aço; escorregador", "UN") is None
assert parquinho("KID PLAY 5.25M X 5.25M, 2 CONEXÕES DE ESCORREGADOR ROTOMOLDADO", "UN") is None
assert parquinho("Ponte de madeira com obstaculos e escorregador", "UN") is None
assert parquinho("PRESTAÇÃO DE SERVIÇOS DE MANUTENÇÃO DE 01 (UM) GANGORRA, FABRICADO EM TUBO DE AÇO", "UN") is None
assert parquinho("Gangodinos - fabricado em base em aço de 4”. Estrutura suporta 3 gangorras", "UN") is None

print("ok: reconhecimento de produto")
