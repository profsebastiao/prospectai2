"""Diagnóstico de presença digital de uma empresa a partir das evidências da prospecção.

Mostra o que foi encontrado (redes sociais, WhatsApp, Google, site, landing page) e, para cada canal
essencial que não apareceu, o que a empresa deixa de ganhar. Usa só as evidências: um canal
"não encontrado" pode existir e deve ser confirmado antes de apresentar ao cliente.
"""
import re
from urllib.parse import urlsplit

from .busca_web import LINK_PAGES, URLS, _telefone_whatsapp
from .perfis import RedeSocial, domain, profile_identity, web_url

TELEFONE = re.compile(r"(?<![\d/])(?:\+?55[\s-]?)?\(?\d{2}\)?[\s-]?9?\s?\d{4}[-.\s]?\d{4}(?![\d/])")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
ARROBA = re.compile(r"(?<![\w@.])@([A-Za-z0-9_](?:[A-Za-z0-9_.]{1,28}[A-Za-z0-9_]))")
ENDERECO = re.compile(
    r"\b(?:Av\.?|Avenida|Rua|R\.|Rodovia|Rod\.|Travessa|Tv\.|Praça|Alameda|Estrada|BR-\d+)\s+[^\n|;]{3,90}", re.I,
)
EMOJI = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️‍]+")

CANAIS = [
    ("instagram", "Instagram"),
    ("facebook", "Facebook"),
    ("whatsapp", "WhatsApp"),
    ("google", "Google Meu Negócio (Google Maps)"),
    ("site", "Site próprio"),
    ("landing", "Landing page"),
    ("tiktok", "TikTok"),
    ("youtube", "YouTube"),
    ("linkedin", "LinkedIn"),
]
ENCURTADORES_WHATSAPP = {"wa.link", "walink.co", "wa.me", "api.whatsapp.com", "whatsapp.com", "chat.whatsapp.com"}
ESSENCIAIS = ("instagram", "facebook", "whatsapp", "google", "site", "landing")

PERDAS = {
    "site": (
        "Sem site próprio, a empresa depende só das redes sociais para ser encontrada e transmitir confiança. "
        "Quem pesquisa no Google antes de comprar procura serviços, preços, endereço e contato num só lugar; "
        "sem site, essa pessoa tende a escolher um concorrente que mostra essas informações."
    ),
    "landing": (
        "Sem uma landing page, anúncios e o link da bio levam para lugares genéricos (o perfil ou um WhatsApp sem contexto). "
        "Uma página focada numa oferta, com benefícios e um botão de contato, transforma mais visitantes em pedidos de orçamento "
        "e permite medir o resultado das campanhas."
    ),
    "google": (
        "Sem perfil no Google Meu Negócio, a empresa não aparece no Google Maps nem nas buscas do tipo \"perto de mim\", "
        "que são feitas por quem já quer comprar. Também perde as avaliações de clientes, que ajudam outras pessoas a decidir."
    ),
    "instagram": (
        "Sem Instagram ativo, a empresa perde a principal vitrine visual para mostrar trabalhos, antes e depois e novidades, "
        "e o canal onde muitos clientes da região descobrem negócios locais e mandam mensagem."
    ),
    "facebook": (
        "Sem página no Facebook, a empresa fica fora de um público que ainda pesquisa e pede indicações por lá, inclusive "
        "em grupos de bairro, e não consegue usar anúncios segmentados por região com a mesma facilidade."
    ),
    "whatsapp": (
        "Sem um WhatsApp divulgado, o cliente interessado não tem um caminho rápido para pedir orçamento e tende a desistir "
        "ou procurar outra empresa que responda na hora."
    ),
}
PALAVRAS_SERVICO = {
    "site": ("site",),
    "landing": ("landing",),
    "google": ("google", "meu negócio", "maps"),
    "instagram": ("instagram",),
    "facebook": ("facebook",),
    "whatsapp": ("whatsapp",),
}
AVISO = (
    "Diagnóstico feito somente com as evidências fornecidas. Um canal marcado como não encontrado pode existir: "
    "confirme antes de apresentar ao cliente."
)


