"""
Descubre URLs (sitemap + BFS), descarga hasta MAX_PAGES por dominio, extrae
texto y metadatos, obtiene logo y captura screenshots para auditoria visual.

Uso: python scripts/scrape_competitors.py
Salida: dashboard/content_audit.json + dashboard/logos/* + dashboard/screenshots/*
"""
from __future__ import annotations

import json
import re
import ssl
import time
from collections import Counter
from collections import deque
from pathlib import Path
from urllib import error as urlerr
from urllib.parse import quote, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
LOGO_DIR = DASH / "logos"
SCREENSHOT_DIR = DASH / "screenshots"
PROFILE_PATH = ROOT / "scripts" / "competitors_profile.json"

# Límite razonable: cubre home, menú principal y secciones clave sin crawlear blogs enteros
MAX_PAGES_PER_DOMAIN = 50
MAX_SCREENSHOTS_PER_DOMAIN = 12
REQUEST_DELAY_S = 0.45
TIMEOUT_S = 22
TEXT_MAX_CHARS = 18000
# Cabecera tipo navegador: muchos sitios bloquean respuestas HTML si el UA no es reconocible
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# Tamaño máximo logo guardado (px lado mayor)
LOGO_MAX_EDGE = 96

_ssl = ssl.create_default_context()
_playwright_ctx = None


def _slugify_text(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned[:70] or "page"


def _playwright_getter():
    global _playwright_ctx
    if _playwright_ctx is not None:
        return _playwright_ctx
    try:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        _playwright_ctx = {"pw": pw, "browser": browser}
    except Exception:
        _playwright_ctx = {"pw": None, "browser": None}
    return _playwright_ctx


def fetch_text_browser(url: str) -> str | None:
    """Fallback con navegador real para sitios anti-bot / JS."""
    ctx = _playwright_getter()
    browser = ctx.get("browser")
    if not browser:
        return None
    page = None
    try:
        page = browser.new_page(viewport={"width": 1366, "height": 2200})
        page.goto(url, wait_until="domcontentloaded", timeout=35_000)
        page.wait_for_timeout(1200)
        html = page.content()
        return html if html and len(html) > 200 else None
    except Exception:
        return None
    finally:
        if page:
            try:
                page.close()
            except Exception:
                pass


def load_domains() -> list[str]:
    data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    return sorted(data.get("profiles", {}).keys())


def domain_slug(domain: str) -> str:
    return domain.replace(".", "_")


def same_domain(url: str, base_netloc: str) -> bool:
    try:
        p = urlparse(url)
        return p.netloc.lower().rstrip(".") == base_netloc.lower().rstrip(".")
    except Exception:
        return False


def fetch_bytes(url: str) -> tuple[bytes | None, str | None]:
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=TIMEOUT_S, context=_ssl) as r:
            ct = r.headers.get("Content-Type", "")
            return r.read(), ct
    except (urlerr.URLError, OSError, ValueError):
        return None, None


def fetch_text(url: str) -> str | None:
    raw, ct = fetch_bytes(url)
    if not raw:
        return None
    enc = "utf-8"
    if ct and "charset=" in ct.lower():
        m = re.search(r"charset=([\w-]+)", ct, re.I)
        if m:
            enc = m.group(1).strip()
    try:
        return raw.decode(enc, errors="replace")
    except Exception:
        return raw.decode("utf-8", errors="replace")


def can_fetch(rp, url: str) -> bool:
    if rp is None:
        return True
    try:
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        return True


