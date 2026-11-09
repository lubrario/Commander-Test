# Taberna Vault v5 — busca com substituição automática para Commander

Esta versão implementa o fluxo **verificar carta → escolher se disponível na Taberna Geek (loja LigaMagic 41369) → sugerir carta de função semelhante quando não houver anúncio → verificar a substituta na mesma loja → adicionar somente resultados com oferta identificada**.

## Para publicar sem programação

Leia **PUBLICAR_SEM_PROGRAMAR.txt**, que contém os cliques no GitHub e Render.

- O ZIP é um projeto Python web com página HTML; não é preciso saber programar.
- No GitHub, coloque todos os arquivos na raiz do repositório, inclusive `builder_engine.py` (novo na v5).
- No Render, use o **plano Free** já configurado; não adicione disco pago.
- Defina a variável secreta `REEF_API_KEY` com a chave da sua conta [ReefAPI](https://reefapi.com/ligamagic-api). **Nunca envie a chave ao GitHub nem ao chat.**
- A variável `TABERNA_ADMIN_TOKEN` continua obrigatória para uso em site público. Aplique essa chave na seção Fonte e importação dentro do webapp.
- O Render pode exigir que você salve as alterações e clique em **Manual Deploy → Deploy latest commit**.
- Abra **Deck inteligente**, escolha o comandante e cole até **10 cartas por execução** (`1 Nome da Carta` por linha).
- Clique em **Conferir e substituir**; o app solicita sua confirmação antes de gastar créditos.
- Baixe a decklist TXT ou o relatório CSV; os resultados são salvos no navegador.

## Fontes e custos

A fonte ao vivo é uma **API de terceiros**, ReefAPI, que consulta anúncios da LigaMagic. Ela não é uma API oficial da Taberna Geek nem uma integração feita pela loja. A documentação da ReefAPI anuncia **1.000 créditos gratuitos iniciais** (sem cartão) e **4 créditos por consulta de carta**, sujeitos a mudanças pelo provedor. Até 30 verificações por execução (até 120 créditos), incluindo substitutas, e sem buscas em segundo plano.

- Um resultado **disponível** requer oferta da loja 41369, preço e quantidade informados para o número de cópias pedido.
- **Sem oferta identificada** significa que a consulta não encontrou anúncio na LigaMagic; não comprova estoque físico zero.
- **Inconclusivo** significa que a resposta não permitiu validar quantidade, loja ou cobertura; o programa não marca como indisponível nem substitui automaticamente.
- O limite de anúncios retornados é 2.000 por carta. Se houver indícios de paginação incompleta, a resposta ficará **inconclusiva** quando nenhuma oferta compatível for encontrada.
- Sem `REEF_API_KEY`, só é possível procurar no último catálogo CSV importado, com **aviso de que os valores não são uma consulta ao vivo**.
- As fontes históricas nunca serão tratadas como estoque atual.

A Scryfall fornece textos Oracle, cor e legalidade em Commander para produzir uma lista curta de candidatos. As recomendações são **heurísticas de função** (descarte, fichas, sacrifício, ramp, remoções, tribos etc.), não análises completas de combos. A versão evita repetição de nomes presentes na lista fornecida. Revise manualmente sinergias, quantidades, preço, condição e possíveis restrições antes de comprar.

## Arquivos

- `app.py`: API Python existente, importação CSV, histórico e rotas `/api/builder/*`
- `builder_engine.py`: consulta ReefAPI, análise de ofertas por loja, candidatos Scryfall
- `app.js` + `index.html` + `styles.css`: interface web
- `render.yaml` e `Dockerfile`: publicação gratuita no Render
- `tests/`: testes automatizados, incluindo respostas simuladas de ofertas

## Situação dos testes

As rotas e o algoritmo foram testados localmente com **respostas simuladas** da ReefAPI e da Scryfall. Não foi possível executar uma consulta real à ReefAPI da sua conta porque nenhuma chave foi fornecida. Caso o serviço de terceiros altere a estrutura dos dados, poderá ser necessário ajustar o adaptador. A versão não afirma que cartas da Taberna Geek estão disponíveis até receber uma resposta verificável.

## Rotas novas

- `GET /api/builder/status`: mostra se a chave ReefAPI está configurada (nunca retorna a chave)
- `POST /api/builder/availability`: verifica uma carta e quantidade na loja 41369; exige token de admin se configurado
- `POST /api/builder/candidates`: sugere alternativas por texto Oracle e identidade de cores; exige token de admin se configurado

A API do restante do Taberna Vault v4 foi preservada. O banco SQLite no Render Free não tem persistência garantida: faça backup de importações. As escolhas do Deck inteligente ficam no armazenamento local do navegador, que também pode ser apagado. Exporte o TXT/CSV.

**URLs de referência:** https://reefapi.com/ligamagic-api , https://reefapi.com/docs/ligamagic , https://www.ligamagic.com.br/?view=mp/showcase/home&id=41369 , https://scryfall.com/docs/api
