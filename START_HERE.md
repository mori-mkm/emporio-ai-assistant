# Instalar e iniciar o harness

Este pacote prepara o desenvolvimento, não contém o aplicativo nem executa chamadas de IA. Destino:
`C:\Users\mathe\Documents\projects\case\emporio-ai-assistant`

## Extração
1. Feche sessões do Claude abertas neste diretório.
2. Extraia o CONTEÚDO do ZIP diretamente na raiz indicada. `CLAUDE.md`, `AGENTS.md`, `.claude`, `docs` e `scripts` devem estar lado a lado com os arquivos do desafio, sem uma pasta adicional de encapsulamento.
3. Se já houver arquivos com os mesmos nomes, compare antes de substituir. O pacote não inclui `.gitignore`, `.env`, dados, código da aplicação ou metadados `.git` para não sobrescrevê-los.
4. Os nomes em MAIÚSCULAS são intencionais: `CLAUDE.md` e `AGENTS.md` são convenções das ferramentas. No Windows não crie cópias em minúsculas.

No PowerShell:
```powershell
Set-Location 'C:\Users\mathe\Documents\projects\case\emporio-ai-assistant'
python scripts/verify.py --mode harness
claude --version
```
O verificador usa somente Python padrão, não instala dependências, não faz rede e não roda o aplicativo. O status HARNESS PASS não significa APP PASS.

## Antes do primeiro prompt
- Claude Code informado: 2.1.314. O hook usa a forma exec documentada (`python` + argumentos), evitando depender de expansão/quoting do PowerShell.
- O Python 3.11 precisa estar no PATH dos processos do Claude. Se faltar, o hook bloqueia a operação em vez de prosseguir. Não desative o hook; ajuste o executável com revisão humana e valide novamente.
- Ao abrir `claude`, aceite a confiança da pasta apenas depois de revisar os arquivos. Confira as permissões efetivas em `/permissions` e os hooks em `/hooks`.
- Confira instruções herdadas/pessoais com `/memory`; não copie regras do Closer. Não compartilhe conteúdo sensível desses arquivos.
- Confirme se a sessão usa sua assinatura ou billing por API. Não reutilize automaticamente a chave do case para o Claude Code.
- Chaves reais são criadas por você (EMP-1 e EMP-2). Nada de chave em prompts, Linear ou Git.
- Se o diretório ainda não for um checkout Git, o primeiro prompt deve identificar isso. Qualquer inicialização, vínculo de remote e primeiro commit ficam sob seu controle; não inicialize Git por cima de outro repo.

## Prompt inicial
Copie o prompt 1 de `docs/prompts.md`. Ele pede auditoria e um plano curto, sem implementar tudo e sem chamadas pagas.

## O que está ativo
- Instruções persistentes, regras por área e revisor opcional somente leitura.
- PreToolUse para Shell/Bash/PowerShell e edição: bloqueia comandos comuns de mutação Git, ações destrutivas óbvias, alterações em fontes originais e edição das próprias proteções.
- Regras de leitura para segredos/validação. Não existe garantia de isolamento de subprocessos só por regras do Claude.
- Nada executa automaticamente testes pagos, benchmark, instalação, commit, push, deploy ou atualização no Linear.
- Nenhum Stop hook: evita loops que consumiriam sessões só para satisfazer um check ainda inexistente.

## Se houver bloqueio
Leia a mensagem e confira `docs/security.md`. Um hook de segurança deve ser corrigido pelo responsável, não contornado pelo agente. Para testar a conexão real Claude→hook, peça somente um comando inocente (`git status` se houver repo), e depois uma tentativa explícita bloqueável em sessão supervisionada. O teste Python verifica a lógica, não substitui essa integração local.

## Commit manual
Revise o diff, `.gitignore`, fontes e segredos ANTES de escolher arquivos para o commit. Não use `git add .` às cegas: o ZIP original e dados pessoais do desafio podem estar na raiz. O README final precisa declarar uso de IA, mesmo sem coautoria automática do Claude nos commits.