def get_robots_parser(base_url: str):
    parsed = urlparse(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = None
    try:
        from urllib.robotparser import RobotFileParser

        rp = RobotFileParser()
        rp.set_url(robots_url)
        # Parse manual para evitar falsos "deny all" que ocurren en algunos
        # robots válidos cuando RobotFileParser.read() falla silenciosamente.
        txt = fetch_text(robots_url)
        if txt:
            rp.parse(txt.splitlines())
        else:
            rp.read()
    except Exception:
        rp = None
    return rp


def _norm_url(u: str) -> str:
    u = re.sub(r"#.*$", "", u).strip()
    return u.rstrip("/") if u.count("/") > 2 else u


def _safe_url(u: str) -> str:
    """Codifica espacios y caracteres raros en el path (evita fallos urllib)."""
    try:
        p = urlparse(u)
        if not p.scheme or not p.netloc:
            return u
        segs = p.path.split("/")
        fixed = "/".join(quote(s, safe="") for s in segs)
        if not fixed.startswith("/"):
            fixed = "/" + fixed
        return urlunparse((p.scheme, p.netloc, fixed, p.params, p.query, p.fragment))
    except Exception:
        return u


def _skippable_path(low: str) -> bool:
    if "/cdn-cgi/" in low:
        return True
    return any(
        low.endswith(x)
        for x in (
            ".pdf",
            ".zip",
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".webp",
            ".svg",
            ".mp4",
            ".css",
            ".js",
            ".woff",
            ".woff2",
        )
    )


def _loc_urls_from_xml(xml_text: str) -> list[str]:
    return [x.strip() for x in re.findall(r"<loc>\s*([^<]+)\s*</loc>", xml_text, re.I)]


def discover_urls(seed: str, rp) -> list[str]:
    parsed = urlparse(seed)
    base_netloc = parsed.netloc
    seen: set[str] = set()
    ordered: list[str] = []

    def add(u: str) -> bool:
        u = _norm_url(u)
        if not u or not u.startswith("http"):
            return False
        if not same_domain(u, base_netloc):
            return False
        if _skippable_path(u.lower()):
            return False
        if u in seen:
            return False
        if not can_fetch(rp, u):
            return False
        seen.add(u)
        ordered.append(u)
        return True

    add(seed)

    # Sitemaps (regex + sub-sitemaps)
    for sm in (
        urljoin(seed, "/sitemap.xml"),
        urljoin(seed, "/sitemap_index.xml"),
        urljoin(seed, "/wp-sitemap.xml"),
    ):
        if len(ordered) >= MAX_PAGES_PER_DOMAIN:
            break
        time.sleep(REQUEST_DELAY_S)
        xml_text = fetch_text(sm)
        if not xml_text or "<html" in xml_text[:300].lower():
            continue
        locs = _loc_urls_from_xml(xml_text)
        if any("sitemap" in x.lower() and x.endswith(".xml") for x in locs[:5]):
            for sub in locs[:15]:
                if len(ordered) >= MAX_PAGES_PER_DOMAIN:
                    break
                if not sub.endswith(".xml"):
                    continue
                time.sleep(REQUEST_DELAY_S * 0.7)
                sub_xml = fetch_text(sub)
                if sub_xml:
                    for loc in _loc_urls_from_xml(sub_xml):
                        add(_safe_url(loc))
                        if len(ordered) >= MAX_PAGES_PER_DOMAIN:
                            break
        else:
            for loc in locs:
                add(_safe_url(loc))
                if len(ordered) >= MAX_PAGES_PER_DOMAIN:
                    break

    # BFS desde home para completar hasta MAX_PAGES
    q = deque([seed])
    while q and len(ordered) < MAX_PAGES_PER_DOMAIN:
        u = q.popleft()
        time.sleep(REQUEST_DELAY_S)
        html = fetch_text(u)
        if not html:
            html = fetch_text_browser(u)
        if not html:
            continue
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "lxml")
        except Exception:
            continue
        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            if not href or href.startswith("#") or href.lower().startswith("javascript:"):
                continue
            abs_u = _safe_url(urljoin(u, href))
            if add(abs_u):
                q.append(abs_u)
            if len(ordered) >= MAX_PAGES_PER_DOMAIN:
                break

    return ordered[:MAX_PAGES_PER_DOMAIN]


