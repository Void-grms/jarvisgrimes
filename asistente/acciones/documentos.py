"""Documentos por voz: Word, PDF, PowerPoint y Excel.

El modelo local escribe el contenido en JSON (con esquema fijo, así no se "sale"
del formato) y aquí se convierte en un archivo con buen formato.
"""
from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..texto import MESES, normalizar, numeros_a_digitos

log = logging.getLogger("documentos")

TIPOS = {"docx": "un documento de Word", "pdf": "un PDF", "pptx": "una presentación",
         "xlsx": "un Excel"}
EXT = {"docx": ".docx", "pdf": ".pdf", "pptx": ".pptx", "xlsx": ".xlsx"}

ESQUEMA_DOC = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string"},
        "secciones": {"type": "array", "items": {
            "type": "object",
            "properties": {"titulo": {"type": "string"},
                           "parrafos": {"type": "array", "items": {"type": "string"}}},
            "required": ["titulo", "parrafos"]}},
    },
    "required": ["titulo", "secciones"],
}
ESQUEMA_PPT = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string"},
        "subtitulo": {"type": "string"},
        "diapositivas": {"type": "array", "items": {
            "type": "object",
            "properties": {"titulo": {"type": "string"},
                           "puntos": {"type": "array", "items": {"type": "string"}},
                           "notas": {"type": "string"}},
            "required": ["titulo", "puntos"]}},
    },
    "required": ["titulo", "diapositivas"],
}
ESQUEMA_XLSX = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string"},
        "columnas": {"type": "array", "items": {
            "type": "object",
            "properties": {"nombre": {"type": "string"},
                           "tipo": {"type": "string",
                                    "enum": ["texto", "numero", "moneda", "fecha", "porcentaje"]}},
            "required": ["nombre", "tipo"]}},
        "filas": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
    },
    "required": ["titulo", "columnas", "filas"],
}

SISTEMA_DOCS = ("Eres un redactor profesional. Escribes siempre en español, claro y bien "
                "organizado. Nunca inventes datos personales, cifras exactas, citas ni referencias "
                "bibliográficas; si algo depende del usuario, deja un marcador entre corchetes, "
                "por ejemplo [nombre] o [fecha]. Responde solo con el JSON pedido.")


@dataclass
class Pedido:
    tipo: str               # docx | pdf | pptx | xlsx
    clase: str = ""         # informe, carta, ensayo, presentación...
    tema: str = ""
    detalles: str = ""
    extra: dict = field(default_factory=dict)

    def descripcion(self) -> str:
        base = TIPOS[self.tipo]
        return f"{base} sobre {self.tema}" if self.tema else base


def detectar_tipo(txt_norm: str, clase: str, formato: str) -> str:
    f = normalizar(formato or "")
    c = normalizar(clase or "")
    if re.search(r"\b(?:en|a|como|formato) pdf\b", txt_norm) or "pdf" in (f, c):
        return "pdf"
    if f in ("word",):
        return "docx"
    if re.search(r"power ?point|ppt|diapositiva|presentacion|slides", f + " " + c):
        return "pptx"
    if re.search(r"excel|hoja de calculo|planilla|tabla|cuadro|base de datos", f + " " + c):
        return "xlsx"
    return "docx"


def _numero_en(texto: str, palabra: str) -> int | None:
    m = re.search(rf"\b(\d+) {palabra}", numeros_a_digitos(normalizar(texto)))
    return int(m.group(1)) if m else None


