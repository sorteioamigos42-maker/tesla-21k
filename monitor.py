import html
import json
import os
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

URLS = {
    "PiscaPisca": "https://www.piscapisca.pt/carros/tesla/model-3?km-ate=100000&ano-de=2021&preco-ate=23000",
    "Standvirtual": "https://www.standvirtual.com/carros/tesla/model-3/desde-2021?search%5Bfilter_float_mileage%3Ato%5D=126000&search%5Bfilter_float_price%3Ato%5D=25000",
}

FICHEIRO_VISTOS = "vistos.json"


def carregar_vistos():
    if not os.path.exists(FICHEIRO_VISTOS):
        return {}, True
    with open(FICHEIRO_VISTOS, "r", encoding="utf-8") as f:
        return json.load(f), False


def guardar_vistos(vistos):
    with open(FICHEIRO_VISTOS, "w", encoding="utf-8") as f:
        json.dump(vistos, f, ensure_ascii=False, indent=2)


def extrair_detalhes(texto):
    texto_limpo = re.sub(r"\s+", " ", texto or "").strip()
    preco_m = re.search(r"(\d{1,3}(?:[\s\.]\d{3})+\s?€|\d{4,6}\s?€)", texto_limpo)
    km_m = re.search(r"(\d{1,3}(?:[\s\.]\d{3})*\s?km)", texto_limpo, re.IGNORECASE)
    ano_m = re.search(r"\b(202[0-9])\b", texto_limpo)

    return {
        "preco": preco_m.group(1).strip() if preco_m else "Preço n/d",
        "km": km_m.group(1).strip() if km_m else "",
        "ano": ano_m.group(1) if ano_m else "",
    }