def extract_visible_text(html: str) -> str:
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript", "iframe"]):
            tag.decompose()
        t = soup.get_text(separator=" ", strip=True)
        return re.sub(r"\s+", " ", t)[:TEXT_MAX_CHARS]
    except Exception:
        return ""


def classify_page_type(url: str, title: str, h1: str) -> str:
    low = f"{url} {title} {h1}".lower()
    rules = [
        ("contacto", ("contact", "contacto", "cotiza", "quote")),
        ("servicio", ("servicio", "services", "instalacion", "mantenimiento")),
        ("producto", ("producto", "products", "catalogo", "linea")),
        ("proyectos", ("proyectos", "project", "portafolio", "obras")),
        ("nosotros", ("nosotros", "about", "empresa", "quienes-somos")),
        ("blog", ("blog", "noticias", "news", "articulo")),
    ]
    for label, kws in rules:
        if any(k in low for k in kws):
            return label
    return "home" if re.fullmatch(r"https?://[^/]+/?", url.lower()) else "general"


def is_soft_404(page_data: dict) -> bool:
    t = (page_data.get("title") or "").lower()
    h1 = (page_data.get("h1") or "").lower()
    txt = (page_data.get("textPreview") or "").lower()
    bad_signals = ("page not found", "404", "not found", "página no encontrada", "pagina no encontrada")
    return any(s in t or s in h1 for s in bad_signals) or (("not found" in txt or "404" in txt) and len(txt) < 240)


def find_ctas(text: str) -> list[str]:
    cta_map = {
        "cotizacion": ("cotiza", "cotizacion", "presupuesto", "quote"),
        "contacto": ("contacto", "contactanos", "contact us"),
        "asesoria": ("asesoria", "asesoria", "consulta", "consultoria"),
        "whatsapp": ("whatsapp", "wa.me"),
        "llamada": ("llamanos", "telefono", "call now"),
        "visita": ("agenda", "visita", "appointment"),
    }
    low = text.lower()
    found = [label for label, kws in cta_map.items() if any(k in low for k in kws)]
    return found[:6]


def extract_page_meta(html: str, url: str) -> dict:
    out = {
        "title": "",
        "h1": "",
        "h2": [],
        "metaDescription": "",
        "canonical": None,
        "textPreview": "",
        "textFull": "",
        "ctaSignals": [],
        "pageType": "general",
    }
    text = extract_visible_text(html)
    out["textPreview"] = text[:500]
    # Mantener más contexto para capturar footer/contacto en sitios single-page.
    out["textFull"] = text[:14000]
    out["ctaSignals"] = find_ctas(text)
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        out["title"] = (soup.title.string or "").strip()[:300] if soup.title else ""
        h1 = soup.find("h1")
        out["h1"] = h1.get_text(" ", strip=True)[:220] if h1 else ""
        out["h2"] = [x.get_text(" ", strip=True)[:180] for x in soup.find_all("h2")[:8] if x.get_text(" ", strip=True)]
        md = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
        if md and md.get("content"):
            out["metaDescription"] = md.get("content", "").strip()[:380]
        can = soup.find("link", rel=lambda v: v and "canonical" in str(v).lower())
        if can and can.get("href"):
            out["canonical"] = urljoin(url, can.get("href").strip())
    except Exception:
        pass
    out["pageType"] = classify_page_type(url, out["title"], out["h1"])
    return out


