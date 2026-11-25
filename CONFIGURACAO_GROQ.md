# Integração Groq por empresa

O ProspectAI usa a API oficial Groq para a prospecção assistida. Cada empresa conecta a própria chave em **Configurações → Integrações → Groq**. Não configure `GROQ_API_KEY`: o servidor não tem chave global nem fallback entre empresas. O CRM manual continua disponível sem integração.

## Configuração local no Windows

O lançador `Iniciar-ProspectAI.cmd` solicita a chave mestre local que protege credenciais. Para persistir o anel de chaves somente na sua conta Windows (nunca no repositório), gere Base64 de 32 bytes e configure `$env:GEMINI_ENCRYPTION_KEYS` na sessão do terminal antes de iniciar. Essa variável mantém compatibilidade com credenciais criptografadas anteriormente; apesar do nome, ela não é uma chave de API Gemini.

Configurações opcionais de ambiente:

```powershell
$env:GROQ_MODEL = "openai/gpt-oss-20b"
$env:GROQ_COMPANY_DAILY_LIMIT = "10"
$env:GROQ_GLOBAL_DAILY_LIMIT = "100"
```

A chave de criptografia precisa continuar disponível para ler credenciais salvas. Para rotação sem perda de acesso, configure temporariamente `NOVA,ANTIGA`, inicie o servidor e regrave cada credencial Groq pela tela de integração; depois retire a chave antiga. Guarde as chaves em um gerenciador seguro e fora do banco, logs, Git e arquivos de configuração compartilhados.

## Modelo e limites

O modelo inicial é `openai/gpt-oss-20b`. Cada empresa pode ajustar seu próprio limite (`GROQ_COMPANY_DAILY_LIMIT` define o padrão inicial); o teto agregado diário do servidor é `GROQ_GLOBAL_DAILY_LIMIT`. Disponibilidade, limites gratuitos e custos variam segundo a conta e o plano Groq e podem mudar. Consulte a documentação oficial: [API compatível com OpenAI](https://console.groq.com/docs/openai), [modelos](https://console.groq.com/docs/models) e [erros](https://console.groq.com/docs/errors).

A busca de navegador fica desativada por padrão. Somente um administrador da empresa pode autorizar e testar o recurso na tela de integração; o projeto Groq precisa oferecer acesso. O ProspectAI não altera faturamento. Se o recurso não estiver habilitado, cole trechos e links como evidências. A pesquisa de navegador e a disponibilidade dos modelos compatíveis podem ter limites ou custos separados; veja [Browser Search](https://console.groq.com/docs/tool-use/built-in-tools/browser-search) e [Responses API](https://console.groq.com/docs/responses-api).

## Desenvolvimento e testes

Use o Python `.venv-windows` e execute:

```powershell
.\.venv-windows\Scripts\python.exe manage.py check
.\.venv-windows\Scripts\python.exe manage.py test apps.accounts.test_gemini_integracao apps.leads.test_gemini apps.leads.test_prospeccao
```

Os testes simulam o transporte HTTP e usam chaves fictícias. Para validar uma chamada real, cada administrador deve configurar uma chave de teste na interface de sua empresa. Não use chaves enviadas por chat nem coloque segredos em prompts ou relatórios.