def nome_da_fonte(fonte, padrao=""):
    """Nome da fonte ou, sem ele, a primeira linha útil do trecho (sem emojis)."""
    titulo = (fonte.get("title") or "").strip()
    if titulo:
        return titulo[:120]
    for linha in (fonte.get("content") or "").splitlines():
        linha = " ".join(EMOJI.sub(" ", linha).split()).strip(" -–—:|")
        if len(linha) >= 4:
            return (linha[:77] + "…") if len(linha) > 80 else linha
    return padrao


def _google_maps(url):
    host = domain(url)
    return host in {"maps.google.com", "maps.app.goo.gl", "g.page", "g.co"} or (
        host in {"google.com", "google.com.br"} and urlsplit(url).path.startswith("/maps")
    )


def _servico_para(codigo, servicos):
    palavras = PALAVRAS_SERVICO.get(codigo, ())
    for servico in servicos or []:
        nome = str(servico.get("nome", ""))
        if any(palavra in nome.casefold() for palavra in palavras):
            return nome
    return ""


def diagnosticar(candidato, fontes, servicos=(), cidade=""):
    textos = [f.get("content") or "" for f in fontes]
    if candidato.get("descricao"):
        textos.append(candidato["descricao"])
    texto = "\n".join(t for t in textos if t)
    # Menções no texto são procuradas fora dos links, para "www." de um link não contar como site.
    minusculo = URLS.sub(" ", texto).casefold()

    presenca = {codigo: {"codigo": codigo, "canal": nome, "itens": [], "situacao": "nao"} for codigo, nome in CANAIS}

    def adicionar(codigo, valor, url="", situacao="encontrado"):
        canal = presenca[codigo]
        if any(item["valor"] == valor for item in canal["itens"]):
            return
        canal["itens"].append({"valor": valor, "url": url})
        if situacao == "encontrado" or canal["situacao"] == "nao":
            canal["situacao"] = situacao

    urls = [f.get("url") for f in fontes if f.get("url")]
    urls += [candidato.get("fonte") or ""]
    urls += [p.get("url", "") for p in candidato.get("perfis", [])]
    urls += [match[0].rstrip(".,;:!?)") for match in URLS.finditer(texto)]
    urls += ["https://" + m[0] for m in re.finditer(r"(?<![\w/.])(?:wa\.link|wa\.me|linktr\.ee)/[\w.\-]+", texto)]
    for bruto in urls:
        url = web_url(bruto) if bruto else ""
        if not url:
            continue
        identidade = profile_identity(url)
        if identidade and identidade[0] != RedeSocial.X:
            rede, usuario, canonico = identidade
            adicionar(str(rede), f"@{usuario}" if usuario else canonico, canonico)
        elif _telefone_whatsapp(url):
            adicionar("whatsapp", _telefone_whatsapp(url), url)
        elif domain(url) in ENCURTADORES_WHATSAPP:
            adicionar("whatsapp", "Link de WhatsApp", url)
        elif _google_maps(url):
            adicionar("google", "Perfil no Google Maps", url)
        elif domain(url) in LINK_PAGES:
            continue
    for link in candidato.get("links", []):
        if link.get("tipo") == "Site do negócio":
            adicionar("site", link["url"], link["url"])
        elif link.get("tipo") == "Landing page":
            adicionar("landing", link["url"], link["url"])

    # Menções no texto, sem link: contam como "mencionado" (a confirmar).
    arrobas = [m[1] for m in ARROBA.finditer(texto)]
    if arrobas and ("instagram" in minusculo or "insta" in minusculo or not presenca["instagram"]["itens"]):
        for usuario in arrobas[:2]:
            adicionar("instagram", f"@{usuario}", f"https://instagram.com/{usuario}", "mencionado")
    if "facebook" in minusculo and not presenca["facebook"]["itens"]:
        adicionar("facebook", "Citado no texto, sem link", "", "mencionado")
    telefones = list(dict.fromkeys(" ".join(m[0].split()) for m in TELEFONE.finditer(texto)))[:3]
    for numero in (candidato.get("whatsapp"), candidato.get("telefone")):
        if numero and numero not in telefones:
            telefones.insert(0, numero)
    if not presenca["whatsapp"]["itens"] and telefones and re.search(r"whats|zap|wpp|agende|agendamento|orçamento", minusculo):
        adicionar("whatsapp", telefones[0], "", "mencionado")
    if re.search(r"\bsite\b|www\.", minusculo) and not presenca["site"]["itens"]:
        adicionar("site", "Citado no texto, sem link", "", "mencionado")
    if re.search(r"google meu neg[oó]cio|google maps|avalia[cç][oõ]es no google", minusculo) and not presenca["google"]["itens"]:
        adicionar("google", "Citado no texto, sem link", "", "mencionado")

    enderecos = list(dict.fromkeys(" ".join(EMOJI.sub(" ", m[0]).split()) for m in ENDERECO.finditer(texto)))[:2]
    emails = list(dict.fromkeys(m[0] for m in EMAIL.finditer(texto)))[:2]
    if candidato.get("email") and candidato["email"] not in emails:
        emails.insert(0, candidato["email"])
    primeira = fontes[0] if fontes else {}
    empresa = {
        "nome": candidato.get("nome") or nome_da_fonte(primeira, "Empresa pesquisada"),
        "cidade": candidato.get("cidade") or cidade,
        "enderecos": enderecos,
        "telefones": telefones,
        "emails": emails,
        "descricao": " ".join(EMOJI.sub(" ", texto).split())[:400],
    }
    canais = [
        presenca[codigo] for codigo, _ in CANAIS
        if codigo in ESSENCIAIS or presenca[codigo]["situacao"] != "nao"
    ]
    perdas = [
        {"codigo": codigo, "canal": presenca[codigo]["canal"], "texto": PERDAS[codigo],
         "servico": _servico_para(codigo, servicos)}
        for codigo in ESSENCIAIS if presenca[codigo]["situacao"] == "nao"
    ]
    encontrados = sum(1 for codigo in ESSENCIAIS if presenca[codigo]["situacao"] != "nao")
    return {
        "empresa": empresa, "canais": canais, "perdas": perdas,
        "pontuacao": {"encontrados": encontrados, "total": len(ESSENCIAIS)},
        "aviso": AVISO,
    }