def extract_home_sections(html: str, home_url: str) -> list[dict]:
    """
    Extrae secciones internas de una single-page como pseudo-paginas:
    home#servicios, home#proyecto, etc.
    """
    sections: list[dict] = []
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return sections

    section_ids: list[str] = []
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if href.startswith("#") and len(href) > 1:
            sid = href[1:].strip()
            if sid and sid not in section_ids:
                section_ids.append(sid)

    # Fallback: IDs comunes aunque no estén linkeados en anchors.
    if not section_ids:
        for tag in soup.find_all(["section", "div"], id=True):
            sid = (tag.get("id") or "").strip()
            if sid and len(sid) <= 40 and sid not in section_ids:
                section_ids.append(sid)

    generic_ids = {"main", "content", "wrapper", "page", "app", "root", "nav-scroller", "masthead"}
    for sid in section_ids[:20]:
        if sid.lower() in generic_ids:
            continue
        node = soup.find(id=sid)
        if not node:
            continue
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
        if len(text) < 30:
            continue
        title = ""
        h = node.find(["h1", "h2", "h3"])
        if h:
            title = h.get_text(" ", strip=True)[:220]
        ptype = classify_page_type(f"{home_url}#{sid}", title, title)
        if ptype == "general":
            # Mapea ids comunes para mejorar lectura.
            low = sid.lower()
            if any(k in low for k in ("serv", "service")):
                ptype = "servicio"
            elif any(k in low for k in ("prod", "catalog")):
                ptype = "producto"
            elif any(k in low for k in ("proy", "obra", "portaf")):
                ptype = "proyectos"
            elif any(k in low for k in ("contac", "quote", "cotiz")):
                ptype = "contacto"
            elif any(k in low for k in ("nosotros", "about", "quienes")):
                ptype = "nosotros"
        sections.append(
            {
                "url": f"{home_url}#{sid}",
                "title": title or sid.replace("-", " ").title(),
                "h1": title or "",
                "h2": [],
                "metaDescription": "",
                "canonical": home_url,
                "textPreview": text[:500],
                "textFull": text[:3000],
                "ctaSignals": find_ctas(text),
                "pageType": ptype,
                "status": "ok",
                "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "isSectionFromHome": True,
            }
        )
    return sections


def screenshot_and_visual(url: str, domain_slug_value: str, idx: int) -> tuple[str | None, dict]:
    """
    Captura screenshot y extrae resumen visual basico:
    - paleta dominante
    - brillo medio
    - familias tipograficas CSS reportadas
    """
    rel = None
    visual = {"dominantColors": [], "avgBrightness": None, "fontFamilies": []}
    ctx = _playwright_getter()
    browser = ctx.get("browser")
    if not browser:
        return rel, visual
    try:
        page = browser.new_page(viewport={"width": 1366, "height": 2200})
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(1400)
        page_file = f"{idx:02d}_{_slugify_text(urlparse(url).path or 'home')}.png"
        out_dir = SCREENSHOT_DIR / domain_slug_value
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / page_file
        page.screenshot(path=str(out_path), full_page=True)
        rel = str(out_path.relative_to(DASH)).replace("\\", "/")
        try:
            fonts = page.evaluate(
                """() => {
                    const arr = [];
                    const all = document.querySelectorAll("body, body *");
                    for (let i = 0; i < all.length && i < 450; i++) {
                      const f = window.getComputedStyle(all[i]).fontFamily || "";
                      if (f) arr.push(f.split(",")[0].replace(/["']/g, "").trim());
                    }
                    return arr;
                }"""
            )
            if fonts:
                top_fonts = Counter([f for f in fonts if f]).most_common(4)
                visual["fontFamilies"] = [f for f, _ in top_fonts]
        except Exception:
            pass
        page.close()
        visual.update(extract_visual_from_image(out_path))
        return rel, visual
    except Exception:
        return rel, visual


def extract_visual_from_image(path: Path) -> dict:
    try:
        from PIL import Image
    except Exception:
        return {"dominantColors": [], "avgBrightness": None}
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            small = im.resize((220, max(120, int(im.height * 220 / max(1, im.width)))))
            quant = small.quantize(colors=6).convert("RGB")
            colors = quant.getcolors(maxcolors=256) or []
            colors = sorted(colors, key=lambda x: x[0], reverse=True)[:5]
            total = sum(c for c, _ in colors) or 1
            dom = []
            for count, rgb in colors:
                dom.append(
                    {
                        "hex": "#{:02x}{:02x}{:02x}".format(*rgb),
                        "share": round(count / total, 3),
                    }
                )
            bright_pixels = list(small.getdata())
            avg_b = round(
                sum((px[0] * 0.299 + px[1] * 0.587 + px[2] * 0.114) for px in bright_pixels)
                / max(1, len(bright_pixels)),
                1,
            )
            return {"dominantColors": dom, "avgBrightness": avg_b}
    except Exception:
        return {"dominantColors": [], "avgBrightness": None}


