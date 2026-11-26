import re
from urllib.parse import parse_qs, urlsplit

from django.core.validators import validate_email
from django.core.exceptions import ValidationError

from .ia import IAIndisponivel, pesquisar
from .perfis import HOSTS, RedeSocial, domain, profile_identity, web_url


LINK_PAGES = {"linktr.ee", "beacons.ai", "bio.site", "lnk.bio", "linkin.bio", "msha.ke", "taplink.cc"}
URLS = re.compile(r"https?://[^\s<>\[\]\"']+", re.I)
COMMERCIAL_EMAIL = re.compile(
    r"(?:e-mail comercial|e-mail|email|contato comercial|contato)\s*[:–-]\s*([\w.+-]+@[\w.-]+\.[A-Za-z]{2,})",
    re.I,
)
COMMERCIAL_PHONE = re.compile(
    r"(?:telefone|tel\.?|ligue|whatsapp)\s*[:–-]\s*(\+?\d[\d ()-]{7,23}\d)", re.I
)
LINK_EVIDENCE = re.compile(
    r"(site oficial|página oficial|official website|website oficial|landing page|página de destino)"
    r"\s*[:–-]?\s*(?:\[\s*)?(?:\]\()?<?(https?://[^\s<>\[\]\"']+)", re.I
)


class BuscaIndisponivel(Exception):
    pass


def buscar(query, *, workspace_id):
    try:
        return pesquisar(query, workspace_id)
    except IAIndisponivel as exc:
        raise BuscaIndisponivel(str(exc)) from exc


def _url(text):
    return web_url(text.rstrip(".,;:!?)"))


def _telefone_whatsapp(url):
    parsed = urlsplit(url)
    host = domain(url)
    path = parsed.path.strip("/")
    if host == "wa.me":
        numero = path
    elif host in {"api.whatsapp.com", "web.whatsapp.com", "whatsapp.com"} and path == "send":
        numero = parse_qs(parsed.query).get("phone", [""])[0].lstrip("+")
    else:
        return ""
    return f"+{numero}" if re.fullmatch(r"\d{8,15}", numero) else ""


def _classificar(url, rotulo, fonte):
    host = domain(url)
    if _telefone_whatsapp(url):
        tipo, evidencia = "WhatsApp direto", "Domínio do WhatsApp e número na URL."
    elif host in LINK_PAGES and urlsplit(url).path.strip("/"):
        tipo, evidencia = "Página de links", f"Perfil em um agregador de links ({host})."
    elif rotulo and host not in HOSTS and host not in LINK_PAGES and "whatsapp" not in host:
        if rotulo.casefold() in {"landing page", "página de destino"}:
            tipo = "Landing page"
        else:
            tipo = "Site do negócio"
        evidencia = f'A fonte apresenta esse endereço como “{rotulo}”.'
    else:
        tipo, evidencia = "Revisão manual", "A fonte não identifica com segurança o tipo da página."
    return {"url": url, "tipo": tipo, "evidencia": evidencia, "fonte": fonte}


def _text_field(text, label):
    match = re.search(rf"(?i)(?:^|[\s.;|])(?:{label})\s*:\s*([^;\n|.]+)", text)
    return match[1].strip()[:120] if match else ""


def _perfil(url, fonte, evidencia):
    identity = profile_identity(url)
    if not identity:
        return None
    rede, usuario, canonical = identity
    return {
        "rede": rede.value, "nome": rede.label, "usuario": usuario,
        "url": canonical, "fonte": fonte, "evidencia": evidencia,
    }


def candidatos(results, cidade_consultada=""):
    encontrados = []
    fontes = set()
    for item in results:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            continue
        fonte = web_url(item["url"])
        if not fonte or len(fonte) > 500:
            continue
        principal = _perfil(fonte, fonte, "URL pública retornada na pesquisa.")
        chave = principal["url"] if principal else fonte
        if chave in fontes:
            continue
        fontes.add(chave)

        titulo = item.get("title")
        titulo = titulo.strip()[:200] if isinstance(titulo, str) else ""
        trecho = item.get("content")
        trecho = trecho.strip()[:2200] if isinstance(trecho, str) else ""
        rotulos = {
            _url(match[2]): match[1] for match in LINK_EVIDENCE.finditer(trecho)
        }
        urls = dict.fromkeys([fonte, *(_url(match[0]) for match in URLS.finditer(trecho))])
        perfis_ligados = {}
        links = []
        for url in urls:
            if not url or len(url) > 500:
                continue
            perfil = _perfil(url, fonte, "A URL do perfil aparece explicitamente nesta fonte.")
            if perfil:
                perfis_ligados.setdefault(perfil["url"], perfil)
            else:
                links.append(_classificar(url, rotulos.get(url), fonte))
        if principal:
            perfis_ligados[principal["url"]] = principal

        cidade = _text_field(trecho, "cidade|localização|localidade")
        if not cidade and cidade_consultada and re.search(r"(?<!\w)" + re.escape(cidade_consultada) + r"(?!\w)", trecho, re.I):
            cidade = cidade_consultada
        email_match = COMMERCIAL_EMAIL.search(trecho)
        email = email_match[1].rstrip(".") if email_match else ""
        if email:
            try:
                validate_email(email)
            except ValidationError:
                email = ""
        phone_match = COMMERCIAL_PHONE.search(trecho)
        whatsapp = next((number for url in urls if url and (number := _telefone_whatsapp(url))), "")
        telefone = phone_match[1].strip() if phone_match else ""
        if phone_match and phone_match[0].lower().startswith("whatsapp"):
            whatsapp = whatsapp or telefone
            telefone = ""
        encontrados.append({
            "fonte": fonte, "titulo": titulo or domain(fonte), "descricao": trecho,
            "nome": re.split(r"\s+[|–—]\s+|\s+-\s+(?:Instagram|Facebook|TikTok|LinkedIn|YouTube)\b", titulo, maxsplit=1, flags=re.I)[0],
            "segmento": _text_field(trecho, "segmento|categoria|ramo"),
            "cidade": cidade, "email": email[:254], "telefone": telefone[:30], "whatsapp": whatsapp[:30],
            "perfis": list(perfis_ligados.values()), "principal": principal,
            "links": links,
        })

    # Shared official URL is evidence only when each candidate source labels it as such.
    por_site = {}
    for candidato in encontrados:
        for link in candidato["links"]:
            if link["tipo"] == "Site do negócio":
                por_site.setdefault(link["url"], []).append(candidato)
    for grupo in por_site.values():
        principais = [c["principal"] for c in grupo if c["principal"]]
        for candidato in grupo:
            existentes = {p["url"] for p in candidato["perfis"]}
            for perfil in principais:
                if perfil["url"] not in existentes:
                    candidato["perfis"].append({
                        **perfil, "evidencia": "As duas fontes indicam o mesmo endereço como site oficial.",
                    })
                    existentes.add(perfil["url"])
    return encontrados
