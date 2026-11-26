# Integração OpenRouter por empresa

O ProspectAI usa a API do OpenRouter na prospecção com IA. Cada empresa conecta a própria chave em **Configurações → Integrações**. Não existe chave global no servidor.

## Conectar

1. Crie uma chave em <https://openrouter.ai/keys> (começa com `sk-or-`).
2. No ProspectAI, abra **Configurações → Integrações**, cole a chave e clique em **Testar e salvar** (ou **Testar e substituir**, se já houver uma chave salva).
3. A chave antiga só é trocada se a nova for aprovada no teste.

A chave fica criptografada com a chave mestre (`GEMINI_ENCRYPTION_KEYS`, nome mantido por compatibilidade) informada ao iniciar o `Iniciar-ProspectAI.cmd`. Use sempre a mesma chave mestre.

Na primeira vez, o lançador pergunta a chave mestre e oferece guardá-la neste computador. Ela fica em `%LOCALAPPDATA%\ProspectAI\chave-mestre.dat`, criptografada pela sua conta do Windows (DPAPI): só abre no mesmo usuário e no mesmo computador, e não vai para o projeto nem para o GitHub. Para apagar a chave guardada e digitar de novo, rode `Iniciar-ProspectAI.cmd -EsquecerChave`.

## Modelo

O padrão é `openrouter/free`, o roteador gratuito do OpenRouter: a cada chamada ele escolhe um modelo gratuito que suporte respostas estruturadas. Modelos gratuitos têm limite de chamadas e alguns guardam os textos enviados. Para usar um modelo específico (pago ou gratuito), defina antes de iniciar:

```powershell
$env:OPENROUTER_MODEL = "openrouter/free"            # ou, por exemplo, "google/gemini-3.6-flash"
$env:OPENROUTER_FALLBACK_MODELS = ""                  # reservas opcionais, separadas por vírgula
$env:IA_COMPANY_DAILY_LIMIT = "10"                    # limite diário inicial de cada empresa
$env:IA_GLOBAL_DAILY_LIMIT = "100"                    # teto diário somando todas as empresas
```

Veja os modelos e preços em <https://openrouter.ai/models>.

## Busca na web

Desativada por padrão. Usa o plugin de busca do OpenRouter, que devolve as fontes com links reais. A busca é cobrada pelo OpenRouter mesmo com modelos gratuitos, então a conta precisa de créditos. Um administrador da empresa testa e habilita na tela de integração.

## Depois de atualizar o código

O servidor não recarrega o código sozinho. Rode `Parar-ProspectAI.cmd` e depois `Iniciar-ProspectAI.cmd`.