def aggregate_visual_summary(pages: list[dict]) -> dict:
    color_counter: Counter[str] = Counter()
    font_counter: Counter[str] = Counter()
    brightness_vals: list[float] = []
    for p in pages:
        v = p.get("visual") or {}
        for c in v.get("dominantColors") or []:
            hx = c.get("hex")
            sh = float(c.get("share") or 0)
            if hx:
                color_counter[hx] += sh
        for f in v.get("fontFamilies") or []:
            font_counter[f] += 1
        b = v.get("avgBrightness")
        if b is not None:
            brightness_vals.append(float(b))
    top_colors = [{"hex": k, "weight": round(v, 3)} for k, v in color_counter.most_common(5)]
    top_fonts = [k for k, _ in font_counter.most_common(4)]
    return {
        "screenshotsCaptured": sum(1 for p in pages if p.get("screenshot")),
        "dominantColors": top_colors,
        "fontFamilies": top_fonts,
        "avgBrightness": round(sum(brightness_vals) / len(brightness_vals), 1) if brightness_vals else None,
    }


def icon_candidates(soup, base_url: str) -> list[tuple[int, str]]:
    """(prioridad menor = mejor), url"""
    found: list[tuple[int, str]] = []
    for link in soup.find_all("link"):
        rel = " ".join(link.get("rel") or []).lower()
        href = link.get("href")
        if not href:
            continue
        abs_u = urljoin(base_url, href)
        prio = 50
        if "apple-touch-icon" in rel:
            prio = 5
        elif "icon" in rel and "mask" not in rel:
            sizes = link.get("sizes") or ""
            if "192" in sizes or "180" in sizes:
                prio = 8
            elif "32" in sizes or "48" in sizes:
                prio = 15
            else:
                prio = 20
        elif "shortcut" in rel:
            prio = 25
        else:
            continue
        found.append((prio, abs_u))
    found.sort(key=lambda x: x[0])
    return found


def og_image(soup, base_url: str) -> str | None:
    for prop in ("og:image", "og:image:url"):
        m = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        if m and m.get("content"):
            return urljoin(base_url, m["content"].strip())
    return None


def save_logo_image(raw: bytes, dest: Path) -> str | None:
    ext = dest.suffix.lower()
    if ext == ".svg":
        dest.write_bytes(raw)
        return str(dest.relative_to(DASH)).replace("\\", "/")

    try:
        from io import BytesIO

        from PIL import Image

        im = Image.open(BytesIO(raw))
        if im.mode in ("RGBA", "P"):
            im = im.convert("RGBA")
        else:
            im = im.convert("RGB")
        w, h = im.size
        if max(w, h) > LOGO_MAX_EDGE:
            ratio = LOGO_MAX_EDGE / max(w, h)
            im = im.resize((int(w * ratio), int(h * ratio)), Image.Resampling.LANCZOS)
        out_path = dest.with_suffix(".png")
        im.save(out_path, format="PNG", optimize=True)
        if dest != out_path and dest.exists():
            dest.unlink(missing_ok=True)
        return str(out_path.relative_to(DASH)).replace("\\", "/")
    except Exception:
        # guardar crudo si es PNG/JPG pequeño
        if len(raw) < 800_000:
            dest.write_bytes(raw)
            return str(dest.relative_to(DASH)).replace("\\", "/")
    return None