def armar_prompt(p: Pedido) -> tuple[str, dict, int]:
    """(prompt, esquema, max_tokens) para el modelo."""
    clase = p.clase or "documento"
    detalles = p.detalles.strip() or "ninguno; decide tú lo más útil"
    if p.tipo in ("docx", "pdf"):
        paginas = _numero_en(p.detalles, "paginas?") or _numero_en(p.detalles, "hojas?")
        palabras = min(2500, (paginas or 1.5) * 450)
        prompt = (f"Escribe {clase} sobre: {p.tema}.\nDetalles del usuario: {detalles}.\n"
                  f"Extensión objetivo: unas {int(palabras)} palabras.\n"
                  "Devuelve un título y secciones (cada una con título y párrafos completos). "
                  "Si es una carta, oficio o solicitud, usa el formato propio de ese documento.")
        return prompt, ESQUEMA_DOC, int(palabras * 2.2) + 300
    if p.tipo == "pptx":
        n = _numero_en(p.detalles, "diapositivas?") or _numero_en(p.tema, "diapositivas?") or 6
        n = max(3, min(15, n))
        prompt = (f"Crea el contenido de una presentación sobre: {p.tema}.\nDetalles del usuario: "
                  f"{detalles}.\nHaz exactamente {n} diapositivas de contenido (sin contar la portada), "
                  "cada una con un título corto, de 3 a 5 puntos breves y notas para el expositor. "
                  "Incluye un subtítulo para la portada.")
        return prompt, ESQUEMA_PPT, n * 170 + 300
    prompt = (f"Diseña una hoja de cálculo para: {p.tema}.\nDetalles del usuario: {detalles}.\n"
              "Define las columnas con su tipo (texto, numero, moneda, fecha o porcentaje). "
              "Si es una plantilla para llenar o un control (gastos, tareas, inventario), deja "
              "'filas' vacío. Si el pedido es de datos concretos que conoces con certeza, "
              "inclúyelos como filas (todo como texto). Las fechas en formato DD/MM/AAAA.")
    return prompt, ESQUEMA_XLSX, 1200


# ---------------------------------------------------------------- utilidades de archivo
def nombre_archivo(carpeta: Path, titulo: str, ext: str) -> Path:
    limpio = re.sub(r'[<>:"/\\|?*\n\r\t]+', " ", titulo).strip(" .")[:70] or "Documento"
    base = f"{datetime.now():%Y-%m-%d} {limpio}"
    ruta = carpeta / f"{base}{ext}"
    n = 2
    while ruta.exists():
        ruta = carpeta / f"{base} ({n}){ext}"
        n += 1
    return ruta


def fecha_larga() -> str:
    d = datetime.now()
    return f"{d.day} de {MESES[d.month - 1]} de {d.year}"


# ---------------------------------------------------------------- Word
def escribir_docx(c: dict, ruta: Path) -> Path:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    doc = Document()
    estilo = doc.styles["Normal"]
    estilo.font.name = "Calibri"
    estilo.font.size = Pt(11)
    titulo = doc.add_heading(c.get("titulo", "Documento"), level=0)
    titulo.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fecha = doc.add_paragraph(fecha_larga())
    fecha.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fecha.runs[0].font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    for s in c.get("secciones", []):
        if s.get("titulo"):
            doc.add_heading(s["titulo"], level=1)
        for p in s.get("parrafos", []):
            par = doc.add_paragraph(p.strip())
            par.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            par.paragraph_format.space_after = Pt(8)
    doc.save(ruta)
    return ruta


