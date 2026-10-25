"""Generación del reporte PDF de una inspección termográfica (ReportLab).

El reporte incluye encabezado con logo configurable, datos generales,
imagen visible y térmica, temperaturas, ΔT, clasificación ANSI/NETA MTS,
recomendación, notas del inspector y el marco normativo (NOM-029-STPS-2011,
NFPA 70B y ANSI/NETA MTS).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepTogether, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

from ..logica.analisis_termico import CLASIFICACIONES

log = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parent.parent
LOGO_DEFECTO = RAIZ / "web" / "static" / "img" / "logo.png"

_RUTAS_FUENTES = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/TTF/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
]


def _registrar_fuentes() -> tuple[str, str, bool]:
    """Registra una fuente TrueType con soporte de 'Δ' y '°'. Devuelve (normal, negrita, unicode)."""
    for normal, negrita in _RUTAS_FUENTES:
        if Path(normal).exists() and Path(negrita).exists():
            try:
                if "RptNormal" not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont("RptNormal", normal))
                    pdfmetrics.registerFont(TTFont("RptNegrita", negrita))
                    # Familia: permite <b> e <i> dentro de los párrafos
                    pdfmetrics.registerFontFamily("RptNormal", normal="RptNormal", bold="RptNegrita",
                                                  italic="RptNormal", boldItalic="RptNegrita")
                return "RptNormal", "RptNegrita", True
            except Exception:  # pragma: no cover
                log.warning("No se pudo registrar la fuente %s", normal)
    return "Helvetica", "Helvetica-Bold", False


def _hex(color: str) -> colors.Color:
    return colors.HexColor(color)


def _imagen_ajustada(ruta: Path | None, ancho_max: float, alto_max: float) -> Image | Paragraph:
    if ruta is None or not Path(ruta).is_file():
        return Paragraph("(imagen no disponible)", getSampleStyleSheet()["Normal"])
    w, h = ImageReader(str(ruta)).getSize()
    escala = min(ancho_max / w, alto_max / h)
    return Image(str(ruta), width=w * escala, height=h * escala)


def generar_reporte(insp: dict, cfg_reporte: dict, umbrales: dict, ruta_salida: Path,
                    dir_capturas: Path, nombre_robot: str = "Robot de inspección térmica") -> Path:
    """Crea el PDF de la inspección ``insp`` (diccionario de la base de datos)."""
    fuente, fuente_b, unicode_ok = _registrar_fuentes()
    delta = "ΔT" if unicode_ok else "Delta T"
    ruta_salida = Path(ruta_salida)
    ruta_salida.parent.mkdir(parents=True, exist_ok=True)

    estilos = getSampleStyleSheet()
    normal = ParagraphStyle("n", parent=estilos["Normal"], fontName=fuente, fontSize=9.5, leading=13)
    pequeno = ParagraphStyle("p", parent=normal, fontSize=8, leading=10.5, textColor=colors.HexColor("#444444"))
    titulo = ParagraphStyle("t", parent=normal, fontName=fuente_b, fontSize=15, leading=19,
                            spaceAfter=4, textColor=colors.HexColor("#14213d"))
    seccion = ParagraphStyle("s", parent=normal, fontName=fuente_b, fontSize=11, leading=14,
                             spaceBefore=10, spaceAfter=5, textColor=colors.HexColor("#14213d"))
    centrado = ParagraphStyle("c", parent=pequeno, alignment=TA_CENTER)

    logo = LOGO_DEFECTO
    if cfg_reporte.get("logo"):
        propio = Path(cfg_reporte["logo"])
        propio = propio if propio.is_absolute() else RAIZ.parent / propio
        if propio.is_file():
            logo = propio
    empresa = cfg_reporte.get("empresa", "")
    titulo_rep = cfg_reporte.get("titulo", "Reporte de Inspección Termográfica")
    generado = datetime.now().strftime("%d/%m/%Y %H:%M")

    def encabezado_pie(canvas, doc):
        canvas.saveState()
        ancho, alto = letter
        y = alto - 1.2 * cm
        if logo.exists():
            canvas.drawImage(str(logo), 1.8 * cm, y - 1.3 * cm, width=1.5 * cm, height=1.5 * cm,
                             preserveAspectRatio=True, mask="auto")
        canvas.setFont(fuente_b, 12)
        canvas.setFillColor(colors.HexColor("#14213d"))
        canvas.drawString(3.6 * cm, y - 0.45 * cm, empresa)
        canvas.setFont(fuente, 9.5)
        canvas.setFillColor(colors.HexColor("#555555"))
        canvas.drawString(3.6 * cm, y - 0.95 * cm, titulo_rep)
        canvas.drawRightString(ancho - 1.8 * cm, y - 0.45 * cm, f"Folio: INS-{insp['id']:05d}")
        canvas.drawRightString(ancho - 1.8 * cm, y - 0.95 * cm, f"Fecha: {_fecha(insp['fecha'])}")
        canvas.setStrokeColor(colors.HexColor("#fca311"))
        canvas.setLineWidth(2)
        canvas.line(1.8 * cm, y - 1.5 * cm, ancho - 1.8 * cm, y - 1.5 * cm)
        canvas.setFont(fuente, 7.5)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawString(1.8 * cm, 1.1 * cm, f"Generado por {nombre_robot} · {generado}")
        canvas.drawRightString(ancho - 1.8 * cm, 1.1 * cm, f"Página {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(ruta_salida), pagesize=letter, leftMargin=1.8 * cm,
                            rightMargin=1.8 * cm, topMargin=3.2 * cm, bottomMargin=1.8 * cm,
                            title=f"{titulo_rep} INS-{insp['id']:05d}", author=empresa)
    ancho_util = letter[0] - 3.6 * cm
    elementos: list = []

    elementos.append(Paragraph(f"{titulo_rep}", titulo))
    if insp.get("simulacion"):
        elementos.append(Paragraph("<font color='#b5651d'>Datos generados en MODO SIMULACIÓN "
                                   "(sin hardware real).</font>", pequeno))
    elementos.append(Spacer(1, 6))

    # --- Datos generales -----------------------------------------------------
    estilo_tabla = TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), fuente), ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 0), (0, -1), fuente_b), ("FONTNAME", (2, 0), (2, -1), fuente_b),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef1f6")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#eef1f6")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#c5ccd8")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ])
    datos = [
        ["Tablero", insp.get("tablero", ""), "Fecha y hora", _fecha(insp["fecha"])],
        ["Ubicación", insp.get("ubicacion") or "—", "Responsable", cfg_reporte.get("responsable", "")],
        ["Equipo", "MLX90640 32×24, FOV 55°", "Emisividad (ε)", f"{insp['emisividad']:.2f}"],
    ]
    t = Table(datos, colWidths=[2.6 * cm, ancho_util / 2 - 2.6 * cm, 2.9 * cm, ancho_util / 2 - 2.9 * cm])
    t.setStyle(estilo_tabla)
    elementos.append(t)

    # --- Imágenes ------------------------------------------------------------
    elementos.append(Paragraph("Registro fotográfico y termográfico", seccion))
    col = ancho_util / 2 - 0.2 * cm
    def ruta_captura(clave: str) -> Path | None:
        return dir_capturas / insp[clave] if insp.get(clave) else None

    img_vis = _imagen_ajustada(ruta_captura("img_visible"), col, 6.6 * cm)
    img_ter = _imagen_ajustada(ruta_captura("img_termica"), col, 6.6 * cm)
    t = Table([[img_vis, img_ter],
               [Paragraph("Imagen visible", centrado),
                Paragraph("Termograma (recuadro: punto más caliente; REF: referencia)", centrado)]],
              colWidths=[ancho_util / 2] * 2)
    t.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    elementos.append(t)

    # --- Resultados ----------------------------------------------------------
    modos = {"mediana": "Mediana del cuadro", "punto": "Punto seleccionado", "manual": "Valor manual"}
    clasif = CLASIFICACIONES[int(insp["nivel"])]
    resultados = [
        ["Temperatura máxima", f"{insp['t_max']:.1f} °C",
         "Temperatura de referencia", f"{insp['t_referencia']:.1f} °C"],
        ["Temperatura mínima", f"{insp['t_min']:.1f} °C",
         "Método de referencia", modos.get(insp["modo_referencia"], insp["modo_referencia"])],
        ["Temperatura media", f"{insp['t_media']:.1f} °C",
         f"{delta} (máx − ref)", f"{insp['delta_t']:.1f} °C"],
    ]
    t = Table(resultados, colWidths=[4.3 * cm, ancho_util / 2 - 4.3 * cm, 5.4 * cm, ancho_util / 2 - 5.4 * cm])
    t.setStyle(estilo_tabla)
    t.setStyle(TableStyle([("FONTNAME", (3, 2), (3, 2), fuente_b), ("FONTSIZE", (3, 2), (3, 2), 11)]))
    bloque = [Paragraph("Resultados", seccion), t, Spacer(1, 8)]

    color_txt = "#3d3200" if clasif.nivel == 1 else "white"
    caja = Table([[Paragraph(f"<font color='{color_txt}'>CLASIFICACIÓN: {clasif.etiqueta.upper()} "
                             f"({delta} = {insp['delta_t']:.1f} °C)</font>",
                             ParagraphStyle("cl", parent=normal, fontName=fuente_b, fontSize=12,
                                            leading=15, alignment=TA_CENTER))]], colWidths=[ancho_util])
    caja.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), _hex(clasif.color)),
                              ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    bloque += [caja, Spacer(1, 6), Paragraph(f"<b>Recomendación:</b> {clasif.recomendacion}", normal)]
    elementos.append(KeepTogether(bloque))

    # --- Criterio ANSI/NETA --------------------------------------------------
    u_p, u_r, u_i = umbrales["posible"], umbrales["reparar"], umbrales["inmediata"]
    filas = [[delta, "Clasificación", "Acción sugerida"],
             [f"< {u_p:g} °C", CLASIFICACIONES[0].etiqueta, "Ninguna; mantenimiento rutinario"],
             [f"{u_p:g} a < {u_r:g} °C", CLASIFICACIONES[1].etiqueta, "Investigar y dar seguimiento"],
             [f"{u_r:g} a {u_i:g} °C", CLASIFICACIONES[2].etiqueta, "Reparar en paro programado"],
             [f"> {u_i:g} °C", CLASIFICACIONES[3].etiqueta, "Reparar de inmediato"]]
    t = Table(filas, colWidths=[3.3 * cm, 6.0 * cm, ancho_util - 9.3 * cm])
    estilo = [("FONTNAME", (0, 0), (-1, -1), fuente), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
              ("FONTNAME", (0, 0), (-1, 0), fuente_b),
              ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#14213d")),
              ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
              ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#c5ccd8")),
              ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    for c in CLASIFICACIONES:
        estilo.append(("BACKGROUND", (0, c.nivel + 1), (0, c.nivel + 1), _hex(c.color)))
    fila_actual = clasif.nivel + 1
    estilo += [("FONTNAME", (0, fila_actual), (-1, fila_actual), fuente_b),
               ("BOX", (0, fila_actual), (-1, fila_actual), 1.6, colors.HexColor("#14213d"))]
    t.setStyle(TableStyle(estilo))
    elementos.append(KeepTogether([
        Paragraph(f"Criterio de evaluación ({delta}, ANSI/NETA MTS)", seccion),
        Paragraph(f"{delta} entre el punto más caliente y un componente de referencia similar bajo "
                  "carga similar (tabla de acciones sugeridas para inspección termográfica).", pequeno),
        Spacer(1, 4), t]))

    # --- Notas ---------------------------------------------------------------
    notas = (insp.get("notas") or "").strip() or "Sin observaciones."
    elementos.append(Paragraph("Notas del inspector", seccion))
    elementos.append(Paragraph(notas.replace("\n", "<br/>"), normal))

    # --- Marco normativo -----------------------------------------------------
    elementos.append(Paragraph("Marco normativo de referencia", seccion))
    for texto in (
        "<b>NOM-029-STPS-2011</b>, <i>Mantenimiento de las instalaciones eléctricas en los centros "
        "de trabajo – Condiciones de seguridad</i>. Establece las condiciones de seguridad para las "
        "actividades de mantenimiento de instalaciones eléctricas, incluyendo la elaboración de un "
        "plan de trabajo, procedimientos de seguridad y el registro de resultados. El presente "
        "reporte documenta la revisión termográfica como evidencia de dicho mantenimiento.",
        "<b>NFPA 70B</b>, <i>Standard for Electrical Equipment Maintenance</i> (edición 2023). "
        "Incluye la inspección termográfica infrarroja dentro del programa de mantenimiento de "
        "equipo eléctrico (EMP), con una frecuencia que depende de la condición del equipo "
        "(típicamente al menos una vez al año).",
        "<b>ANSI/NETA MTS</b>, <i>Standard for Maintenance Testing Specifications for Electrical "
        "Power Equipment &amp; Systems</i>. Proporciona el criterio de clasificación por diferencia "
        "de temperatura (ΔT) usado en este reporte.".replace("ΔT", delta),
    ):
        elementos.append(Paragraph("• " + texto, normal))
        elementos.append(Spacer(1, 3))
    elementos.append(Paragraph(
        "<b>Limitaciones:</b> el sensor MLX90640 tiene resolución de 32×24 píxeles y exactitud "
        "típica de ±1 °C, por lo que esta inspección es de carácter comparativo. Los hallazgos "
        "clasificados como «Reparar» o «Reparación inmediata» deben confirmarse con una cámara "
        "termográfica calibrada, preferentemente con el equipo operando al menos al 40 % de su "
        "carga nominal. Durante la inspección deben respetarse las distancias de seguridad y el "
        "equipo de protección personal aplicable (NOM-029-STPS / NFPA 70E).", pequeno))

    # --- Firmas --------------------------------------------------------------
    firmas = Table([["", "", ""],
                    ["Inspeccionó", "", "Revisó / Aprobó"],
                    [cfg_reporte.get("responsable", ""), "", ""]],
                   colWidths=[7 * cm, ancho_util - 14 * cm, 7 * cm], rowHeights=[1.6 * cm, None, None])
    firmas.setStyle(TableStyle([("LINEABOVE", (0, 1), (0, 1), 0.8, colors.black),
                                ("LINEABOVE", (2, 1), (2, 1), 0.8, colors.black),
                                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                                ("FONTNAME", (0, 0), (-1, -1), fuente), ("FONTSIZE", (0, 0), (-1, -1), 9)]))
    elementos.append(KeepTogether([Spacer(1, 14), firmas]))

    doc.build(elementos, onFirstPage=encabezado_pie, onLaterPages=encabezado_pie)
    return ruta_salida


def _fecha(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M:%S")
    except (TypeError, ValueError):
        return str(iso)
