# Execução distribuída do Coach Silvs

## Visão geral

O coordenador mantém uma fila SQLite e nunca executa Webots. Cada processo
worker informa sua capacidade com `--workers N`; são criados N slots, e cada
slot reserva no máximo uma partida. A reserva possui prazo (`lease`): se o
computador desaparecer da VPN, a partida volta à fila. Conclusões são aceitas
uma única vez pelo identificador determinístico da partida.

Todo tráfego usa `Authorization: Bearer`. Gere dois segredos: o administrativo
cria jobs e lê resultados; o de worker apenas registra capacidade, reserva e
entrega partidas:

```bash
export COACH_SILVS_ADMIN_TOKEN="$(openssl rand -hex 32)"
export COACH_SILVS_WORKER_TOKEN="$(openssl rand -hex 32)"
```

Não coloque esses valores no Git, README, histórico compartilhado ou arquivos
de execução. O `config.json` remove o token administrativo automaticamente.

## Coordenador

No computador principal:

```bash
source .venv/bin/activate
export COACH_SILVS_ADMIN_TOKEN='SEGREDO_ADMINISTRATIVO_DE_64_CARACTERES'
export COACH_SILVS_WORKER_TOKEN='SEGREDO_DOS_WORKERS_DE_64_CARACTERES'
coach-silvs-distributed coordinator \
  --host 127.0.0.1 \
  --port 8090 \
  --data-dir runs/distributed
```

Exponha apenas à VPN, com HTTPS privado:

```bash
tailscale serve --bg 8090
tailscale serve status
```

Use a URL `https://NOME-DA-MAQUINA.TAILNET.ts.net` exibida pelo comando como
URL do coordenador. Não use `tailscale funnel`: Funnel tornaria o serviço
público.

Inicie a evolução apontando para a fila:

```bash
export COACH_SILVS_ADMIN_TOKEN='SEGREDO_ADMINISTRATIVO_DE_64_CARACTERES'
coach-silvs-evolve run \
  --backend travesim \
  --execution distributed \
  --coordinator-url 'https://NOME-DA-MAQUINA.TAILNET.ts.net' \
  --competitors 10 \
  --matches-per-pair 5 \
  --generations 10 \
  --field-strategy shared \
  --early-stopping none \
  --run-name shared-vpn
```

## Computador colaborador

Pré-requisitos: Git, Python 3.11, mesma versão do Webots usada pelo time,
TraveSim compilado e Tailscale conectado ao tailnet.

```bash
git clone URL_DO_REPOSITORIO
cd travesim
python3.11 -m venv .venv
source .venv/bin/activate
PIP_USER=0 python3.11 -m pip install -e './strategy[dev]'
make
export COACH_SILVS_WORKER_TOKEN='SEGREDO_RECEBIDO_POR_CANAL_SEGURO'
coach-silvs-distributed worker \
  --coordinator 'https://NOME-DA-MAQUINA.TAILNET.ts.net' \
  --workers 2 \
  --worker-name 'computador-da-ana' \
  --travesim-root "$PWD" \
  --webots-executable '/caminho/para/webots'
```

Escolha `--workers` de acordo com CPU e memória. Comece com 1; aumente somente
se uma partida completa continuar estável. Cada slot inicia um Webots e dois
clientes de estratégia. O worker pode ser interrompido com `Ctrl+C`; jobs
reservados retornam à fila quando o lease expirar.

## VPN e acesso mínimo

1. O administrador instala e autentica o Tailscale no computador principal.
2. No painel Tailscale, convida os colaboradores como `Member`, não `Admin`.
3. Cria uma Grant que permita aos colaboradores somente HTTPS para o
   coordenador. Não anuncie subnet routes e não habilite Funnel.
4. Cada colaborador instala o Tailscale, aceita o convite e testa o nome
   MagicDNS do coordenador.
5. Somente o token de worker é enviado por um canal separado; o token
   administrativo permanece no coordenador. VPN e Bearer token são
   camadas independentes.

Exemplo conceitual de Grant (substitua identidades e destino pelos valores do
seu tailnet; valide no editor de políticas antes de salvar):

```json
{
  "grants": [
    {
      "src": ["group:coach-silvs-workers"],
      "dst": ["NOME-DO-COORDENADOR:443"],
      "ip": ["tcp:443"]
    }
  ]
}
```

## API do coordenador

Todas as rotas usam JSON e Bearer token:

- `POST /api/v1/jobs`: cadastra lote idempotente de partidas;
- `POST /api/v1/lease`: reserva até `limit` jobs por um prazo;
- `POST /api/v1/jobs/{id}/complete`: entrega resultado e replay;
- `POST /api/v1/jobs/{id}/fail`: devolve ou encerra um job;
- `POST /api/v1/results`: resultados e contagens incrementais da geração;
- `GET /api/v1/status`: jobs e slots ativos por worker.

O dashboard lê `distributed.json`, atualizado pela evolução, e mostra workers,
slots ativos, fila, partidas em execução e concluídas.

## Diagnóstico

```bash
curl -H "Authorization: Bearer $COACH_SILVS_WORKER_TOKEN" \
  'https://NOME-DA-MAQUINA.TAILNET.ts.net/api/v1/status'
tailscale ping NOME-DA-MAQUINA
tailscale serve status
```

Se versões de código, Webots ou regras divergirem, pare o worker e sincronize o
repositório antes de continuar. Replays e resultados ficam no coordenador; os
arquivos temporários locais ficam em `runs/worker-replays/`.