def extrair_standvirtual(page, url):
    anuncios = {}
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_selector('a[href*="/carros/anuncio/"]', timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(3000)
    page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
    page.wait_for_timeout(1500)

    itens = page.evaluate("""
        () => {
            const links = Array.from(document.querySelectorAll('a[href*="/carros/anuncio/"]'));
            const resultados = [];
            for (const a of links) {
                const href = a.getAttribute('href');
                if (!href) continue;
                let card = a.closest('article') || a.parentElement;
                let passos = 0;
                while (card && card.tagName !== 'ARTICLE' && passos < 5) {
                    if ((card.innerText || '').includes('€') && card.innerText.length < 700) break;
                    card = card.parentElement;
                    passos++;
                }
                const textoCard = card ? card.innerText : a.innerText;
                const heading = card ? card.querySelector('h1, h2, h3') : null;
                const titulo = (heading ? heading.innerText : a.innerText || '').trim();
                resultados.push({ href, titulo, textoCard });
            }
            return resultados;
        }
    """)

    for item in itens:
        link = urljoin("https://www.standvirtual.com", item["href"].split("?")[0])
        match_id = re.search(r"-ID([A-Za-z0-9]+)\.html", link)
        ad_id = f"SV_{match_id.group(1)}" if match_id else f"SV_{link}"
        if ad_id in anuncios:
            continue

        detalhes = extrair_detalhes(item["textoCard"])
        titulo = item["titulo"].splitlines()[0].strip() if item["titulo"] else "Tesla Model 3 (Standvirtual)"
        anuncios[ad_id] = {
            "site": "Standvirtual",
            "titulo": titulo,
            "link": link,
            **detalhes,
        }
    return anuncios


def extrair_piscapisca(page, url):
    anuncios = {}
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_selector('a[href*="/carros/usados/"]', timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(4000)
    page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
    page.wait_for_timeout(1500)

    itens = page.evaluate("""
        () => {
            const links = Array.from(document.querySelectorAll('a[href*="/carros/usados/"]'));
            const resultados = [];
            for (const a of links) {
                const href = a.getAttribute('href');
                if (!href) continue;
                let card = a.parentElement;
                let passos = 0;
                while (card && passos < 6) {
                    const txt = card.innerText || '';
                    if (txt.includes('€') && txt.length < 700) break;
                    card = card.parentElement;
                    passos++;
                }
                const textoCard = card ? card.innerText : a.innerText;
                const heading = card ? card.querySelector('h1, h2, h3, h4') : null;
                const titulo = (heading ? heading.innerText : a.innerText || '').trim();
                resultados.push({ href, titulo, textoCard });
            }
            return resultados;
        }
    """)

    for item in itens:
        link = urljoin("https://www.piscapisca.pt", item["href"].split("?")[0])
        partes = [p for p in link.rstrip("/").split("/") if p]
        if len(partes) < 2:
            continue
        ad_id = f"PP_{partes[-1]}"
        if ad_id in anuncios:
            continue

        detalhes = extrair_detalhes(item["textoCard"])
        linhas = [l.strip() for l in item["titulo"].splitlines() if l.strip()]
        titulo = linhas[0] if linhas else "Tesla Model 3 (PiscaPisca)"
        anuncios[ad_id] = {
            "site": "PiscaPisca",
            "titulo": titulo,
            "link": link,
            **detalhes,
        }
    return anuncios


def enviar_email(novos, primeira_execucao=False):
    remetente = os.environ["GMAIL_USER"].strip()
    password = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "").strip()
    destino = os.environ["EMAIL_DESTINO"].strip()

    if primeira_execucao:
        assunto = f"[Ativo] Monitor iniciado - {len(novos)} anúncios encontrados"
        intro = "O teu monitor de carros ficou ativo. Estes são os anúncios atualmente disponíveis nos teus filtros (a partir de agora só recebes emails quando surgirem novidades):"
    else:
        assunto = f"🚗 {len(novos)} novo(s) Tesla Model 3 encontrado(s)!"
        intro = "Acabaram de aparecer novos anúncios dentro dos critérios que definiste:"

    blocos_html = []
    linhas_texto = [intro, ""]
    for ad in novos:
        meta = " • ".join(p for p in [ad["preco"], ad["ano"], ad["km"]] if p)
        blocos_html.append(
            f"""
            <div style="border:1px solid #ddd;border-radius:8px;padding:12px;margin-bottom:12px;font-family:Arial,sans-serif;">
              <div style="font-size:12px;color:#666;font-weight:bold;">{html.escape(ad['site'])}</div>
              <div style="font-size:16px;font-weight:bold;margin:4px 0;">{html.escape(ad['titulo'])}</div>
              <div style="font-size:14px;color:#111;margin-bottom:8px;">{html.escape(meta)}</div>
              <a href="{html.escape(ad['link'])}" style="display:inline-block;background:#1a73e8;color:#fff;text-decoration:none;padding:8px 14px;border-radius:5px;font-size:13px;">Ver anúncio</a>
            </div>
            """
        )
        linhas_texto.append(f"[{ad['site']}] {ad['titulo']} | {meta}\n{ad['link']}\n")

    corpo_html = f"<p style='font-family:Arial,sans-serif;'>{intro}</p>" + "".join(blocos_html)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = assunto
    msg["From"] = remetente
    msg["To"] = destino
    msg.attach(MIMEText("\n".join(linhas_texto), "plain", "utf-8"))
    msg.attach(MIMEText(corpo_html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as servidor:
        servidor.login(remetente, password)
        servidor.sendmail(remetente, destino, msg.as_string())


def main():
    vistos, primeira_execucao = carregar_vistos()
    atuais = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="pt-PT",
            viewport={"width": 1366, "height": 900},
        )
        page = context.new_page()

        try:
            sv = extrair_standvirtual(page, URLS["Standvirtual"])
            print(f"Standvirtual: {len(sv)} anúncios encontrados.")
            atuais.update(sv)
        except Exception as e:
            print(f"Erro no Standvirtual: {e}")

        try:
            pp = extrair_piscapisca(page, URLS["PiscaPisca"])
            print(f"PiscaPisca: {len(pp)} anúncios encontrados.")
            atuais.update(pp)
        except Exception as e:
            print(f"Erro no PiscaPisca: {e}")

        browser.close()

    novos = [dados for ad_id, dados in atuais.items() if ad_id not in vistos]

    if novos:
        print(f"A enviar email com {len(novos)} novo(s) anúncio(s)...")
        enviar_email(novos, primeira_execucao=primeira_execucao)
        vistos.update(atuais)
        guardar_vistos(vistos)
    else:
        print("Sem anúncios novos nesta verificação.")
        if primeira_execucao and not os.path.exists(FICHEIRO_VISTOS):
            guardar_vistos(vistos)


if __name__ == "__main__":
    main()