# ---------------------------------------------------------------- PDF
_FUENTES = [(r"C:\Windows\Fonts\calibri.ttf", r"C:\Windows\Fonts\calibrib.ttf"),
            (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
            ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")]


def escribir_pdf(c: dict, ruta: Path) -> Path:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    pdf = FPDF(format="A4")
    pdf.set_margins(25, 22, 25)
    pdf.set_auto_page_break(True, 22)
    pdf.set_title(c.get("titulo", "Documento"))
    fuente = "Helvetica"
    for normal, negrita in _FUENTES:
        if Path(normal).exists() and Path(negrita).exists():
            pdf.add_font("Doc", "", normal)
            pdf.add_font("Doc", "B", negrita)
            fuente = "Doc"
            break

    def txt(s: str) -> str:
        if fuente == "Doc":
            return s
        reemplazos = {"“": '"', "”": '"', "‘": "'", "’": "'", "—": "-", "–": "-", "…": "..."}
        for a, b in reemplazos.items():
            s = s.replace(a, b)
        return s.encode("latin-1", "replace").decode("latin-1")

    sig = dict(new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.add_page()
    pdf.set_font(fuente, "B", 20)
    pdf.set_text_color(31, 58, 95)
    pdf.multi_cell(0, 10, txt(c.get("titulo", "Documento")), align="C", **sig)
    pdf.set_font(fuente, "", 10)
    pdf.set_text_color(110, 110, 110)
    pdf.multi_cell(0, 6, txt(fecha_larga()), align="C", **sig)
    pdf.ln(6)
    for s in c.get("secciones", []):
        if s.get("titulo"):
            pdf.set_font(fuente, "B", 13)
            pdf.set_text_color(31, 58, 95)
            pdf.multi_cell(0, 8, txt(s["titulo"]), **sig)
            pdf.ln(1)
        pdf.set_font(fuente, "", 11)
        pdf.set_text_color(30, 30, 30)
        for p in s.get("parrafos", []):
            pdf.multi_cell(0, 6, txt(p.strip()), align="J", **sig)
            pdf.ln(3)
        pdf.ln(2)
    pdf.output(str(ruta))
    return ruta


# ---------------------------------------------------------------- PowerPoint
def escribir_pptx(c: dict, ruta: Path) -> Path:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches, Pt

    oscuro, acento, claro = RGBColor(0x1F, 0x3A, 0x5F), RGBColor(0x2E, 0x86, 0xDE), RGBColor(0xFF, 0xFF, 0xFF)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    vacia = prs.slide_layouts[6]

    def fondo(slide, color):
        f = slide.background.fill
        f.solid()
        f.fore_color.rgb = color

    def caja(slide, x, y, w, h, texto, tam, color, negrita=False):
        tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.text = texto
        for par in tf.paragraphs:
            for r in par.runs:
                r.font.size, r.font.bold, r.font.color.rgb = Pt(tam), negrita, color
        return tf

    # Portada
    s = prs.slides.add_slide(vacia)
    fondo(s, oscuro)
    barra = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.8), Inches(3.55), Inches(1.6), Inches(0.08))
    barra.fill.solid()
    barra.fill.fore_color.rgb = acento
    barra.line.fill.background()
    caja(s, 0.8, 1.6, 11.7, 1.9, c.get("titulo", "Presentación"), 40, claro, True)
    caja(s, 0.8, 3.85, 11.7, 1.2, c.get("subtitulo", ""), 20, RGBColor(0xC9, 0xD6, 0xE8))
    caja(s, 0.8, 6.4, 11.7, 0.5, fecha_larga(), 12, RGBColor(0xC9, 0xD6, 0xE8))

    for n, d in enumerate(c.get("diapositivas", []), start=2):
        s = prs.slides.add_slide(vacia)
        fondo(s, claro)
        franja = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, Inches(0.12))
        franja.fill.solid()
        franja.fill.fore_color.rgb = acento
        franja.line.fill.background()
        caja(s, 0.7, 0.45, 11.9, 1.1, d.get("titulo", ""), 30, oscuro, True)
        tf = s.shapes.add_textbox(Inches(0.9), Inches(1.75), Inches(11.5), Inches(5.0)).text_frame
        tf.word_wrap = True
        for i, punto in enumerate(d.get("puntos", [])):
            par = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            par.text = f"•  {punto.strip()}"
            par.space_after = Pt(14)
            for r in par.runs:
                r.font.size, r.font.color.rgb = Pt(22), RGBColor(0x33, 0x33, 0x33)
        caja(s, 12.2, 6.9, 0.8, 0.4, str(n), 11, RGBColor(0x99, 0x99, 0x99))
        if d.get("notas"):
            s.notes_slide.notes_text_frame.text = d["notas"]
    prs.save(ruta)
    return ruta


# ---------------------------------------------------------------- Excel
FORMATOS = {"moneda": '"S/" #,##0.00', "numero": "#,##0.##", "fecha": "DD/MM/YYYY",
            "porcentaje": "0%"}


def _convertir(valor: str, tipo: str):
    v = (valor or "").strip()
    if not v:
        return None
    if tipo in ("numero", "moneda", "porcentaje"):
        limpio = re.sub(r"[^\d,.\-]", "", v)
        if limpio.count(",") and limpio.count("."):
            limpio = limpio.replace(",", "")
        elif limpio.count(",") == 1 and len(limpio.split(",")[1]) != 3:
            limpio = limpio.replace(",", ".")
        else:
            limpio = limpio.replace(",", "")
        try:
            n = float(limpio)
            if tipo == "porcentaje" and ("%" in v or n > 1):
                n /= 100
            return int(n) if n.is_integer() and tipo == "numero" else n
        except ValueError:
            return v
    if tipo == "fecha":
        for f in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y"):
            try:
                return datetime.strptime(v, f)
            except ValueError:
                pass
    return v