def fetch_logo_for_domain(home_url: str, slug: str) -> tuple[str | None, str | None]:
    """(ruta relativa dashboard, url origen)"""
    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    time.sleep(REQUEST_DELAY_S)
    html = fetch_text(home_url)
    if not html:
        return None, None
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return None, None

    candidates: list[tuple[int, str]] = icon_candidates(soup, home_url)
    og = og_image(soup, home_url)
    if og:
        candidates.append((12, og))
    candidates.append((30, urljoin(home_url, "/favicon.ico")))

    dest_base = LOGO_DIR / slug

    for prio, url in sorted(candidates, key=lambda x: x[0]):
        time.sleep(REQUEST_DELAY_S * 0.6)
        raw, ct = fetch_bytes(url)
        if not raw or len(raw) < 32:
            continue
        ext = ".png"
        if "svg" in (ct or "").lower() or raw.strip()[:1] == b"<":
            ext = ".svg"
        elif "ico" in (ct or "").lower() or url.endswith(".ico"):
            ext = ".ico"
        saved = save_logo_image(raw, dest_base.with_suffix(ext))
        if saved:
            return saved, url
    return None, None


def normalize_seed(domain: str) -> str:
    return f"https://{domain}/"


def scrape_one(domain: str) -> dict:
    seed = normalize_seed(domain)
    slug = domain_slug(domain)
    rp = get_robots_parser(seed)
    urls = discover_urls(seed, rp)
    pages: list[dict] = []
    seen_urls: set[str] = set()
    for idx, u in enumerate(urls, start=1):
        time.sleep(REQUEST_DELAY_S)
        html = fetch_text(u)
        if not html:
            html = fetch_text_browser(u)
        if not html:
            continue
        page_data = extract_page_meta(html, u)
        if is_soft_404(page_data):
            continue
        page_data["url"] = u
        page_data["status"] = "ok"
        page_data["capturedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if idx <= MAX_SCREENSHOTS_PER_DOMAIN:
            shot, visual = screenshot_and_visual(u, slug, idx)
            page_data["screenshot"] = shot
            page_data["visual"] = visual
        seen_urls.add(u)
        pages.append(page_data)

    # Single-page fallback: si pocas paginas validas, extrae secciones del home.
    if len(pages) <= 4:
        home_html = fetch_text(seed) or fetch_text_browser(seed)
        if home_html:
            for sec in extract_home_sections(home_html, seed.rstrip("/")):
                if sec["url"] in seen_urls:
                    continue
                seen_urls.add(sec["url"])
                pages.append(sec)

    logo_path, logo_src = fetch_logo_for_domain(seed, slug)
    visual_summary = aggregate_visual_summary(pages)
    return {
        "domain": domain,
        "homeUrl": seed,
        "pagesFetched": len(pages),
        "urls": [p["url"] for p in pages],
        "pages": pages,
        "visualSummary": visual_summary,
        "logo": logo_path,
        "logoSourceUrl": logo_src,
    }


def main():
    domains = load_domains()
    DASH.mkdir(parents=True, exist_ok=True)
    LOGO_DIR.mkdir(parents=True, exist_ok=True)

    out: dict = {
        "meta": {
            "maxPagesPerDomain": MAX_PAGES_PER_DOMAIN,
            "maxScreenshotsPerDomain": MAX_SCREENSHOTS_PER_DOMAIN,
            "scrapedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "userAgentNote": USER_AGENT[:80] + "…",
        },
        "sites": {},
    }

    for domain in domains:
        print("Scraping", domain, "...")
        try:
            out["sites"][domain] = scrape_one(domain)
        except Exception as e:
            out["sites"][domain] = {
                "domain": domain,
                "error": str(e),
                "pages": [],
                "logo": None,
            }
        print("  ->", out["sites"][domain].get("pagesFetched"), "páginas, logo:", out["sites"][domain].get("logo"))

    out_path = DASH / "content_audit.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Wrote", out_path)


if __name__ == "__main__":
    try:
        main()
    finally:
        ctx = _playwright_getter()
        if ctx.get("browser"):
            try:
                ctx["browser"].close()
            except Exception:
                pass
        if ctx.get("pw"):
            try:
                ctx["pw"].stop()
            except Exception:
                pass
