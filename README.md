# Taberna Vault — versão Render gratuita

**Sem plano Free, sem disco pago:** veja [PUBLICAR_SEM_PROGRAMAR.txt](PUBLICAR_SEM_PROGRAMAR.txt).

> ⚠️ O banco SQLite é temporário na hospedagem gratuita. Dados podem sumir ao hibernar,
> reiniciar ou republicar. Baixe o backup ZIP após cada importação e restaure conforme necessário.

O arquivo `render.yaml` já declara `plan: free`, sem bloco `disk`. O banco fica em `/tmp`.
A API ganhou `/api/backup.zip` (GET, autenticado) e `/api/restore` (POST, arquivo ZIP,
chave de administrador) para salvar e restaurar ofertas, histórico e metadados.

# Taberna Vault — Webapp de singles MTG (v2)

Webapp responsivo para **Magic: The Gathering da Taberna Geek**. Base para um futuro construtor de decks Commander. Interface HTML/CSS/JS (sem build) + API Python standard library + SQLite.

## Status da fonte

**Não há integração ao vivo garantida com a Taberna Geek.** O site oficial bloqueou as consultas automatizadas e não foi identificado um feed/API pública e autorizada. O aplicativo **não inventa estoque atual**: permite carregar um CSV fornecido pela loja ou consultar, **sob demanda**, uma URL HTTPS de CSV autorizada pela própria loja. A hora da importação *não* é a hora da disponibilidade, a menos que a origem confirme isso. Referências históricas devem ser importadas com a opção "Marcar como referência histórica".

## Rodar agora no computador

Requer **Python 3.10+**. Não exige instalação de bibliotecas. No macOS, também é possível tentar abrir o arquivo `iniciar-mac.command`; no Windows, `iniciar-windows.bat`. Esses atalhos abrem o app no navegador e precisam que o Python já esteja instalado.

```bash
python app.py
```

Abra **http://127.0.0.1:8765** no navegador.

O banco `taberna_inventory.sqlite3` é criado ao lado dos arquivos (não incluído no ZIP). Para manter o histórico, guarde esse banco. No modo local, não é necessário token.

## Rodar como webapp com Docker

Requer Docker com Docker Compose. Use um token forte para proteger uploads, consultas e configuração do feed:

```bash
cp .env.example .env
# edite .env e substitua o texto de exemplo por um token longo e secreto
docker compose up --build -d
```

Abra **http://localhost:8765**. Em **Fonte e importação → Chave de administrador**, digite o mesmo token definido no `.env` para autorizar operações de edição. O token é guardado apenas na sessão do navegador, não no banco.

O volume Docker `taberna-data` preserva o SQLite entre reinicializações e atualizações do container. Ao executar em rede, configure HTTPS na camada de proxy/hospedagem.

## Publicar na internet (Render)

Inclui `render.yaml` e `Dockerfile`. Para publicar:

1. Envie os arquivos deste projeto para um **repositório GitHub** (não envie `.env` ou o banco `.sqlite3`).
2. No Render, escolha **New → Blueprint**, conecte o repositório e autorize a criação da aplicação.
3. Configure `TABERNA_ADMIN_TOKEN` com uma chave longa e exclusiva, quando solicitado.
4. Habilite o banco temporário (use backup) definido no `render.yaml` e publique. O arquivo indica o plano *starter* (pode haver cobrança; confira as condições vigentes no Render).
5. Acesse a URL HTTPS criada pelo Render, entre em **Fonte e importação**, aplique a chave e importe um CSV ou configure um feed autorizado.

**Não basta enviar somente o HTML a uma hospedagem estática.** As consultas, o histórico, o banco de dados e o acesso à Scryfall precisam da API Python.

O aplicativo não está hospedado por padrão. O ZIP permite rodar e implantar, mas não cria uma URL pública automaticamente.

## Como usar

- **Visão geral:** métricas, fonte do último catálogo, últimas consultas.
- **Catálogo de singles:** pesquisa por nome, edição e disponibilidade, ordenação por preço/quantidade e paginação.
- **Alterações:** comparação entre duas importações **completas e comparáveis**.
- **Commander Lab:** busca um comandante pela Scryfall e filtra ofertas por identidade de cores e legalidade. Cartas não enriquecidas aparecem como não verificadas, não como ilegais. **Não é ainda um gerador de decks sinérgicos**.
- **Fonte e importação:** CSV com sinalização de histórico, endereço do feed CSV autorizado, consulta sob demanda, enriquecimento Scryfall, exportação JSON/CSV, controle de token.