def escribir_xlsx(c: dict, ruta: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    columnas = c.get("columnas") or [{"nombre": "Dato", "tipo": "texto"}]
    filas = c.get("filas") or []
    wb = Workbook()
    ws = wb.active
    ws.title = re.sub(r"[\[\]:*?/\\]", "", c.get("titulo", "Hoja"))[:31] or "Hoja"
    ncol = len(columnas)
    ultima = get_column_letter(ncol)
    borde = Border(*(Side(style="thin", color="D0D7E2"),) * 4)

    ws["A1"] = c.get("titulo", "")
    ws["A1"].font = Font(size=15, bold=True, color="1F3A5F")
    if ncol > 1:
        ws.merge_cells(f"A1:{ultima}1")
    ws["A2"] = f"Creado el {fecha_larga()}"
    ws["A2"].font = Font(size=9, italic=True, color="888888")

    cab = 4
    for j, col in enumerate(columnas, start=1):
        celda = ws.cell(row=cab, column=j, value=col["nombre"])
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = PatternFill("solid", fgColor="1F3A5F")
        celda.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        celda.border = borde

    n_filas = len(filas) if filas else 30  # plantilla vacía: 30 filas listas para llenar
    for i in range(n_filas):
        fila = filas[i] if i < len(filas) else []
        for j, col in enumerate(columnas, start=1):
            valor = _convertir(fila[j - 1], col["tipo"]) if j - 1 < len(fila) else None
            celda = ws.cell(row=cab + 1 + i, column=j, value=valor)
            celda.border = borde
            if col["tipo"] in FORMATOS:
                celda.number_format = FORMATOS[col["tipo"]]
            if i % 2:
                celda.fill = PatternFill("solid", fgColor="F3F6FA")

    numericas = [j for j, col in enumerate(columnas, start=1) if col["tipo"] in ("numero", "moneda")]
    if numericas:
        tot = cab + n_filas + 1
        ws.cell(row=tot, column=1, value="Total").font = Font(bold=True)
        for j in numericas:
            letra = get_column_letter(j)
            celda = ws.cell(row=tot, column=j, value=f"=SUM({letra}{cab + 1}:{letra}{tot - 1})")
            celda.font = Font(bold=True)
            celda.number_format = FORMATOS[columnas[j - 1]["tipo"]]
            celda.border = Border(top=Side(style="medium", color="1F3A5F"))

    for j, col in enumerate(columnas, start=1):
        largos = [len(str(col["nombre"]))] + [len(str(f[j - 1])) for f in filas if j - 1 < len(f)]
        ws.column_dimensions[get_column_letter(j)].width = min(50, max(12, max(largos) + 3))
    ws.freeze_panes = ws.cell(row=cab + 1, column=1)
    ws.auto_filter.ref = f"A{cab}:{ultima}{cab + n_filas}"
    wb.save(ruta)
    return ruta


ESCRITORES = {"docx": escribir_docx, "pdf": escribir_pdf, "pptx": escribir_pptx, "xlsx": escribir_xlsx}


class Documentos:
    def __init__(self, carpeta: Path, cerebro, modelo: str = "",
                 abrir: Callable[[Path], None] | None = None):
        self.carpeta = carpeta
        self.cerebro = cerebro
        self.modelo = modelo or None
        self.abrir = abrir

    def crear(self, p: Pedido) -> Path:
        prompt, esquema, max_tokens = armar_prompt(p)
        contenido = self.cerebro.generar_json(prompt, esquema, sistema=SISTEMA_DOCS,
                                              modelo=self.modelo, max_tokens=max_tokens)
        self.carpeta.mkdir(parents=True, exist_ok=True)
        ruta = nombre_archivo(self.carpeta, contenido.get("titulo") or p.tema or "Documento", EXT[p.tipo])
        ESCRITORES[p.tipo](contenido, ruta)
        return ruta

    def crear_en_fondo(self, p: Pedido, avisar: Callable[[str], None]) -> None:
        def trabajo():
            try:
                ruta = self.crear(p)
                if self.abrir:
                    self.abrir(ruta)
                avisar(f"Listo, terminé {TIPOS[p.tipo]}: {ruta.stem[11:]}. Está en Documentos, "
                       "carpeta Jarvis, y te lo abrí.")
            except Exception as e:
                log.exception("Falló el documento")
                avisar(f"No pude crear el documento. {str(e)[:120]}")

        threading.Thread(target=trabajo, daemon=True).start()
