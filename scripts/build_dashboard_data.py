"""
Lee los Excel TotalTrafficSourcesOverview*.xlsx y genera JSON + HTML embebido.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROFILES_PATH = ROOT / "scripts" / "competitors_profile.json"
CONTENT_AUDIT_PATH = ROOT / "dashboard" / "content_audit.json"


def load_content_audit() -> tuple[dict[str, dict], dict]:
    """Sitios de auditoría web + meta (límite páginas, fecha)."""
    if not CONTENT_AUDIT_PATH.exists():
        return {}, {}
    raw = json.loads(CONTENT_AUDIT_PATH.read_text(encoding="utf-8"))
    return raw.get("sites") or {}, raw.get("meta") or {}


def load_competitor_profiles() -> tuple[dict, dict[str, dict]]:
    """Meta contextual + mapa domain -> perfil cualitativo."""
    if not PROFILES_PATH.exists():
        return {}, {}
    raw = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
    meta_ctx = raw.get("meta") or {}
    profiles = raw.get("profiles") or {}
    return meta_ctx, profiles


# Grupos sectoriales para filtros / pestañas
GROUPS: dict[str, dict] = {
    "vidrio_canceleria": {
        "label": "Vidrio / cancelería / aluminio",
        "domains": [
            "aviglass.com.mx",
            "alvagsa.com",
            "vetrogalo.com",
            "cristel.com.mx",
            "cancelesfinos.com",
        ],
    },
    "ventanas": {
        "label": "Ventanas / PVC",
        "domains": [
            "ventanashermex.com.mx",
            "abatikventanas.com",
            "idealventanas.com",
            "ventanastermoacusticasdepvc.com.mx",
            "sisvent.mx",
        ],
    },
    "vitralogic_veka": {
        "label": "Vitralogic / Veka LA",
        "domains": ["vitralogic.com", "vekalatinamerica.com"],
    },
}

DISPLAY_NAMES: dict[str, str] = {
    "aviglass.com.mx": "Aviglass",
    "alvagsa.com": "Alvagsa",
    "vetrogalo.com": "Vetrogalo",
    "cristel.com.mx": "Cristel",
    "cancelesfinos.com": "Canceles Finos",
    "ventanashermex.com.mx": "Ventanas Hermex",
    "abatikventanas.com": "Abatik",
    "idealventanas.com": "Ideal Ventanas",
    "ventanastermoacusticasdepvc.com.mx": "Termoacústicas PVC",
    "sisvent.mx": "Sisvent",
    "vitralogic.com": "Vitralogic",
    "vekalatinamerica.com": "Veka Latinoamérica",
}


def domain_group(domain: str) -> str:
    for gid, g in GROUPS.items():
        if domain in g["domains"]:
            return gid
    return "other"


def parse_month(ts) -> str | None:
    if pd.isna(ts):
        return None
    if isinstance(ts, datetime):
        return ts.strftime("%Y-%m")
    try:
        dt = pd.to_datetime(ts)
        return dt.strftime("%Y-%m")
    except Exception:
        return None


def read_monthly_sheets(xlsx_path: Path) -> dict[str, list[dict]]:
    """domain -> lista de {month, channel, traffic, share}"""
    xl = pd.ExcelFile(xlsx_path)
    out: dict[str, list[dict]] = defaultdict(list)
    for sheet in xl.sheet_names:
        if not sheet.startswith("Monthly_Data"):
            continue
        df = pd.read_excel(xlsx_path, sheet_name=sheet, header=0)
        if df.shape[0] < 1:
            continue
        # columnas esperadas
        cols = [str(c).strip() for c in df.columns]
        # normalizar nombres
        col_map = {}
        for i, c in enumerate(cols):
            cl = c.lower()
            if "time" in cl or "period" in cl:
                col_map["time"] = df.columns[i]
            elif "domain" in cl:
                col_map["domain"] = df.columns[i]
            elif "channel" in cl and "traffic" not in cl:
                col_map["channel"] = df.columns[i]
            elif "channel" in cl and "traffic" in cl:
                col_map["traffic"] = df.columns[i]
            elif "traffic share" == cl or (cl.startswith("traffic") and "share" in cl and "channel" not in cl):
                col_map["share"] = df.columns[i]
            elif "traffic" in cl and "share" not in cl:
                col_map["traffic"] = df.columns[i]
            elif "share" in cl:
                col_map["share"] = df.columns[i]
        if "time" not in col_map or "domain" not in col_map or "channel" not in col_map:
            continue
        traffic_col = col_map.get("traffic")
        for _, row in df.iterrows():
            dom = row.get(col_map["domain"])
            if pd.isna(dom) or not str(dom).strip():
                continue
            dom = str(dom).strip().lower()
            ch = row.get(col_map["channel"])
            if pd.isna(ch):
                continue
            channel = str(ch).strip()
            month = parse_month(row.get(col_map["time"]))
            if not month:
                continue
            traf = row.get(traffic_col) if traffic_col else None
            if pd.isna(traf):
                traffic_val = 0.0
            else:
                traffic_val = float(traf)
            share = None
            if "share" in col_map:
                s = row.get(col_map["share"])
                if not pd.isna(s):
                    share = float(s)
            out[dom].append(
                {
                    "month": month,
                    "channel": channel,
                    "traffic": traffic_val,
                    "share": share,
                }
            )
    return dict(out)


def aggregate_by_month(rows: list[dict]) -> dict[str, dict]:
    """month -> { channels: {ch: sum}, total }"""
    by_month: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        m = r["month"]
        ch = r["channel"]
        by_month[m][ch] += r["traffic"]
    result: dict[str, dict] = {}
    for m, chans in sorted(by_month.items()):
        total = sum(chans.values())
        result[m] = {"channels": dict(chans), "total": total}
    return result


def all_domains_in_order() -> list[str]:
    ordered: list[str] = []
    for g in GROUPS.values():
        ordered.extend(g["domains"])
    return ordered


def _short_snippet(text: str, keyword: str, span: int = 95) -> str:
    low = text.lower()
    i = low.find(keyword.lower())
    if i < 0:
        return ""
    start = max(0, i - span)
    end = min(len(text), i + len(keyword) + span)
    out = text[start:end].strip().replace("\n", " ")
    out = re.sub(r"\s+", " ", out)
    return out[:240]


def extract_company_signals(ca: dict, profile: dict) -> dict:
    pages = ca.get("pages") or []
    corpus_parts: list[str] = []
    evidences: list[dict] = []
    for p in pages:
        txt = " ".join(
            [
                str(p.get("title") or ""),
                str(p.get("h1") or ""),
                str(p.get("metaDescription") or ""),
                str(p.get("textFull") or ""),
                str(p.get("textPreview") or ""),
            ]
        )
        if txt.strip():
            corpus_parts.append(txt)
    corpus = " ".join(corpus_parts)
    low = corpus.lower()

    found_years = []
    for m in re.finditer(r"\b(19[5-9]\d|20[0-2]\d)\b", corpus):
        year = int(m.group(1))
        if year <= datetime.now().year:
            found_years.append(year)
    founded_year = min(found_years) if found_years else None
    founded_profile = profile.get("founded")
    if founded_profile:
        m = re.search(r"(19[5-9]\d|20[0-2]\d)", str(founded_profile))
        if m:
            founded_year = int(m.group(1))
    if founded_year:
        ev = _short_snippet(corpus, str(founded_year))
        if ev:
            evidences.append({"signal": "foundedYear", "quote": ev})

    exp_years = None
    exp_match = re.search(
        r"(m[aá]s de|mas de|con)\s+(\d{1,2})\s+a[nñ]os", low, re.I
    ) or re.search(r"(\d{1,2})\s+a[nñ]os\s+de\s+experiencia", low, re.I)
    if exp_match:
        n = int(exp_match.group(2) if exp_match.lastindex and exp_match.lastindex >= 2 else exp_match.group(1))
        if 1 <= n <= 80:
            exp_years = n
            ev = _short_snippet(corpus, exp_match.group(0))
            if ev:
                evidences.append({"signal": "yearsExperience", "quote": ev})

    branch_matches = re.findall(
        r"\b(\d{1,3})\s+(sucursal(?:es)?|showroom(?:s)?|tienda(?:s)?|oficina(?:s)?|punto(?:s)? de venta|planta(?:s)?)\b",
        low,
        re.I,
    )
    branch_count = None
    if branch_matches:
        branch_count = max(int(x[0]) for x in branch_matches)
        ev = _short_snippet(corpus, branch_matches[0][1])
        if ev:
            evidences.append({"signal": "branchCount", "quote": ev})

    scope = "local/regional"
    if any(k in low for k in ["latinoam", "latam", "internacional", "global"]):
        scope = "regional/internacional"
    elif any(k in low for k in ["nacional", "todo mexico", "republica mexicana"]):
        scope = "nacional"

    place_keywords = [
        "ciudad de mexico",
        "cdmx",
        "edomex",
        "guadalajara",
        "monterrey",
        "queretaro",
        "puebla",
        "cancun",
        "merida",
        "tijuana",
        "mexico",
        "latinoamerica",
    ]
    places = [k for k in place_keywords if k in low]
    if profile.get("hqNote"):
        hq = str(profile.get("hqNote"))
        for k in place_keywords:
            if k in hq.lower() and k not in places:
                places.append(k)
    places = places[:8]
    if places:
        for pl in places[:2]:
            ev = _short_snippet(corpus, pl)
            if ev:
                evidences.append({"signal": "coverage", "quote": ev})

    # Señales de ubicaciones más concretas en footer/contacto.
    address_snippets: list[str] = []
    compact = re.sub(r"\s+", " ", corpus)
    addr_patterns = [
        r"(showroom[^)]{20,260}\))",
        r"(planta[^)]{20,260}\))",
        r"(ciudad de m[eé]xico[^.]{10,120})",
        r"(cdmx[^.]{6,120})",
    ]
    for pat in addr_patterns:
        for m in re.finditer(pat, compact, re.I):
            sn = m.group(1).strip(" -:;,.")
            if len(sn) >= 18 and sn not in address_snippets:
                address_snippets.append(sn[:260])
    address_snippets = address_snippets[:4]
    if address_snippets:
        evidences.append({"signal": "coverage", "quote": address_snippets[0]})

    confidence_points = 0
    confidence_points += 1 if founded_year else 0
    confidence_points += 1 if exp_years else 0
    confidence_points += 1 if branch_count else 0
    confidence_points += 1 if places else 0
    confidence = "baja"
    if confidence_points >= 3:
        confidence = "alta"
    elif confidence_points == 2:
        confidence = "media"

    size_hint = "no determinado"
    if branch_count and branch_count >= 10:
        size_hint = "operacion multisitio"
    elif branch_count and branch_count >= 3:
        size_hint = "operacion mediana"
    elif exp_years and exp_years >= 15:
        size_hint = "trayectoria consolidada"

    return {
        "foundedYear": founded_year,
        "yearsExperienceClaim": exp_years,
        "branchCountEstimate": branch_count,
        "coverageScope": scope,
        "coveragePlaces": places,
        "addressSnippets": address_snippets,
        "sizeHint": size_hint,
        "confidence": confidence,
        "evidence": evidences[:5],
    }


def extract_certifications(ca: dict, profile: dict) -> dict:
    pages = ca.get("pages") or []
    corpus = " ".join(
        " ".join(
            [
                str(p.get("title") or ""),
                str(p.get("h1") or ""),
                str(p.get("metaDescription") or ""),
                str(p.get("textFull") or ""),
                str(p.get("textPreview") or ""),
            ]
        )
        for p in pages
    )
    low = corpus.lower()
    cert_patterns = {
        "NAMI": [r"\bnami\b"],
        "ISO 9001": [r"iso\s*9001", r"iso[-\s]?9001:?\d*"],
        "ISO 14001": [r"iso\s*14001", r"iso[-\s]?14001:?\d*"],
        "CE": [r"\bmarcado ce\b", r"\bcertificaci[oó]n ce\b", r"\bce\b"],
        "ENERGY STAR": [r"energy\s*star"],
        "LEED": [r"\bleed\b"],
        "NFRC": [r"\bnfrc\b"],
        "ASTM": [r"\bastm\b"],
        "NMX": [r"\bnmx\b"],
        "NOM": [r"\bnom\b"],
    }
    found: list[str] = []
    evidence: list[dict] = []
    for cert, pats in cert_patterns.items():
        matched = False
        for pat in pats:
            m = re.search(pat, low, re.I)
            if m:
                matched = True
                # Prioriza evidencia con URL real por página.
                for p in pages:
                    page_text = " ".join(
                        [
                            str(p.get("title") or ""),
                            str(p.get("h1") or ""),
                            str(p.get("metaDescription") or ""),
                            str(p.get("textFull") or ""),
                            str(p.get("textPreview") or ""),
                        ]
                    )
                    if re.search(pat, page_text.lower(), re.I):
                        sn = _short_snippet(page_text, m.group(0))
                        if sn:
                            evidence.append(
                                {
                                    "certification": cert,
                                    "quote": sn,
                                    "url": p.get("url"),
                                }
                            )
                        break
                break
        if matched:
            found.append(cert)
    # Complemento desde perfil cualitativo si existe texto relacionado
    prof_text = " ".join(
        [
            str(profile.get("positioning") or ""),
            str(profile.get("qualitativeNote") or ""),
            str(profile.get("materialsLabel") or ""),
        ]
    ).lower()
    for cert in ["NAMI", "ISO 9001", "ISO 14001", "LEED"]:
        if cert.lower().split()[0] in prof_text and cert not in found:
            found.append(cert)
    confidence = "baja"
    if len(found) >= 3:
        confidence = "alta"
    elif len(found) >= 1:
        confidence = "media"
    return {
        "certifications": found,
        "confidence": confidence,
        "evidence": evidence[:5],
    }


def derive_communication_summary(ca: dict) -> dict:
    pages = ca.get("pages") or []
    joined = " ".join(
        " ".join(
            [
                str(p.get("title") or ""),
                str(p.get("h1") or ""),
                str(p.get("metaDescription") or ""),
                str(p.get("textFull") or ""),
                str(p.get("textPreview") or ""),
            ]
        )
        for p in pages
    ).lower()
    joined_norm = unicodedata.normalize("NFKD", joined).encode("ascii", "ignore").decode("ascii")
    topic_map = {
        "aislamiento termico/acustico": ["aislamiento", "termico", "acustico", "ruido"],
        "canceleria y fachadas": ["canceleria", "fachada", "fachadas", "muro cortina", "aluminio"],
        "ventanas pvc": ["ventana", "pvc", "hermeticidad"],
        "proyectos e instalacion": ["proyecto", "instalacion", "obra", "residencial", "comercial"],
        "diseno y acabados": ["diseno", "acabados", "estetica", "arquitectonico"],
        "seguridad y calidad": ["seguridad", "calidad", "garantia", "certificacion"],
    }
    topics = []
    for label, kws in topic_map.items():
        score = sum(joined_norm.count(unicodedata.normalize("NFKD", k).encode("ascii", "ignore").decode("ascii")) for k in kws)
        if score > 0:
            topics.append({"topic": label, "score": score})
    topics.sort(key=lambda x: x["score"], reverse=True)
    cta_counter: defaultdict[str, int] = defaultdict(int)
    page_types: defaultdict[str, int] = defaultdict(int)
    evidence = []
    for p in pages:
        for c in p.get("ctaSignals") or []:
            cta_counter[c] += 1
        pt = p.get("pageType") or "general"
        page_types[pt] += 1
        prev = (p.get("textPreview") or "").strip()
        if prev:
            evidence.append({"url": p.get("url"), "quote": prev[:220]})
    audiences = []
    if "residencial" in joined_norm:
        audiences.append("residencial")
    if any(x in joined_norm for x in ["arquitecto", "arquitectura", "constructor", "desarrollador"]):
        audiences.append("profesionales de construccion")
    if any(x in joined_norm for x in ["industrial", "corporativo", "comercial"]):
        audiences.append("B2B comercial/industrial")
    differentiators = []
    if any(x in joined_norm for x in ["garantia", "calidad", "certificacion"]):
        differentiators.append("enfasis en calidad y confianza")
    if any(x in joined_norm for x in ["diseno", "acabados", "premium", "arquitectonico"]):
        differentiators.append("foco en diseno y valor estetico")
    if any(x in joined_norm for x in ["aislamiento", "eficiencia energetica", "termico", "acustico"]):
        differentiators.append("beneficio funcional de confort/eficiencia")
    if not differentiators:
        differentiators.append("propuesta comunicada principalmente por portfolio y servicios")
    product_terms = [
        "ventanas",
        "canceles",
        "fachadas",
        "muro cortina",
        "unitizadas",
        "barandales",
        "puertas",
        "domos",
        "espejos",
        "cristal templado",
        "cristal arquitectonico",
        "cerramientos",
        "aluminio",
        "pvc",
    ]
    service_terms = [
        "instalacion",
        "mantenimiento",
        "asesoria",
        "asesoria integral",
        "fabricacion",
        "diseno",
        "cotizacion",
        "ingenieria",
        "soluciones",
        "servicio abarca",
        "acompanamiento",
    ]
    products = [k for k in product_terms if unicodedata.normalize("NFKD", k).encode("ascii", "ignore").decode("ascii") in joined_norm]
    services = [k for k in service_terms if unicodedata.normalize("NFKD", k).encode("ascii", "ignore").decode("ascii") in joined_norm]
    visual = ca.get("visualSummary") or {}
    exec_summary = "Comunica principalmente {} con enfoque en {}.".format(
        topics[0]["topic"] if topics else "servicios generales",
        " / ".join(services[:2]) if services else "soluciones de instalacion y proyecto",
    )
    return {
        "topics": topics[:6],
        "contentStructure": dict(sorted(page_types.items(), key=lambda kv: kv[1], reverse=True)),
        "products": products[:8],
        "services": services[:8],
        "audiences": audiences[:5],
        "differentiators": differentiators[:4],
        "ctaSignals": dict(sorted(cta_counter.items(), key=lambda kv: kv[1], reverse=True)),
        "territories": [t["topic"] for t in topics[:5]],
        "evidenceQuotes": evidence[:4],
        "visualSummary": visual,
        "executiveSummary": exec_summary,
    }


def pick_home_screenshot(ca: dict) -> str | None:
    pages = ca.get("pages") or []
    for p in pages:
        if p.get("pageType") == "home" and p.get("screenshot"):
            return p.get("screenshot")
    for p in pages:
        if p.get("screenshot"):
            return p.get("screenshot")
    return None


def pick_screenshot_gallery(ca: dict, max_items: int = 12) -> list[dict]:
    pages = ca.get("pages") or []
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    preferred_types = ["home", "servicio", "producto", "proyectos", "contacto", "nosotros", "general"]
    pages_sorted = sorted(
        pages,
        key=lambda p: (
            preferred_types.index(p.get("pageType")) if p.get("pageType") in preferred_types else 99,
            p.get("url") or "",
        ),
    )
    for p in pages_sorted:
        shot = p.get("screenshot")
        url = p.get("url") or ""
        if not shot or (shot, url) in seen:
            continue
        seen.add((shot, url))
        out.append(
            {
                "image": shot,
                "url": url,
                "pageType": p.get("pageType") or "general",
                "title": (p.get("title") or p.get("h1") or "").strip()[:140],
            }
        )
        if len(out) >= max_items:
            break
    return out


def yearly_metrics_2025(monthly_agg: dict[str, dict]) -> dict:
    months_2025 = [m for m in sorted(monthly_agg.keys()) if m.startswith("2025-")]
    if not months_2025:
        return {
            "months": [],
            "totalTraffic": 0.0,
            "avgMonthlyTraffic": 0.0,
            "sourceMix": {},
            "topSource": None,
        }
    total = 0.0
    sources: defaultdict[str, float] = defaultdict(float)
    for m in months_2025:
        entry = monthly_agg.get(m) or {}
        total += float(entry.get("total") or 0)
        for ch, v in (entry.get("channels") or {}).items():
            sources[ch] += float(v or 0)
    mix = {}
    if total > 0:
        for ch, v in sources.items():
            mix[ch] = v / total
    top_source = None
    if sources:
        top_source = sorted(sources.items(), key=lambda kv: kv[1], reverse=True)[0][0]
    return {
        "months": months_2025,
        "totalTraffic": total,
        "avgMonthlyTraffic": total / len(months_2025),
        "sourceMix": dict(sorted(mix.items(), key=lambda kv: kv[1], reverse=True)),
        "topSource": top_source,
    }


def build_company_card(site_item: dict) -> dict:
    prof = site_item.get("profile") or {}
    comm = site_item.get("communication") or {}
    sig = site_item.get("companySignals") or {}
    certs = site_item.get("certifications") or {}
    metrics = site_item.get("metrics2025") or {}
    summary_lines = []
    products = comm.get("products") or []
    services = comm.get("services") or []
    audiences = comm.get("audiences") or []
    diffs = comm.get("differentiators") or []
    topics = [x.get("topic") for x in (comm.get("topics") or []) if x.get("topic")]
    if prof.get("positioning"):
        summary_lines.append(str(prof.get("positioning")))
    else:
        summary_lines.append(
            "Enfocada en {} con propuesta de {}.".format(
                prof.get("materialsLabel") or "soluciones de cerramiento",
                (comm.get("topics") or [{"topic": "servicios e instalacion"}])[0].get("topic"),
            )
        )
    if prof.get("hqNote"):
        summary_lines.append("Ubicacion/cobertura: {}.".format(prof.get("hqNote")))
    if topics:
        summary_lines.append("Temas comunicados: {}.".format(", ".join(topics[:3])))
    if products or services:
        summary_lines.append(
            "Oferta principal: productos ({}) y servicios ({}).".format(
                ", ".join(products[:4]) if products else "no explicitados",
                ", ".join(services[:4]) if services else "no explicitados",
            )
        )
    if audiences:
        summary_lines.append("Audiencias objetivo probables: {}.".format(", ".join(audiences[:3])))
    if diffs:
        summary_lines.append("Diferenciadores comunicados: {}.".format(" | ".join(diffs[:2])))

    what_they_do = []
    what_they_do.append("Empresa del segmento {}.".format(prof.get("materialsLabel") or "vidrio/canceleria/ventanas"))
    if products:
        what_they_do.append("Portafolio destacado: {}.".format(", ".join(products[:5])))
    if services:
        what_they_do.append("Servicios ofrecidos: {}.".format(", ".join(services[:5])))
    if audiences:
        what_they_do.append("Se dirige principalmente a {}.".format(", ".join(audiences[:3])))
    if prof.get("positioning"):
        what_they_do.append("Posicionamiento observado: {}.".format(str(prof.get("positioning"))[:190]))
    elif diffs:
        what_they_do.append("Propuesta de valor: {}.".format(" | ".join(diffs[:2])))

    # Normalizar "Dónde están" a formato corto y comparable.
    def _norm_place_label(p: str) -> str:
        low = (p or "").lower()
        if "cdmx" in low or "ciudad de mexico" in low:
            return "CDMX"
        if "edomex" in low or "estado de mexico" in low or "lopez mateos" in low:
            return "Edomex"
        if "queretaro" in low:
            return "Queretaro"
        if "guadalajara" in low:
            return "Guadalajara"
        if "monterrey" in low:
            return "Monterrey"
        if "puebla" in low:
            return "Puebla"
        if "cancun" in low:
            return "Cancun"
        if "merida" in low:
            return "Merida"
        if "tijuana" in low:
            return "Tijuana"
        if "latinoamerica" in low:
            return "Latinoamerica"
        if "mexico" in low:
            return "Mexico"
        return p.strip().title() if p else ""

    scope_map = {
        "nacional": "presencia nacional",
        "regional/internacional": "presencia regional/internacional",
        "local/regional": "presencia local/regional",
    }
    scope_label = scope_map.get(sig.get("coverageScope"), "presencia no especificada")

    place_candidates: list[str] = []
    for x in (sig.get("coveragePlaces") or []):
        lbl = _norm_place_label(x)
        if lbl and lbl not in place_candidates:
            place_candidates.append(lbl)
    hq = (prof.get("hqNote") or "").lower()
    for k in ["cdmx", "ciudad de mexico", "edomex", "queretaro", "guadalajara", "monterrey", "puebla", "cancun", "merida", "tijuana", "latinoamerica", "mexico"]:
        if k in hq:
            lbl = _norm_place_label(k)
            if lbl and lbl not in place_candidates:
                place_candidates.append(lbl)
    if not place_candidates:
        place_candidates = ["Ubicacion no especificada"]
    where_text = "{}; {}".format(", ".join(place_candidates[:2]), scope_label)

    return {
        "summaryShort": " ".join(summary_lines)[:520],
        "whatTheyDo": " ".join(what_they_do)[:680] if what_they_do else "Sin descripcion automatica.",
        "where": where_text,
        "sizeHint": sig.get("sizeHint") or "no determinado",
        "kpi2025": {
            "totalTraffic": metrics.get("totalTraffic", 0.0),
            "avgMonthlyTraffic": metrics.get("avgMonthlyTraffic", 0.0),
            "shareInGroup": metrics.get("shareInGroup"),
            "topSource": metrics.get("topSource"),
            "sourceMix": metrics.get("sourceMix") or {},
            "monthsCount": len(metrics.get("months") or []),
        },
        "topics": comm.get("topics") or [],
        "products": comm.get("products") or [],
        "services": comm.get("services") or [],
        "audiences": comm.get("audiences") or [],
        "differentiators": comm.get("differentiators") or [],
        "territories": comm.get("territories") or [],
        "evidenceQuotes": comm.get("evidenceQuotes") or [],
        "visualSummary": comm.get("visualSummary") or {},
        "homeScreenshot": site_item.get("homeScreenshot"),
        "screenshotGallery": site_item.get("screenshotGallery") or [],
        "signals": sig,
        "certifications": certs,
    }


def build_executive_benchmark(sites_payload: list[dict]) -> dict:
    months_all: defaultdict[str, float] = defaultdict(float)
    topic_scores: defaultdict[str, float] = defaultdict(float)
    audience_counts: defaultdict[str, int] = defaultdict(int)
    products_counts: defaultdict[str, int] = defaultdict(int)
    services_counts: defaultdict[str, int] = defaultdict(int)
    source_2025_totals: defaultdict[str, float] = defaultdict(float)

    for s in sites_payload:
        if s.get("hasData"):
            for month, m in (s.get("monthly") or {}).items():
                months_all[month] += float(m.get("total") or 0)
                if month.startswith("2025-"):
                    for ch, v in (m.get("channels") or {}).items():
                        source_2025_totals[ch] += float(v or 0)

        comm = s.get("communication") or {}
        for t in comm.get("topics") or []:
            topic_scores[str(t.get("topic"))] += float(t.get("score") or 0)
        for a in comm.get("audiences") or []:
            audience_counts[str(a)] += 1
        for p in comm.get("products") or []:
            products_counts[str(p)] += 1
        for sv in comm.get("services") or []:
            services_counts[str(sv)] += 1

    ranking_2025 = sorted(
        [
            {
                "domain": s["domain"],
                "label": s["label"],
                "totalTraffic2025": float((s.get("metrics2025") or {}).get("totalTraffic") or 0),
            }
            for s in sites_payload
            if (s.get("metrics2025") or {}).get("totalTraffic") is not None
        ],
        key=lambda x: x["totalTraffic2025"],
        reverse=True,
    )
    winner = ranking_2025[0] if ranking_2025 else None

    monthly_sorted = sorted(months_all.items(), key=lambda kv: kv[0])
    peaks = sorted(monthly_sorted, key=lambda kv: kv[1], reverse=True)[:3]
    lows = sorted([x for x in monthly_sorted if x[1] > 0], key=lambda kv: kv[1])[:3]

    top_topics = sorted(topic_scores.items(), key=lambda kv: kv[1], reverse=True)[:6]
    top_topic = top_topics[0][0] if top_topics else None
    top_audiences = sorted(audience_counts.items(), key=lambda kv: kv[1], reverse=True)[:6]
    top_products = sorted(products_counts.items(), key=lambda kv: kv[1], reverse=True)[:8]
    top_services = sorted(services_counts.items(), key=lambda kv: kv[1], reverse=True)[:8]

    src_total = sum(source_2025_totals.values()) or 1.0
    source_mix_2025 = [
        {"channel": ch, "share": v / src_total, "traffic": v}
        for ch, v in sorted(source_2025_totals.items(), key=lambda kv: kv[1], reverse=True)
    ]

    fairness_principles = [
        "Comparar por segmentos equivalentes y no mezclar marcas con o sin datos de tráfico al calcular ganador.",
        "Separar lectura cuantitativa (SimilarWeb) de lectura cualitativa (contenido y diseño del sitio).",
        "Usar periodos homogéneos (2025 completo) para ranking y estacionalidad.",
        "Reportar cobertura del scraping por marca para no sobreinterpretar casos con baja captura.",
    ]
    segment_challenges = [
        "Alta fragmentación competitiva con propuestas similares en fachadas/cancelería y presión por diferenciación real.",
        "Dependencia relevante de canales no propios (según mix) que puede afectar estabilidad del funnel.",
        "Mensajes técnicos complejos: riesgo de comunicar capacidades sin traducir claramente beneficios de negocio.",
    ]
    vitralogic_opportunities = [
        "Capitalizar su narrativa en sistemas/fachadas con casos y pruebas de desempeño para elevar percepción de especialidad.",
        "Estandarizar propuesta por audiencias (arquitectos, desarrolladores, residencial premium) con landing por necesidad.",
        "Convertir contenido de servicios en activos SEO/consideración para capturar demanda informacional del segmento.",
        "Monitorear share de tráfico cuando haya serie disponible y contrastarlo con share of voice de contenidos.",
    ]

    return {
        "winner2025": winner,
        "ranking2025": ranking_2025[:10],
        "monthlyPeaks": [{"month": m, "traffic": v} for m, v in peaks],
        "monthlyLows": [{"month": m, "traffic": v} for m, v in lows],
        "topTopics": [{"topic": t, "score": s} for t, s in top_topics],
        "topTopic": top_topic,
        "topAudiences": [{"audience": a, "count": c} for a, c in top_audiences],
        "topProducts": [{"name": n, "count": c} for n, c in top_products],
        "topServices": [{"name": n, "count": c} for n, c in top_services],
        "sourceMix2025": source_mix_2025[:8],
        "fairnessPrinciples": fairness_principles,
        "segmentChallenges": segment_challenges,
        "vitralogicOpportunities": vitralogic_opportunities,
    }


def build_payload() -> dict:
    ctx_meta, profiles = load_competitor_profiles()
    content_by_domain, content_meta = load_content_audit()

    files = sorted(ROOT.glob("TotalTrafficSourcesOverview*.xlsx"))
    merged: dict[str, list[dict]] = defaultdict(list)
    for f in files:
        part = read_monthly_sheets(f)
        for dom, rows in part.items():
            merged[dom].extend(rows)

    # dedupe: si mismo mes+channel aparece dos veces, sumar (no debería)
    cleaned: dict[str, list[dict]] = {}
    for dom, rows in merged.items():
        key_acc: dict[tuple[str, str], float] = defaultdict(float)
        for r in rows:
            key_acc[(r["month"], r["channel"])] += r["traffic"]
        new_rows: list[dict] = []
        for (month, channel), traf in sorted(key_acc.items()):
            new_rows.append({"month": month, "channel": channel, "traffic": traf})
        cleaned[dom] = new_rows

    months_set: set[str] = set()
    sites_payload = []
    group_total_2025: defaultdict[str, float] = defaultdict(float)
    for dom in all_domains_in_order():
        rows = cleaned.get(dom, [])
        monthly_agg = aggregate_by_month(rows) if rows else {}
        months_set.update(monthly_agg.keys())
        has_data = len(monthly_agg) > 0 and sum(m["total"] for m in monthly_agg.values()) > 0
        prof = profiles.get(dom)
        if prof is None:
            prof = {}
        ca = content_by_domain.get(dom) or {}
        logo = ca.get("logo")
        communication = derive_communication_summary(ca) if ca else {}
        company_signals = extract_company_signals(ca, prof) if ca else {}
        certifications = extract_certifications(ca, prof) if ca else {"certifications": [], "confidence": "baja", "evidence": []}
        metrics_2025 = yearly_metrics_2025(monthly_agg)
        grp = domain_group(dom)
        group_total_2025[grp] += metrics_2025["totalTraffic"]
        home_shot = pick_home_screenshot(ca) if ca else None
        gallery = pick_screenshot_gallery(ca) if ca else []
        sites_payload.append(
            {
                "domain": dom,
                "label": DISPLAY_NAMES.get(dom, dom),
                "group": grp,
                "hasData": has_data,
                "monthly": monthly_agg,
                "profile": prof,
                "logo": logo,
                "homeScreenshot": home_shot,
                "screenshotGallery": gallery,
                "contentAudit": {
                    "pagesFetched": ca.get("pagesFetched"),
                    "logoSourceUrl": ca.get("logoSourceUrl"),
                    "homeUrl": ca.get("homeUrl"),
                    "error": ca.get("error"),
                },
                "communication": communication,
                "companySignals": company_signals,
                "certifications": certifications,
                "metrics2025": metrics_2025,
            }
        )

    for site in sites_payload:
        grp = site["group"]
        total_grp = group_total_2025.get(grp, 0.0)
        my_t = site.get("metrics2025", {}).get("totalTraffic", 0.0)
        share = (my_t / total_grp) if total_grp > 0 else None
        site["metrics2025"]["shareInGroup"] = share
        site["companyCard"] = build_company_card(site)

    executive = build_executive_benchmark(sites_payload)

    all_months = sorted(months_set)
    meta = {
        "title": "Benchmark tráfico web (SimilarWeb)",
        "monthMin": all_months[0] if all_months else None,
        "monthMax": all_months[-1] if all_months else None,
        "monthList": all_months,
        "groups": {k: {"label": v["label"], "domains": v["domains"]} for k, v in GROUPS.items()},
        "context": ctx_meta,
        "executiveBenchmark": executive,
        "contentAuditMeta": content_meta,
        "notes": [
            "Tráfico estimado por canal (SimilarWeb / export). No equivale a analytics propio.",
            "Totales mensuales = suma de tráfico por canal en ese mes.",
            "Vitralogic: sin serie mensual en el export analizado; se muestra como «Sin datos».",
            "Textos de posicionamiento, distancias Castel y marcas de perfil son contexto interno / desk research; no provienen de SimilarWeb.",
            "Logos y textos de sitio provienen de scraping público (máx. páginas/dominio en content_audit.json); uso informativo.",
            "La pestaña de comunicación integra extracción textual + señales visuales desde screenshots automáticos.",
        ],
    }
    return {"meta": meta, "sites": sites_payload}


def build_fichas_360_payload(payload: dict) -> dict:
    sites = payload.get("sites") or []
    meta = payload.get("meta") or {}
    companies = []
    for s in sites:
        companies.append(
            {
                "domain": s.get("domain"),
                "label": s.get("label"),
                "group": s.get("group"),
                "hasTrafficData": s.get("hasData"),
                "logo": s.get("logo"),
                "homeScreenshot": s.get("homeScreenshot"),
                "screenshotGallery": s.get("screenshotGallery") or [],
                "profile": s.get("profile") or {},
                "contentAudit": s.get("contentAudit") or {},
                "communication": s.get("communication") or {},
                "companySignals": s.get("companySignals") or {},
                "certifications": s.get("certifications") or {},
                "metrics2025": s.get("metrics2025") or {},
                "companyCard": s.get("companyCard") or {},
                # Se incluye la serie mensual para análisis externos.
                "monthlyTrafficSeries": s.get("monthly") or {},
            }
        )
    return {
        "meta": {
            "generatedAt": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "dashboard/data.json",
            "description": "Dataset unico de Fichas 360 por empresa (trafico + contenido + visual).",
            "monthRange": {"min": meta.get("monthMin"), "max": meta.get("monthMax")},
            "groups": meta.get("groups") or {},
            "contentAuditMeta": meta.get("contentAuditMeta") or {},
        },
        "companies": companies,
    }


def main():
    payload = build_payload()
    out_json = ROOT / "dashboard" / "data.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # snippet para embebido
    embedded = "window.__BENCHMARK_DATA__ = " + json.dumps(payload, ensure_ascii=False) + ";"
    tpl = (ROOT / "scripts" / "dashboard_template.html").read_text(encoding="utf-8")
    html = tpl.replace("/*__EMBED_DATA__*/", embedded)
    out_html = ROOT / "dashboard" / "index.html"
    out_html.write_text(html, encoding="utf-8")
    fichas_360 = build_fichas_360_payload(payload)
    out_fichas = ROOT / "dashboard" / "fichas_360.json"
    out_fichas.write_text(json.dumps(fichas_360, ensure_ascii=False, indent=2), encoding="utf-8")
    social_tpl = ROOT / "scripts" / "social_dashboard_template.html"
    if social_tpl.is_file():
        out_social = ROOT / "dashboard" / "social.html"
        out_social.write_text(social_tpl.read_text(encoding="utf-8"), encoding="utf-8")
        print("Wrote:", out_social)
    print("Wrote:", out_json)
    print("Wrote:", out_html)
    print("Wrote:", out_fichas)

    try:
        import importlib.util

        _soc_path = ROOT / "scripts" / "build_social_benchmark.py"
        _spec = importlib.util.spec_from_file_location("build_social_benchmark", _soc_path)
        _mod = importlib.util.module_from_spec(_spec)
        if _spec.loader:
            _spec.loader.exec_module(_mod)
            _mod.main()
    except Exception as _e:
        print("Social benchmark (opcional):", _e)


if __name__ == "__main__":
    main()