## Modelo de CSV

Arquivo de exemplo em `examples/modelo_estoque_preencher.csv`:

```csv
nome;edicao;numero;preco;quantidade;idioma;condicao;acabamento;url;observado_em
Sol Ring;CMM;396;5,90;2;EN;NM;nonfoil;https://exemplo.com/carta;2026-11-09
Arcane Signet;ELD;331;2,50;4;PT;NM;nonfoil;https://exemplo.com/carta;2026-11-09
```

**Estas são linhas ilustrativas e não representam estoque da Taberna Geek.**

Obrigatório: nome + preço e/ou quantidade. Aceita vírgula, ponto e vírgula ou TAB como delimitador e preço brasileiro com vírgula decimal. Preços e quantidades ausentes permanecem desconhecidos. Ao usar `Quantidade > 0`, apenas reflete o estado da última fonte importada.

O arquivo `examples/historico_verificado_setembro_2026.csv` contém referências **históricas** e **incompletas**; não representa o catálogo atual. Caso queira testá-lo, marque a opção histórica no painel.

## API para futura integração Commander

| Método | Rota | O que retorna |
|---|---|---|
| GET | `/api/status` | Origem, hora, métricas, necessidade de chave |
| GET | `/api/cards?q=sliver&set=mh1&stock=1&sort=price_asc&limit=35&offset=0` | Ofertas com `total` e paginação |
| GET | `/api/snapshots` | Histórico de importações |
| GET | `/api/changes` | Entradas, saídas e alterações |
| GET | `/api/export.json` | Base do último snapshot com metadados Scryfall |
| GET | `/api/export.csv` | Exportação de ofertas em CSV |
| POST | `/api/upload` | Importação CSV `{csv, filename, historical}` |
| POST | `/api/feed` | Configurar URL do CSV `{url}` |
| POST | `/api/sync` | Consultar feed HTTPS previamente configurado |
| POST | `/api/enrich` | Metadados Scryfall `{limit}` |
| POST | `/api/commander` | Filtrar por cores/legalidade `{name}` |
| GET | `/health` | Verificação de integridade básica da API |

Ao publicar externamente, todos os POSTs exigem `X-Admin-Token: <seu token>` (definido em `TABERNA_ADMIN_TOKEN`); os GETs são somente leitura. A URL do feed configurada aparece no status GET; **não insira tokens de acesso ou credenciais no endereço**.

O endpoint Commander é de triagem e **não calcula pontuação de sinergia, combos ou listas de 100 cartas**. Esse é o escopo de uma etapa futura usando dados Oracle e estoque com procedência confirmada.

## Segurança e limitações

- Não faz crawler em páginas bloqueadas, não quebra proteção antibot nem faz consultas em segundo plano.
- Feeds devem ser HTTPS público, sem redirecionamentos; há validação de endereços privados para reduzir riscos de SSRF.
- Este é um aplicativo para uso pessoal ou pequena equipe, não uma plataforma multiusuário: a chave controla escrita; **os GETs, inclusive exportação e feed configurado, são públicos se a URL for pública**.
- O servidor usa Python `http.server` da biblioteca padrão; publique atrás do TLS/reverse proxy da hospedagem. Para uso com alta concorrência, recomenda-se substituir por stack WSGI/ASGI, autenticação por usuário e armazenamento gerenciado.
- Importação automática só será possível com acesso legítimo e estável a um feed oficial atualizado. Sem isso, o botão exibe a limitação.
- A Scryfall fornece características de cartas, não estoque, preços nem ofertas da Taberna Geek.
- Em hospedagens sem banco temporário (use backup), perderá o banco ao reiniciar. O `render.yaml` inclui banco temporário (use backup).

## Testes

```bash
python -m unittest discover -s tests -v
```

Os testes de parser, snapshots, diff e filtros são locais; não fazem consultas externas à loja nem à Scryfall.