def texto_relatorio(diagnostico):
    """Versão em texto do diagnóstico para o relatório editável e o download."""
    empresa = diagnostico["empresa"]
    linhas = ["DADOS DA EMPRESA", f"Nome: {empresa['nome']}"]
    if empresa.get("cidade"):
        linhas.append(f"Cidade: {empresa['cidade']}")
    for endereco in empresa.get("enderecos", []):
        linhas.append(f"Endereço: {endereco}")
    if empresa.get("telefones"):
        linhas.append("Telefone/WhatsApp: " + ", ".join(empresa["telefones"]))
    if empresa.get("emails"):
        linhas.append("E-mail: " + ", ".join(empresa["emails"]))
    pontos = diagnostico["pontuacao"]
    linhas += ["", f"PRESENÇA DIGITAL ({pontos['encontrados']} de {pontos['total']} canais essenciais encontrados)"]
    rotulos = {"encontrado": "Encontrado", "mencionado": "Citado, a confirmar", "nao": "Não encontrado"}
    for canal in diagnostico["canais"]:
        valores = ", ".join(item["url"] or item["valor"] for item in canal["itens"])
        linhas.append(f"- {canal['canal']}: {rotulos[canal['situacao']]}{f' — {valores}' if valores else ''}")
    if diagnostico["perdas"]:
        linhas += ["", "O QUE A EMPRESA ESTÁ DEIXANDO DE GANHAR"]
        for perda in diagnostico["perdas"]:
            linhas += [f"{perda['canal']}:", perda["texto"]]
            if perda["servico"]:
                linhas.append(f"Como resolver: {perda['servico']}.")
            linhas.append("")
    while linhas and linhas[-1] == "":
        linhas.pop()
    linhas += ["", diagnostico["aviso"]]
    return "\n".join(linhas).strip()
