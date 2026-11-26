# Integração Gemini por empresa

O ProspectAI usa a API Gemini (Google AI Studio) como provedor padrão da prospecção com IA. Cada empresa conecta a própria chave em **Configurações → Integrações**. Não existe chave global no servidor.

## Conectar

1. Crie uma chave em <https://aistudio.google.com/apikey>.
2. No ProspectAI, abra **Configurações → Integrações**, cole a chave e clique em **Testar e salvar** (ou **Testar e substituir**, se já houver uma chave Groq salva).
3. A chave antiga só é trocada se a nova for aprovada no teste.

A chave fica criptografada com a chave mestre (`GEMINI_ENCRYPTION_KEYS`) informada ao iniciar o `Iniciar-ProspectAI.cmd`. Use sempre a mesma chave mestre.

## Configurações de ambiente

```powershell
$env:IA_PROVEDOR = "gemini"            # provedor das chaves novas: gemini ou groq
$env:GEMINI_MODEL = "gemini-3.8-flash" # modelo usado nas chamadas
```

Empresas que ainda têm chave Groq salva continuam usando a Groq até a chave ser substituída. Para voltar a cadastrar chaves Groq, use `IA_PROVEDOR = "groq"` (veja [CONFIGURACAO_GROQ.md](CONFIGURACAO_GROQ.md)).

## Busca no Google

Desativada por padrão. Um administrador da empresa pode testar e habilitar na tela de integração. Os links da busca chegam como redirecionamentos do Google; o ProspectAI lê apenas o endereço de destino de cada um, sem abrir a página. As sugestões de pesquisa do Google aparecem junto dos resultados, como exigem os termos da API. A busca pode ter cota e custo próprios no projeto do Google; consulte <https://ai.google.dev/gemini-api/docs/pricing>.

## Depois de atualizar o código

O servidor não recarrega o código sozinho. Rode `Parar-ProspectAI.cmd` e depois `Iniciar-ProspectAI.cmd`.
