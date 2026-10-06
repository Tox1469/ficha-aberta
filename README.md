# Ficha Aberta

O que tribunais e órgãos de controle já registraram sobre quem se candidatou de 2018 a 2026, e quanto cada prefeitura gastou.

O site cruza, pelo CPF, todos os candidatos do TSE com as listas oficiais de condenações e sanções, e mostra:

- o ranking de candidatos pelo valor que o TCU mandou devolver aos cofres públicos;
- a ficha de cada um, com cada caso, em que pé está (decisão final, em recurso ou derrubado) e o link para a fonte;
- a comparação entre partidos pela taxa por mil candidaturas, para não favorecer partido grande nem pequeno;
- quem está no cargo hoje (eleitos de 2024, de 2022 e senadores de 2018);
- quanto cada uma das 5.570 prefeituras gastou, por área, comparado com cidades do mesmo tamanho;
- compras públicas com preço muito acima do normal (parquinho, ar-condicionado, remédio, combustível);
- para onde foi a verba que cada deputado e senador mandou (emendas) e o que gastou do mandato (cota);
- as empresas em que cada político é sócio e o dinheiro público que foi para elas.

Ninguém escreve nada à mão. As mesmas regras valem para todo mundo.

## Fontes

| Fonte | O que entra | Como liga ao candidato |
|---|---|---|
| TSE, candidaturas 2018, 2020, 2022, 2024 e 2026 | nome, cargo, partido, resultado | é a base |
| TSE, motivos de indeferimento e cassação | Ficha Limpa, abuso de poder, compra de voto, gasto ilícito, conduta vedada | pela candidatura |
| TCU, contas julgadas irregulares, lista eleitoral e inabilitados | condenações com trânsito em julgado | CPF |
| TCU, texto completo dos acórdãos | valor do débito e da multa | número do acórdão + nome |
| CGU, CEIS | condenações por improbidade e outras sanções | CPF |
| CGU, CEAF | servidores federais expulsos | nome completo + 6 dígitos do meio do CPF (o CPF vem mascarado) |
| Tesouro Nacional, SICONFI (contas anuais DCA) | gasto da prefeitura por área, receita e royalties | código IBGE + nome da cidade |
| Portal Nacional de Contratações Públicas (PNCP) | preço unitário de cada item comprado | cidade (prefeito da época) |
| Portal da Transparência, emendas por favorecido | quem recebeu cada pagamento de emenda | nome parlamentar do autor |
| Câmara e Senado, cota parlamentar | cada nota reembolsada | cadastro do deputado (CPF pela API) e nome completo + UF do senador |
| TSE, prestação de contas de 2026 | quanto cada campanha recebeu e com quem gastou | pela candidatura |
| Receita Federal, quadro de sócios do CNPJ (mensal) | empresas de cada político | nome completo + 6 dígitos do meio do CPF |
| CGU, acordos de leniência; Banco Central, dívida bruta | números do país | não liga a pessoas |

Em 2024 o TSE deixou de publicar o CPF dos candidatos. Quem concorreu em 2024 e também em 2020, 2022 ou 2026 é ligado pelo título de eleitor. O CPF nunca aparece no site.

O que fica de fora e por quê está na aba "Como funciona" do site.

## Rodar localmente

Só precisa de Python 3.10+, sem dependências.

```bash
python coleta/teste_valores.py # confere o extrator de valores com casos reais
python coleta/valores_tcu.py   # lê os acórdãos do TCU (vários GB na primeira vez; depois só o ano corrente)
python coleta/tcu_total.py     # total que o TCU mandou devolver, de todo mundo
python coleta/precos.py        # compras no PNCP (incremental)
python coleta/prefeituras.py   # contas das prefeituras no Tesouro (uma vez por mês; demora pelo limite da API)
python coleta/empresas.py      # quadro de sócios da Receita (uma vez por mês; baixa uns 2 GB)
python coleta/coletar.py       # baixa o resto, cruza tudo e gera os arquivos do site
cd site && python -m http.server 8000
```

Os downloads ficam em `coleta/cache/`. O que é lento de refazer fica em `dados/` no repositório: valores dos acórdãos, compras já lidas, contas das prefeituras e empresas dos políticos (sem CPF: só o número da candidatura).

GitHub Actions: `atualizar.yml` roda todo dia e publica no GitHub Pages; `prefeituras.yml` e `empresas.yml` rodam uma vez por mês.

Nenhum CPF sai daqui: todo arquivo gravado passa por `gravar_json`, que apaga qualquer coisa com cara de CPF.

## Correções e casos novos

Achou um erro? Abra uma issue com o link da fonte oficial.

Casos de outras fontes (por exemplo, uma condenação criminal) podem entrar por pull request em `dados/casos_manuais.json`. Cada caso precisa de fonte oficial e número de processo, senão o script recusa:

```json
{
  "sq_candidato": "130002352213",
  "tipo": "Condenação por peculato",
  "status": "final",
  "status_texto": "Trânsito em julgado em 10/03/2025",
  "data": "2025-03-10",
  "orgao": "Tribunal de Justiça de Minas Gerais",
  "processo": "0000000-00.0000.8.13.0000",
  "fonte_url": "https://link-oficial-da-decisao",
  "resumo": "O que a decisão diz, em uma ou duas frases, sem adjetivos."
}
```

`sq_candidato` é o número da candidatura no TSE (aparece na URL da ficha). `status` é `final`, `recurso`, `andamento` ou `revertido`.

## Licença

Código sob licença MIT. Os dados são públicos e pertencem aos órgãos de origem.
