"""
Clasifica exportaciones Rival IQ (Facebook + Instagram) y genera dashboard/social_data.json.

Framework de clasificación (v2):
  - Nivel 1: macro (rol estratégico): comercial, inspiracional, educativo, prueba_confianza, marca
  - Nivel 2: categoría (tipo de contenido)
  - Nivel 3: subcategoría (detalle)
  - Intención embudo: awareness | consideration | decision
"""
from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUT_JSON = ROOT / "dashboard" / "social_data.json"


def load_project_dotenv() -> None:
    """Carga variables desde .env en la raíz del proyecto (si existe python-dotenv)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_path = ROOT / ".env"
    if env_path.is_file():
        load_dotenv(env_path)


def resolve_vision_model(provider: str, explicit: str | None) -> str:
    e = (explicit or "").strip()
    if e:
        return e
    env_m = os.environ.get("VISION_MODEL", "").strip()
    if env_m:
        return env_m
    return "gemini-2.0-flash" if provider == "gemini" else "gpt-4o-mini"


def resolve_vision_api_key(provider: str) -> str | None:
    p = (provider or "gemini").strip().lower()
    if p == "openai":
        return os.environ.get("OPENAI_API_KEY", "").strip() or None
    return (
        os.environ.get("GEMINI_API_KEY", "").strip()
        or os.environ.get("GOOGLE_API_KEY", "").strip()
        or None
    )

# -----------------------------------------------------------------------------
# Taxonomía: cada hoja = (macro_id, macro_label, cat_id, cat_label, sub_id, sub_label, keywords)
# Orden en la lista: prioridad de desempate si empatan scores (más arriba = gana).
# -----------------------------------------------------------------------------

TAXONOMY_LEAVES: list[tuple[str, str, str, str, str, str, list[str]]] = [
    # ——— COMERCIAL ———
    (
        "comercial",
        "Comercial",
        "promociones_descuentos",
        "Promociones / descuentos",
        "promociones_general",
        "Promociones",
        ["descuento", "promoción", "promocion", "oferta", "2x1", "mes sin intereses", "rebaja", "precio especial"],
    ),
    (
        "comercial",
        "Comercial",
        "lanzamientos",
        "Lanzamientos",
        "lanzamientos_general",
        "Lanzamientos",
        [
            "lanzamiento", "nuevo lanzamiento", "presentamos", "nuevo producto", "new product",
            "nueva colección", "nueva linea", "nueva serie", "inaugur", "nuevo almacén",
            "abrimos", "apertura", "nueva sucursal",
        ],
    ),
    (
        "comercial",
        "Comercial",
        "catalogo_productos",
        "Catálogo de productos",
        "ventanas_pvc",
        "Ventanas PVC",
        # "pvc" suelto eliminado — demasiado amplio (capturaba nombre de empresa)
        [
            "ventana de pvc", "ventanas de pvc", "ventanas pvc", "perfil pvc",
            "cancelería de pvc", "sistema de pvc", "kommerling", "veka", "kömmerling",
            "rehau", "aluplast", "eurofutur", "premiline",
        ],
    ),
    (
        "comercial",
        "Comercial",
        "catalogo_productos",
        "Catálogo de productos",
        "sistemas_aluminio",
        "Sistemas de aluminio",
        # "aluminio" suelto eliminado — necesita más contexto
        [
            "cancel de aluminio", "canceles de aluminio", "sistema de aluminio", "sistemas de aluminio",
            "aluminio y vidrio", "carpintería de aluminio", "perfil de aluminio", "ventana de aluminio",
            "ventanas de aluminio",
        ],
    ),
    (
        "comercial",
        "Comercial",
        "catalogo_productos",
        "Catálogo de productos",
        "fachadas",
        "Fachadas",
        [
            "fachada de cristal", "curtain wall", "muro cortina", "fachada integral",
            "sistema de fachada", "fachada estructural", "fachada ventilada",
        ],
    ),
    (
        "comercial",
        "Comercial",
        "catalogo_productos",
        "Catálogo de productos",
        "herrajes",
        "Herrajes",
        ["herraje", "bisagra", "cerradura", "manija", "handle", "fitting"],
    ),
    (
        "comercial",
        "Comercial",
        "catalogo_productos",
        "Catálogo de productos",
        "seguridad",
        "Seguridad / protección",
        [
            "vidrio de seguridad", "vidrio antirrobo", "antiintrusion", "anti-intrusión",
            "vidrio antibalístico", "antibalas", "vidrio de protección", "burglar",
            "security glass", "peace of mind", "protege tu hogar", "seguridad del hogar",
        ],
    ),
    (
        "comercial",
        "Comercial",
        "catalogo_productos",
        "Catálogo de productos",
        "diseno_personalizado",
        "A la medida / diseño personalizado",
        [
            "a la medida", "a medida", "diseño personalizado", "personaliz", "custom",
            "soluciones integrales", "proyecto a tu medida", "hacemos a la medida",
        ],
    ),
    (
        "comercial",
        "Comercial",
        "catalogo_productos",
        "Catálogo de productos",
        "especificaciones_tecnicas",
        "Especificaciones técnicas",
        [
            "refuerzo de acero", "cámara de aire", "camara de aire", "estanqueidad",
            "mm de profundidad", "sistema deslizante", "triple acristalamiento",
            "acristalamiento doble", "insulated glass", "vg10500", "vg-4500",
        ],
    ),
    (
        "comercial",
        "Comercial",
        "showroom_contacto",
        "Showroom / contacto",
        "showroom_general",
        "Showroom / visítanos",
        [
            "showroom", "show room", "visítanos", "visítanos en", "visita nuestro",
            "conócenos", "te esperamos en", "nuestras instalaciones", "ven a conocer",
            "agenda tu visita", "agenda una cita", "sin compromiso",
        ],
    ),
    (
        "comercial",
        "Comercial",
        "ficha_tecnica",
        "Ficha técnica",
        "ficha_general",
        "Ficha técnica",
        ["ficha técnica", "ficha tecnica", "datasheet", "especificación técnica"],
    ),
    # ——— INSPIRACIONAL ———
    (
        "inspiracional",
        "Inspiracional",
        "proyectos_terminados",
        "Proyectos terminados",
        "hoteles",
        "Hoteles",
        ["four seasons", "ritz", "hotel", "resort", "le blanc", "spa resort", "boutique hotel"],
    ),
    (
        "inspiracional",
        "Inspiracional",
        "proyectos_terminados",
        "Proyectos terminados",
        "residencial_lujo",
        "Residencial lujo",
        [
            "residencia", "casa de lujo", "penthouse", "los cabos", "luxury residence",
            "casa privada", "vivienda de lujo", "high-end residential",
        ],
    ),
    (
        "inspiracional",
        "Inspiracional",
        "proyectos_terminados",
        "Proyectos terminados",
        "residencial_departamentos",
        "Departamentos / condominios",
        [
            "departamento", "condominio", "torre residencial", "desarrollo habitacional",
            "real estate", "realestate", "real estate development",
        ],
    ),
    (
        "inspiracional",
        "Inspiracional",
        "proyectos_terminados",
        "Proyectos terminados",
        "comercial_corporativo",
        "Comercial / corporativo",
        ["oficina", "corporativo", "torre de oficinas", "edificio de oficinas", "campus empresarial"],
    ),
    (
        "inspiracional",
        "Inspiracional",
        "proyectos_terminados",
        "Proyectos terminados",
        "proyecto_general",
        "Proyecto / obra",
        [
            "proyecto terminado", "obra terminada", "proyecto realizado", "instalación exitosa",
            "proyecto completado", "entregamos", "entrega de proyecto",
        ],
    ),
    (
        "inspiracional",
        "Inspiracional",
        "arquitectura_diseno",
        "Arquitectura / diseño",
        "detalles_esteticos",
        "Detalles estéticos",
        [
            "luz natural", "vista panorámica", "panoramic", "minimalist", "minimalista",
            "elegancia", "aesthetic", "seamless glass", "minimal frames", "total immersion",
            "diseño de interiores", "interior design",
        ],
    ),
    (
        "inspiracional",
        "Inspiracional",
        "arquitectura_diseno",
        "Arquitectura / diseño",
        "arquitectura_general",
        "Arquitectura",
        [
            "arquitectura", "diseño arquitectónico", "modern architecture",
            "diseño contemporáneo", "architectural design",
        ],
    ),
    (
        "inspiracional",
        "Inspiracional",
        "antes_despues",
        "Antes vs después",
        "antes_despues_general",
        "Transformación",
        ["antes y después", "antes despues", "before and after", "transformación"],
    ),
    # ——— EDUCATIVO ———
    (
        "educativo",
        "Educativo",
        "tips_recomendaciones",
        "Tips / recomendaciones",
        "huracanes_resiliencia_climatica",
        "Huracanes / resiliencia climática",
        [
            "huracán", "huracan", "hurricane", "cyclone",
            "temporada de huracanes", "ventana antihuracán", "ventana antihuracan",
            "vidrio antihuracán", "vidrio antihuracan", "impact resistant",
            "resistencia al impacto", "carga de viento", "wind load",
            "preparación ante huracanes", "protección ante huracanes",
            "categoría 5", "categoria 5", "temporal de huracanes", "storm resistant",
            "tormenta tropical",
        ],
    ),
    (
        "educativo",
        "Educativo",
        "tips_recomendaciones",
        "Tips / recomendaciones",
        "aislamiento_acustico",
        "Aislamiento acústico",
        [
            "aislamiento acústico", "aislamiento acustico",
            "ruido exterior", "ruido del exterior", "sonido exterior",
            "insonoriz", "soundproof", "ruido urbano", "reducción de ruido",
            "reduccion de ruido", "ruido del tráfico", "acoustic comfort",
            "aislamiento sonoro", "control de ruido", "menos ruido",
            "nivel de ruido", "decibeles", "stc ",
        ],
    ),
    (
        "educativo",
        "Educativo",
        "tips_recomendaciones",
        "Tips / recomendaciones",
        "aislamiento_termico",
        "Aislamiento térmico",
        [
            "aislamiento térmico", "aislamiento termico",
            "factor solar", "transferencia de calor", "u-value", "valor u",
            "thermal break", "puente térmico", "confort térmico", "comfort térmico",
            "ahorro de calefacción", "aislamiento térmico del vidrio",
            "temperatura interior", "calor en verano", "frío en invierno",
            "control solar", "solar control",
        ],
    ),
    (
        "educativo",
        "Educativo",
        "tips_recomendaciones",
        "Tips / recomendaciones",
        "aislamiento_termico_acustico",
        "Aislamiento térmico y acústico (ambos)",
        [
            "térmico y acústico", "termico y acustico",
            "thermoacoustic", "thermo-acoustic", "termoacústic", "termoacustic",
            "térmico/acústico", "thermo acoustic", "termo-acústic",
            "sistemas termo-acústic", "ventanas termo-acústic",
        ],
    ),
    (
        "educativo",
        "Educativo",
        "tips_recomendaciones",
        "Tips / recomendaciones",
        "eficiencia_energetica",
        "Eficiencia energética",
        [
            "eficiencia energética", "ahorro energético", "leed", "well building", "cooling cost",
            "consumo de energía", "ahorro de energía", "factura eléctrica", "reduce el consumo",
            "huella de carbono", "carbon footprint", "net zero", "cut cooling", "save on your bills",
            "reduce ac usage", "energy savings",
        ],
    ),
    (
        "educativo",
        "Educativo",
        "tips_recomendaciones",
        "Tips / recomendaciones",
        "sustentabilidad",
        "Sustentabilidad / medio ambiente",
        [
            "sustentable", "sustentabilidad", "sostenible", "sostenibilidad",
            "medio ambiente", "impacto ambiental", "eco", "ecológico",
            "green building", "construcción verde", "reciclable", "reciclado",
            "certificación ambiental", "impacto sustentable",
        ],
    ),
    (
        "educativo",
        "Educativo",
        "tips_recomendaciones",
        "Tips / recomendaciones",
        "tipos_vidrio",
        "Tipos de vidrio",
        [
            "vidrio laminado", "vidrio templado", "doble acristalamiento", "laminated glass",
            "uv-filtering", "vidrio insulado", "insulated glass unit", " IGU",
            "vidrio de control solar", "low-e", "low e glass", "vidrio reflectivo",
            "acristalamiento insulado", "vidrio inteligente", "smart glass",
        ],
    ),
    (
        "educativo",
        "Educativo",
        "tips_recomendaciones",
        "Tips / recomendaciones",
        "mantenimiento_cuidado",
        "Mantenimiento / cuidado",
        [
            "mantenimiento", "limpieza de ventanas", "cómo limpiar", "cuidado de ventanas",
            "vida útil", "conservación", "garantía", "durabilidad",
        ],
    ),
    (
        "educativo",
        "Educativo",
        "tips_recomendaciones",
        "Tips / recomendaciones",
        "errores_instalacion",
        "Errores comunes",
        [
            "error común", "evita estos", "mal instalado", "instalación incorrecta",
            "mala instalación", "cómo evitar", "no cometas el error",
        ],
    ),
    (
        "educativo",
        "Educativo",
        "tips_recomendaciones",
        "Tips / recomendaciones",
        "como_elegir",
        "Cómo elegir ventanas",
        [
            "cómo elegir", "como elegir", "guía para elegir", "tips para elegir",
            "qué considerar", "cuál es el mejor material", "pvc aluminio o madera",
            "pvc, aluminio", "pvc o aluminio",
        ],
    ),
    (
        "educativo",
        "Educativo",
        "explicaciones_tecnicas",
        "Explicaciones técnicas simples",
        "explicaciones_general",
        "Explicación técnica",
        [
            "sabías que", "did you know", "permeabilidad al aire", "cómo funciona",
            "¿por qué", "por qué las ventanas", "¿sabías que", "¿qué es",
        ],
    ),
    (
        "educativo",
        "Educativo",
        "comparativas",
        "Comparativas",
        "comparativas_general",
        "Comparativa",
        ["vs.", " versus ", "comparar", "diferencia entre", "compared to", "¿cuál es mejor"],
    ),
    # ——— PRUEBA / CONFIANZA ———
    (
        "prueba_confianza",
        "Prueba / confianza",
        "casos_exito",
        "Casos de éxito",
        "casos_exito_general",
        "Caso de éxito",
        ["caso de éxito", "success story", "historia de éxito"],
    ),
    (
        "prueba_confianza",
        "Prueba / confianza",
        "testimoniales",
        "Testimoniales",
        "testimoniales_general",
        "Testimonial",
        ["testimonio", "reseña", "cliente satisfecho", "nos recomienda", "nuestros clientes dicen"],
    ),
    (
        "prueba_confianza",
        "Prueba / confianza",
        "proyectos_reales_documentados",
        "Proyectos reales documentados",
        "proyectos_documentados",
        "Proyecto documentado",
        ["proyecto documentado", "documentamos", "caso real", "así quedó", "así luce"],
    ),
    (
        "prueba_confianza",
        "Prueba / confianza",
        "proceso_instalacion",
        "Procesos de instalación",
        "obra_avance",
        "Avance de obra",
        [
            "avance de obra", "avance del proyecto", "seguimos avanzando", "en proceso",
            "progreso de obra", "así va", "actualización de obra", "obra en progreso",
            "en pleno proceso",
        ],
    ),
    (
        "prueba_confianza",
        "Prueba / confianza",
        "proceso_instalacion",
        "Procesos de instalación",
        "fabricacion_planta",
        "Fabricación / planta",
        [
            "planta de fabricación", "planta de armado", "proceso de fabricación",
            "manufactura", "taller de producción", "línea de producción",
            "maquinaria de última generación", "tecnología de fabricación",
            "proceso productivo",
        ],
    ),
    (
        "prueba_confianza",
        "Prueba / confianza",
        "proceso_instalacion",
        "Procesos de instalación",
        "behind_the_scenes",
        "Behind the scenes",
        ["behind the scenes", "detrás de", "making of", "backstage"],
    ),
    (
        "prueba_confianza",
        "Prueba / confianza",
        "proceso_instalacion",
        "Procesos de instalación",
        "obra_en_proceso",
        "Obra en proceso (instalación)",
        [
            "instalación en curso", "instalamos", "proceso de instalación",
            "en plena instalación", "equipo de instalación",
        ],
    ),
    (
        "prueba_confianza",
        "Prueba / confianza",
        "proceso_instalacion",
        "Procesos de instalación",
        "clientes_desarrolladores",
        "Clientes / desarrolladores",
        ["desarrollador", "constructor", "inmobiliaria", "desarrollo inmobiliario"],
    ),
    (
        "prueba_confianza",
        "Prueba / confianza",
        "certificaciones",
        "Certificaciones",
        "certificaciones_nami",
        "Certificación NAMI / normas",
        [
            "nami", "certificación", "certificado", "2640", "hurricane ready",
            "estándar de impacto", "ensayado", "tested under", "large missile", "small missile",
            "astm e1886", "astm e1996", "impact test", "cyclone rating",
            "wind pressure", "presión de viento", "protocolo de impacto",
        ],
    ),
    # ——— MARCA ———
    (
        "marca",
        "Marca",
        "eventos",
        "Eventos",
        "expos_ferias",
        "Participación en expos",
        ["expo", "feria", "stand", "congreso", "exhibición", "exhibicion", "trade show"],
    ),
    (
        "marca",
        "Marca",
        "alianzas",
        "Alianzas",
        "alianzas_general",
        "Alianzas",
        ["alianza", "partnership", "avipartner", "business partners", "partner", "firmamos contrato"],
    ),
    (
        "marca",
        "Marca",
        "cultura_equipo",
        "Cultura / equipo",
        "equipo_tecnico",
        "Equipo técnico",
        ["nuestro equipo", "equipo técnico", "ingeniero", "staff", "nuestros colaboradores"],
    ),
    (
        "marca",
        "Marca",
        "cultura_equipo",
        "Cultura / equipo",
        "hecho_en_mexico",
        "Hecho en México / orgullo nacional",
        [
            "hecho en méxico", "hecho en mexico", "100% mexicano", "orgullo mexicano",
            "ingeniería mexicana", "empresa mexicana", "fabricado en méxico",
            "#hechoenmexico", "orgullosamente mexicano", "talento mexicano",
        ],
    ),
    (
        "marca",
        "Marca",
        "cultura_equipo",
        "Cultura / equipo",
        "fechas_especiales",
        "Fechas especiales / saludos",
        [
            "año nuevo", "feliz año", "happy new year", "día del padre", "día de la madre",
            "navidad", "feliz navidad", "merry christmas", "feliz navideña",
            "día de muertos", "independencia", "festejamos", "celebramos con",
            "en este día especial",
        ],
    ),
    (
        "marca",
        "Marca",
        "cultura_equipo",
        "Cultura / equipo",
        "cultura_general",
        "Cultura / valores",
        [
            "cultura", "nuestros valores", "somos una empresa", "nuestra misión",
            "nuestra visión", "nuestra filosofía", "compromiso con",
        ],
    ),
    (
        "marca",
        "Marca",
        "historia_valores",
        "Historia / valores",
        "historia_marca",
        "Historia / legacy",
        # "historia" y "visión" eliminados — demasiado amplios
        # "legacy" se mantiene pero se requiere contexto de marca/trayectoria
        [
            "trayectoria", "desde 19", "años de experiencia", "fundad", "desde que nacimos",
            "legacy", "intothefuture", "#intothefuture",
        ],
    ),
    (
        "marca",
        "Marca",
        "historia_valores",
        "Historia / valores",
        "propuesta_valor",
        "Propuesta de valor / misión",
        [
            "somos líderes", "somos lideres", "nos respalda", "calidad e innovación",
            "calidad que se ve", "nuestro compromiso", "creemos en",
            "soluciones de calidad", "empresa de confianza",
        ],
    ),
    (
        "marca",
        "Marca",
        "historia_valores",
        "Historia / valores",
        "certificaciones_marca",
        "Certificaciones de marca",
        ["empresa certificada", "iso ", "certificación corporativa"],
    ),
]

# Revisión de taxonomía: incluir en caché de Gemini para invalidar resultados al cambiar hojas.
TAXONOMY_REVISION = "2026-04-13d"

# Palabras de intención “decisión” (CTA fuerte)
CTA_DECISION_KEYWORDS = [
    "cotización",
    "cotizacion",
    "whatsapp",
    "llámanos",
    "llamanos",
    "visítanos",
    "visitenos",
    "contáctanos",
    "contactanos",
    "solicita tu",
    "solicita tu cotización",
    "agenda",
    "reserva",
    "llama al",
    "mándanos",
    "mandanos",
    "contact us today",
    "shop now",
    "buy now",
]


def strip_accents(s: str) -> str:
    if not s:
        return ""
    n = unicodedata.normalize("NFKD", s)
    return "".join(c for c in n if not unicodedata.combining(c))


def normalize_for_match(text: str) -> str:
    return strip_accents(text.lower())


def score_leaf(blob: str, keywords: list[str]) -> int:
    return sum(1 for kw in keywords if normalize_for_match(kw) in blob)


def classify_hierarchy(full_text: str) -> dict:
    """
    Devuelve macro, categoría, subcategoría (primaria) y lista de candidatos con score.
    """
    blob = normalize_for_match(full_text)
    scored: list[tuple[int, int, tuple]] = []
    for i, (mid, mlab, cid, clab, sid, slab, kws) in enumerate(TAXONOMY_LEAVES):
        sc = score_leaf(blob, kws)
        if sc > 0:
            scored.append((sc, -i, (mid, mlab, cid, clab, sid, slab, sc)))

    if not scored:
        return {
            "macro_id": "sin_clasificar",
            "macro_label": "Sin clasificar",
            "category_id": "sin_clasificar",
            "category_label": "Sin clasificar",
            "subcategory_id": "sin_clasificar",
            "subcategory_label": "Sin clasificar",
            "score": 0,
            "candidates_top": [],
        }

    scored.sort(key=lambda x: (-x[0], x[1]))
    best = scored[0][2]
    top_n = []
    for sc, _, tup in scored[:5]:
        mid, mlab, cid, clab, sid, slab, _ = tup
        top_n.append(
            {
                "macro_id": mid,
                "category_id": cid,
                "subcategory_id": sid,
                "score": sc,
            }
        )

    return {
        "macro_id": best[0],
        "macro_label": best[1],
        "category_id": best[2],
        "category_label": best[3],
        "subcategory_id": best[4],
        "subcategory_label": best[5],
        "score": best[6],
        "candidates_top": top_n,
    }


def classify_intent(full_text: str, cl: dict) -> str:
    """awareness | consideration | decision"""
    blob = normalize_for_match(full_text)
    macro = cl["macro_id"]
    cat = cl["category_id"]
    cta_hits = sum(1 for kw in CTA_DECISION_KEYWORDS if normalize_for_match(kw) in blob)

    if macro == "sin_clasificar":
        return "consideration"

    if macro == "comercial":
        if cat in ("promociones_descuentos", "lanzamientos"):
            return "decision"
        if cta_hits >= 1:
            return "decision"
        return "consideration"

    if macro == "educativo":
        if "¿" in full_text and cta_hits == 0 and len(full_text) < 280:
            return "awareness"
        if cat == "explicaciones_tecnicas" and cta_hits == 0:
            return "awareness"
        return "consideration"

    if macro == "inspiracional":
        return "consideration"

    if macro == "prueba_confianza":
        return "consideration"

    if macro == "marca":
        return "awareness"

    return "consideration"


def build_classification(full_text: str) -> dict:
    cl = classify_hierarchy(full_text)
    intent = classify_intent(full_text, cl)
    return {
        "macro": {"id": cl["macro_id"], "label": cl["macro_label"]},
        "category": {"id": cl["category_id"], "label": cl["category_label"]},
        "subcategory": {"id": cl["subcategory_id"], "label": cl["subcategory_label"]},
        "intent": intent,
        "score": cl["score"],
        "candidates_top": cl["candidates_top"],
    }


HASHTAG_RE = re.compile(r"#([\w\u00c0-\u024f]+)", re.UNICODE)


def extract_hashtags(text: str) -> list[str]:
    if not text or not isinstance(text, str):
        return []
    return [m.group(1) for m in HASHTAG_RE.finditer(text)]


def normalize_format(
    platform: str,
    raw: str,
    image_url: str = "",
    video_views: float | None = None,
    post_link: str = "",
) -> str:
    """Normaliza el tipo de post a un formato canónico.

    Para Facebook, `status` puede contener imagen o video:
    - Si tiene /videos/ o /reel en la URL  → video
    - Si tiene video_views > 0             → video
    - Si tiene image_url (thumbnail)       → image
    - Si no tiene nada de lo anterior      → text
    """
    r = (raw or "").strip().lower()
    if platform == "instagram":
        if r == "reel":
            return "reel"
        if r == "carousel":
            return "carousel"
        if r == "photo":
            return "image"
        return r or "other"
    # Facebook
    if r == "photo":
        return "image"
    if r == "video":
        return "video"
    if r == "link":
        return "link"
    if r == "status":
        pl = (post_link or "").lower()
        if "/videos/" in pl or "/reel" in pl:
            return "video"
        if video_views and float(video_views) > 0:
            return "video"
        if image_url and str(image_url).strip().startswith("http"):
            return "image"
        return "text"
    return r or "other"


def pick_fb_rate(row: pd.Series) -> float | None:
    """Tasa vs fans de página (no mezclar con tasa por impresión estimada)."""
    col = "engagement_rate_by_page_fan"
    if col in row.index and pd.notna(row[col]):
        try:
            return float(row[col])
        except (TypeError, ValueError):
            pass
    return None


def pick_ig_rate(row: pd.Series) -> float | None:
    """Tasa vs seguidores (no mezclar con tasa por impresión estimada)."""
    col = "engagement_rate_by_follower"
    if col in row.index and pd.notna(row[col]):
        try:
            return float(row[col])
        except (TypeError, ValueError):
            pass
    return None


def pick_er_by_estimated_impression(row: pd.Series) -> float | None:
    col = "engagement_rate_by_estimated_impression"
    if col not in row.index or pd.isna(row[col]):
        return None
    try:
        return float(row[col])
    except (TypeError, ValueError):
        return None


def _safe_float(v) -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(v) -> int | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _safe_impressions(v) -> int | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip().lower()
    if s in ("", "nan", "no prediction", "none"):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _median(nums: list) -> float | None:
    if not nums:
        return None
    s = sorted(nums)
    n = len(s)
    m = n // 2
    if n % 2:
        return float(s[m])
    return (float(s[m - 1]) + float(s[m])) / 2.0


def canonical_company(raw: str) -> str:
    """Unifica variantes de nombre en el export (AVIGLASS / Aviglass, etc.)."""
    s = (raw or "").strip()
    if not s:
        return "Desconocido"
    k = normalize_for_match(s)
    if "aviglass" in k:
        return "Aviglass"
    if "cristel" in k and "corpor" in k:
        return "Corporación Cristel"
    if "veka" in k and "mexico" in k:
        return "VEKA México"
    if "termo" in k and "pvc" in k:
        return "Ventanas Termo-acústicas de PVC"
    if "vetro" in k and "galo" in k:
        return "Vetro Galo"
    if "canceles" in k and "finos" in k:
        return "Canceles Finos"
    if k.startswith("abatik"):
        return "Abatik"
    return s


def text_for_classification(msg: str, link_title, link_desc) -> str:
    parts = [msg]
    for x in (link_title, link_desc):
        if pd.notna(x) and str(x).strip():
            parts.append(str(x))
    return " ".join(parts)


def _image_url_from_row(row: pd.Series) -> str | None:
    u = row.get("image")
    if u is None or (isinstance(u, float) and pd.isna(u)):
        return None
    s = str(u).strip()
    return s if s.startswith("http") else None


def load_facebook(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8")
    rows = []
    for _, row in df.iterrows():
        msg = row.get("message")
        msg = "" if pd.isna(msg) else str(msg)
        link_title = row.get("link_title")
        link_desc = row.get("link_description")
        full = text_for_classification(msg, link_title, link_desc)
        classification = build_classification(full)

        pub = row.get("published_at")
        pub_iso = None
        if pd.notna(pub):
            try:
                pub_iso = pd.to_datetime(pub, errors="coerce")
                if pd.notna(pub_iso):
                    pub_iso = pub_iso.isoformat()
            except Exception:
                pub_iso = str(pub)

        co_raw = str(row.get("company", "")).strip()
        img_url = _image_url_from_row(row)
        lt = "" if pd.isna(link_title) else str(link_title).strip()
        ld = "" if pd.isna(link_desc) else str(link_desc).strip()
        rows.append(
            {
                "platform": "facebook",
                "company": co_raw,
                "company_canonical": canonical_company(co_raw),
                "presence_handle": str(row.get("presence_handle", "") or "").strip(),
                "published_at": pub_iso,
                "message": msg[:1200],
                "link_title": lt[:500] if lt else None,
                "link_description": ld[:800] if ld else None,
                "image_url": img_url,
                "_classify_text": full,
                "post_link": str(row.get("post_link", "") or ""),
                "post_type_raw": str(row.get("post_type", "") or ""),
                "format_normalized": normalize_format(
                    "facebook",
                    str(row.get("post_type", "") or ""),
                    image_url=str(row.get("image", "") or ""),
                    video_views=_safe_float(row.get("video_views")),
                    post_link=str(row.get("post_link", "") or ""),
                ),
                "classification": classification,
                "classification_source": "rules",
                "classification_llm": None,
                "hashtags": [],
                "engagement_total": _safe_float(row.get("engagement_total")),
                "engagement_rate_audience": pick_fb_rate(row),
                "estimated_impressions": _safe_impressions(row.get("estimated_impressions")),
                "engagement_rate_by_estimated_impression": pick_er_by_estimated_impression(row),
                "comments": _safe_int(row.get("comments")),
                "shares": _safe_int(row.get("shares")),
                "page_fans": _safe_int(row.get("page_fans")),
                "post_tag_ugc": _safe_int(row.get("post_tag_ugc")),
                "post_tag_contests": _safe_int(row.get("post_tag_contests")),
            }
        )
    return pd.DataFrame(rows)


def load_instagram(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8")
    rows = []
    for _, row in df.iterrows():
        msg = row.get("message")
        msg = "" if pd.isna(msg) else str(msg)
        link_title = row.get("link_title")
        link_desc = row.get("link_description")
        full_ig = text_for_classification(msg, link_title, link_desc)
        classification = build_classification(full_ig)
        tags = extract_hashtags(msg)

        pub = row.get("published_at")
        pub_iso = None
        if pd.notna(pub):
            try:
                pub_iso = pd.to_datetime(pub, errors="coerce")
                if pd.notna(pub_iso):
                    pub_iso = pub_iso.isoformat()
            except Exception:
                pub_iso = str(pub)

        co_raw = str(row.get("company", "")).strip()
        img_url = _image_url_from_row(row)
        lt = "" if pd.isna(link_title) else str(link_title).strip()
        ld = "" if pd.isna(link_desc) else str(link_desc).strip()
        rows.append(
            {
                "platform": "instagram",
                "company": co_raw,
                "company_canonical": canonical_company(co_raw),
                "presence_handle": str(row.get("presence_handle", "") or "").strip(),
                "published_at": pub_iso,
                "message": msg[:1200],
                "link_title": lt[:500] if lt else None,
                "link_description": ld[:800] if ld else None,
                "image_url": img_url,
                "_classify_text": full_ig,
                "post_link": str(row.get("post_link", "") or ""),
                "post_type_raw": str(row.get("post_type", "") or ""),
                "format_normalized": normalize_format("instagram", str(row.get("post_type", "") or "")),
                "classification": classification,
                "classification_source": "rules",
                "classification_llm": None,
                "hashtags": tags[:40],
                "engagement_total": _safe_float(row.get("engagement_total")),
                "engagement_rate_audience": pick_ig_rate(row),
                "estimated_impressions": _safe_impressions(row.get("estimated_impressions")),
                "engagement_rate_by_estimated_impression": pick_er_by_estimated_impression(row),
                "likes": _safe_float(row.get("likes")),
                "comments": _safe_int(row.get("comments")),
                "followers": _safe_int(row.get("followers")),
                "post_tag_ugc": _safe_int(row.get("post_tag_ugc")),
                "post_tag_contests": _safe_int(row.get("post_tag_contests")),
            }
        )
    return pd.DataFrame(rows)


def find_csv_files() -> tuple[Path | None, Path | None]:
    if not DATA_DIR.is_dir():
        return None, None
    fb = None
    ig = None
    for p in DATA_DIR.iterdir():
        if not p.is_file() or p.suffix.lower() != ".csv":
            continue
        name = p.name.lower()
        if "facebook" in name and "rivaliq" in name:
            fb = p
        if "instagram" in name and "rivaliq" in name:
            ig = p
    return fb, ig


def _avg(lst: list) -> float | None:
    if not lst:
        return None
    return sum(lst) / len(lst)


def _new_metric_bucket():
    return {
        "count": 0,
        "engagement_rates": [],
        "impressions": [],
        "er_by_imp": [],
        "by_platform": defaultdict(int),
    }


def _append_metrics(bucket: dict, row: pd.Series, erf: float | None) -> None:
    bucket["count"] += 1
    plat = row["platform"]
    bucket["by_platform"][plat] += 1
    if erf is not None:
        bucket["engagement_rates"].append(erf)
    imp = row.get("estimated_impressions")
    if imp is not None and isinstance(imp, (int, float)):
        bucket["impressions"].append(int(imp))
    eri = row.get("engagement_rate_by_estimated_impression")
    if eri is not None and isinstance(eri, (int, float)):
        bucket["er_by_imp"].append(float(eri))


def _finalize_bucket(v: dict) -> dict:
    er = v["engagement_rates"]
    imp = v["impressions"]
    eri = v["er_by_imp"]
    return {
        "count": v["count"],
        "avg_engagement_rate_audience": _avg(er),
        "median_engagement_rate_audience": _median(er),
        "avg_estimated_impressions": _avg(imp) if imp else None,
        "median_estimated_impressions": _median(imp) if imp else None,
        "posts_with_estimated_impressions": len(imp),
        "avg_engagement_rate_by_estimated_impression": _avg(eri) if eri else None,
        "median_engagement_rate_by_estimated_impression": _median(eri) if eri else None,
        "by_platform": dict(v["by_platform"]),
    }


def _pack_sorted(d: dict) -> dict:
    out = {}
    for k, v in sorted(d.items(), key=lambda x: -x[1]["count"]):
        out[k] = _finalize_bucket(v)
    return out


def aggregate_posts(df: pd.DataFrame) -> dict:
    by_macro: dict = defaultdict(_new_metric_bucket)
    by_category: dict = defaultdict(_new_metric_bucket)
    by_subcategory: dict = defaultdict(_new_metric_bucket)
    by_intent: dict = defaultdict(_new_metric_bucket)
    by_brand: dict = defaultdict(_new_metric_bucket)
    cross_macro_intent: dict = defaultdict(lambda: defaultdict(int))
    by_format: dict = defaultdict(_new_metric_bucket)
    macro_format: dict = defaultdict(lambda: defaultdict(int))

    for _, row in df.iterrows():
        er = row.get("engagement_rate_audience")
        erf = float(er) if er is not None and isinstance(er, (int, float)) else None
        cl = row.get("classification") or {}
        mid = cl.get("macro", {}).get("id", "sin_clasificar")
        cid = cl.get("category", {}).get("id", "sin_clasificar")
        sid = cl.get("subcategory", {}).get("id", "sin_clasificar")
        intent = cl.get("intent", "consideration")
        brand = str(row.get("company_canonical") or "Desconocido").strip() or "Desconocido"

        for bucket, key in (
            (by_macro, mid),
            (by_category, cid),
            (by_subcategory, sid),
            (by_intent, intent),
            (by_brand, brand),
        ):
            _append_metrics(bucket[key], row, erf)

        cross_macro_intent[mid][intent] += 1

        fmt = row.get("format_normalized", "other")
        _append_metrics(by_format[fmt], row, erf)
        macro_format[mid][fmt] += 1

    return {
        "by_macro": _pack_sorted(by_macro),
        "by_category": _pack_sorted(by_category),
        "by_subcategory": _pack_sorted(by_subcategory),
        "by_intent": _pack_sorted(by_intent),
        "by_brand_canonical": _pack_sorted(by_brand),
        "cross_macro_intent": {m: dict(sorted(iv.items())) for m, iv in sorted(cross_macro_intent.items())},
        "by_format": {
            k: _finalize_bucket(v) for k, v in sorted(by_format.items(), key=lambda x: -x[1]["count"])
        },
        "cross_macro_format": {m: dict(fm) for m, fm in sorted(macro_format.items())},
    }


def taxonomy_reference() -> dict:
    """Árbol legible para el dashboard (sin keywords)."""
    tree: dict = {}
    for mid, mlab, cid, clab, sid, slab, _ in TAXONOMY_LEAVES:
        if mid not in tree:
            tree[mid] = {"label": mlab, "categories": {}}
        if cid not in tree[mid]["categories"]:
            tree[mid]["categories"][cid] = {"label": clab, "subcategories": {}}
        tree[mid]["categories"][cid]["subcategories"][sid] = slab
    return tree


def hashtag_counts(df: pd.DataFrame, top_n: int = 40) -> list[dict]:
    if "hashtags" not in df.columns:
        return []
    freq: dict[str, int] = defaultdict(int)
    for tags in df["hashtags"]:
        if not isinstance(tags, list):
            continue
        for t in tags:
            freq[t.lower()] += 1
    sorted_items = sorted(freq.items(), key=lambda x: -x[1])[:top_n]
    return [{"tag": a, "count": b} for a, b in sorted_items]


def build_payload(
    fb_path: Path,
    ig_path: Path,
    *,
    gemini_classify: bool = False,
    gemini_mode: str = "low_conf",
    gemini_max: int = 2500,
    gemini_min_confidence: float = 0.72,
    gemini_fetch_playwright: bool = False,
    gemini_model: str | None = None,
    gemini_min_rule_score: int = 2,
    detect_status_media: bool = False,
    vision: bool = False,
    vision_max: int = 400,
    vision_provider: str = "gemini",
    vision_model: str | None = None,
) -> dict:
    load_project_dotenv()
    dfb = load_facebook(fb_path)
    dfi = load_instagram(ig_path)
    posts_df = pd.concat([dfb, dfi], ignore_index=True)

    # ── Detección og:type para posts FB status ────────────────────────────────
    # Distingue si un post tipo "status" con imagen es en realidad video o imagen
    # consultando la etiqueta og:type de la URL pública del post.
    # Cachea resultados en data/.fb_media_type_cache.json.
    if detect_status_media:
        try:
            from social_fb_media_detect import enrich_status_formats
            posts_df, _detect_stats = enrich_status_formats(posts_df, verbose=True)
        except ImportError as e:
            print(f"Advertencia: no se pudo importar social_fb_media_detect: {e}")

    gemini_meta: dict | None = None
    vision_meta: dict | None = None

    if gemini_classify:
        try:
            from social_gemini_classifier import run_gemini_taxonomy_pass
        except ImportError:
            run_gemini_taxonomy_pass = None
        gkey = resolve_vision_api_key("gemini")
        gmodel = resolve_vision_model("gemini", gemini_model)
        if not run_gemini_taxonomy_pass:
            print("Advertencia: no se pudo importar social_gemini_classifier.")
            posts_df["classification_enrichment"] = None
            gemini_meta = {"applied": False, "reason": "import_error"}
        elif not gkey:
            print("Advertencia: --gemini-classify requiere GEMINI_API_KEY o GOOGLE_API_KEY en .env.")
            posts_df["classification_enrichment"] = None
            gemini_meta = {"applied": False, "reason": "missing_api_key"}
        else:
            gm = (gemini_mode or "low_conf").strip().lower()
            if gm not in ("doubtful", "low_conf", "rules_only", "multimodal", "all"):
                gm = "low_conf"
            posts_df, gstats = run_gemini_taxonomy_pass(
                posts_df,
                api_key=gkey,
                model=gmodel,
                mode=gm,
                min_confidence=float(gemini_min_confidence),
                max_api_calls=max(1, int(gemini_max)),
                fetch_playwright=gemini_fetch_playwright,
                min_rule_score=int(gemini_min_rule_score),
            )
            gemini_meta = {
                "applied": True,
                "model": gmodel,
                "mode": gm,
                "min_confidence": float(gemini_min_confidence),
                "max_api_calls": max(1, int(gemini_max)),
                "fetch_playwright": gemini_fetch_playwright,
                "stats": gstats,
            }
            print(
                "Gemini taxonomía: aceptados",
                gstats.get("accepted"),
                "| API",
                gstats.get("api_calls"),
                "| caché",
                gstats.get("cache_hits"),
                "| baja conf.",
                gstats.get("rejected_low_confidence"),
            )
        posts_df["classification_enrichment"] = None
    elif vision:
        try:
            from social_vision_enrichment import enrich_posts_dataframe
        except ImportError:
            enrich_posts_dataframe = None
        vp = (vision_provider or "gemini").strip().lower()
        if vp not in ("gemini", "openai"):
            vp = "gemini"
        model_resolved = resolve_vision_model(vp, vision_model)
        api_key = resolve_vision_api_key(vp)
        if not enrich_posts_dataframe:
            print("Advertencia: no se pudo importar social_vision_enrichment (¿dependencias instaladas?).")
            posts_df["classification_enrichment"] = None
            vision_meta = {"applied": False, "reason": "import_error"}
        elif not api_key:
            key_hint = "GEMINI_API_KEY o GOOGLE_API_KEY" if vp == "gemini" else "OPENAI_API_KEY"
            print(
                f"Advertencia: --vision con proveedor '{vp}' pero falta API key "
                f"({key_hint} en .env o en el entorno). Ver .env.example."
            )
            posts_df["classification_enrichment"] = None
            vision_meta = {"applied": False, "reason": "missing_api_key", "provider": vp}
        else:
            posts_df, vstats = enrich_posts_dataframe(
                posts_df,
                vision_provider=vp,
                api_key=api_key,
                model=model_resolved,
                max_calls=vision_max,
                only_sin_clasificar=True,
                build_classification_fn=build_classification,
            )
            vision_meta = {
                "applied": True,
                "provider": vp,
                "model": model_resolved,
                "max_api_calls": vision_max,
                "stats": vstats,
            }
            print(
                f"Visión ({vp}): enriquecidos",
                vstats.get("posts_enriched"),
                "posts;",
                vstats.get("errors"),
                "errores;",
                vstats.get("cache_hits"),
                "caché.",
            )
    else:
        posts_df["classification_enrichment"] = None

    posts_df = posts_df.drop(columns=["_classify_text"], errors="ignore")

    brands_fb = set(dfb["company"].unique())
    brands_ig = set(dfi["company"].unique())
    all_brands = sorted(brands_fb | brands_ig)
    brands_canonical = sorted({canonical_company(str(x)) for x in all_brands})
    posts_with_imp = int(posts_df["estimated_impressions"].notna().sum())

    aggregates = aggregate_posts(posts_df)
    ig_only = posts_df[posts_df["platform"] == "instagram"]
    hashtag_top = hashtag_counts(ig_only)
    posts_records = posts_df.to_dict(orient="records")

    return {
        "meta": {
            "classification_version": (
                "3.0-gemini-hybrid"
                if gemini_meta and gemini_meta.get("applied")
                else ("2.1-framework+vision" if vision_meta and vision_meta.get("applied") else "2.0-framework")
            ),
            "gemini_classification": gemini_meta,
            "vision_enrichment": vision_meta,
            "framework": {
                "levels": [
                    "macro: rol estratégico del contenido (comercial, inspiracional, educativo, prueba_confianza, marca)",
                    "category: tipo de contenido dentro del macro",
                    "subcategory: detalle operativo para análisis fino",
                    "intent: awareness | consideration | decision (embudo)",
                ],
                "intent_definitions": {
                    "awareness": "Descubrimiento, marca, inspiración ligera sin CTA de cierre.",
                    "consideration": "Evaluación: prueba social, educación profunda, inspiración de proyecto.",
                    "decision": "Cierre: CTA fuerte, promoción, solicitud directa de contacto o compra.",
                },
            },
            "taxonomy_tree": taxonomy_reference(),
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sources": {
                "facebook": str(fb_path.relative_to(ROOT)).replace("\\", "/"),
                "instagram": str(ig_path.relative_to(ROOT)).replace("\\", "/"),
            },
            "row_counts": {"facebook": len(dfb), "instagram": len(dfi), "total": len(posts_df)},
            "brands_distinct": all_brands,
            "brands_canonical": brands_canonical,
            "posts_with_estimated_impressions": posts_with_imp,
            "notes": [
                "Export tipo 'top landscape posts' de Rival IQ: muestra priorizada, no necesariamente el universo completo.",
                "Clasificación por reglas de palabras clave (ES/EN); afinar con muestreo manual o modelo en v3.",
                "engagement_rate_audience: Facebook = tasa vs fans de página; Instagram = tasa vs seguidores (exclusivo de la tasa por impresión).",
                "estimated_impressions y engagement_rate_by_estimated_impression: modelo/estimación Rival IQ; usar como proxy de alcance, no como dato contable.",
                "company_canonical: nombres unificados (p. ej. AVIGLASS → Aviglass) para comparar competidores.",
                "sin_clasificar: ampliar keywords o usar capa ML para posts ambiguos.",
                "Opcional: --gemini-classify clasifica con Gemini (texto + imagen si la URL responde; --gemini-fetch-playwright para captura del post). Modos: doubtful | multimodal | all.",
                "Opcional: --vision solo añade descripción de imagen y re-ejecuta reglas (legacy). Con --gemini-classify se recomienda no combinar --vision.",
                "Campos por post: classification_source (rules|gemini), classification_llm (metadatos del modelo cuando aplica).",
            ],
        },
        "aggregates": aggregates,
        "hashtags_top_instagram": hashtag_top,
        "posts": posts_records,
    }


def run_social_benchmark(
    *,
    gemini_classify: bool = False,
    gemini_mode: str = "low_conf",
    gemini_max: int = 2500,
    gemini_min_confidence: float = 0.72,
    gemini_fetch_playwright: bool = False,
    gemini_model: str | None = None,
    gemini_min_rule_score: int = 2,
    detect_status_media: bool = False,
    vision: bool = False,
    vision_max: int = 400,
    vision_provider: str = "gemini",
    vision_model: str | None = None,
) -> Path | None:
    """Punto de entrada estable para otros scripts (sin leer sys.argv)."""
    load_project_dotenv()
    fb, ig = find_csv_files()
    if not fb or not ig:
        print("Social benchmark: CSV no encontrados en data/ (rivaliq * facebook.csv / instagram.csv). Omitido.")
        return None
    payload = build_payload(
        fb,
        ig,
        gemini_classify=gemini_classify,
        gemini_mode=gemini_mode,
        gemini_max=max(1, gemini_max),
        gemini_min_confidence=float(gemini_min_confidence),
        gemini_fetch_playwright=gemini_fetch_playwright,
        gemini_model=gemini_model,
        gemini_min_rule_score=int(gemini_min_rule_score),
        detect_status_media=detect_status_media,
        vision=vision,
        vision_max=max(1, vision_max),
        vision_provider=vision_provider,
        vision_model=vision_model,
    )
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Wrote:", OUT_JSON)
    return OUT_JSON


def main() -> Path | None:
    load_project_dotenv()
    default_vp = (os.environ.get("VISION_PROVIDER", "gemini").strip().lower() or "gemini")
    if default_vp not in ("gemini", "openai"):
        default_vp = "gemini"
    default_gmode = (os.environ.get("GEMINI_CLASSIFY_MODE", "low_conf").strip().lower() or "low_conf")
    if default_gmode not in ("doubtful", "low_conf", "rules_only", "multimodal", "all"):
        default_gmode = "low_conf"
    ap = argparse.ArgumentParser(description="Genera dashboard/social_data.json desde CSV Rival IQ.")
    ap.add_argument(
        "--gemini-classify",
        action="store_true",
        help="Clasificar con Gemini (JSON + taxonomía cerrada); texto + imagen si hay URL o captura.",
    )
    ap.add_argument(
        "--gemini-mode",
        choices=["doubtful", "low_conf", "rules_only", "multimodal", "all"],
        default=default_gmode,
        help=(
            "doubtful=solo sin_clasificar; "
            "low_conf=sin_clasificar + reglas score=1; "
            "rules_only=todos los clasificados por reglas (NO reclasifica los ya hechos por Gemini); "
            "multimodal=formatos imagen/video/reel/carousel + sin_clasificar; "
            "all=todos."
        ),
    )
    ap.add_argument(
        "--gemini-min-rule-score",
        type=int,
        default=int(os.environ.get("GEMINI_MIN_RULE_SCORE", "2")),
        help="En modo low_conf: score mínimo de reglas para NO enviar a Gemini (default 2, es decir score=1 sí va).",
    )
    ap.add_argument("--gemini-max", type=int, default=2500, help="Máximo de llamadas API Gemini (caché no cuenta).")
    ap.add_argument(
        "--gemini-min-confidence",
        type=float,
        default=float(os.environ.get("GEMINI_MIN_CONFIDENCE", "0.72")),
        help="Si confidence del modelo es menor, se mantienen reglas.",
    )
    ap.add_argument(
        "--gemini-fetch-playwright",
        action="store_true",
        help="Si falla la URL de image, capturar viewport del post_link con Playwright.",
    )
    ap.add_argument(
        "--gemini-model",
        type=str,
        default=None,
        help="Modelo Gemini; por defecto VISION_MODEL o gemini-2.0-flash.",
    )
    ap.add_argument(
        "--detect-status-media",
        action="store_true",
        help=(
            "Para posts FB con post_type=status, detecta vía og:type si son video o imagen. "
            "Cachea resultados en data/.fb_media_type_cache.json. "
            "Recomendado combinarlo con --gemini-classify --gemini-mode multimodal."
        ),
    )
    ap.add_argument(
        "--vision",
        action="store_true",
        help="Legacy: solo descripción de imagen + reglas (no combinar con --gemini-classify).",
    )
    ap.add_argument("--vision-max", type=int, default=400, help="Máximo de llamadas API (no cuenta caché).")
    ap.add_argument(
        "--vision-provider",
        choices=["gemini", "openai"],
        default=default_vp,
        help="Proveedor de visión (por defecto: variable VISION_PROVIDER en .env o gemini).",
    )
    ap.add_argument(
        "--vision-model",
        type=str,
        default=None,
        help="Modelo explícito; si se omite, usa VISION_MODEL en .env o el default del proveedor.",
    )
    args = ap.parse_args()
    if args.gemini_classify and args.vision:
        print("Advertencia: --gemini-classify y --vision a la vez; se ignora --vision.")
        args.vision = False
    return run_social_benchmark(
        gemini_classify=args.gemini_classify,
        gemini_mode=args.gemini_mode,
        gemini_max=args.gemini_max,
        gemini_min_confidence=args.gemini_min_confidence,
        gemini_fetch_playwright=args.gemini_fetch_playwright,
        gemini_model=args.gemini_model,
        gemini_min_rule_score=args.gemini_min_rule_score,
        detect_status_media=args.detect_status_media,
        vision=args.vision,
        vision_max=args.vision_max,
        vision_provider=args.vision_provider,
        vision_model=args.vision_model,
    )


if __name__ == "__main__":
    main()
