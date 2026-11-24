"""Navegador: perfiles de Chrome/Edge, búsquedas, YouTube y asistentes de IA."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import webbrowser
from pathlib import Path
from urllib.parse import quote_plus

import requests

from ..texto import normalizar, numeros_a_digitos
from .pestana_propia import Apertura
from .sistema import ES_WINDOWS, copiar

log = logging.getLogger("navegador")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0 Safari/537.36")

# Servicio → (nombre hablado, URL con {q} o None si no acepta la pregunta en la URL, URL de inicio)
SERVICIOS = {
    "google": ("Google", "https://www.google.com/search?q={q}", "https://www.google.com/"),
    "google_ia": ("la IA de Google", "https://www.google.com/search?udm=50&q={q}",
                  "https://www.google.com/search?udm=50"),
    "youtube": ("YouTube", "https://www.youtube.com/results?search_query={q}", "https://www.youtube.com/"),
    "chatgpt": ("ChatGPT", "https://chatgpt.com/?q={q}", "https://chatgpt.com/"),
    "claude": ("Claude", "https://claude.ai/new?q={q}", "https://claude.ai/new"),
    "gemini": ("Gemini", None, "https://gemini.google.com/app"),
    "perplexity": ("Perplexity", "https://www.perplexity.ai/search?q={q}", "https://www.perplexity.ai/"),
    "copilot": ("Copilot", "https://copilot.microsoft.com/?q={q}", "https://copilot.microsoft.com/"),
    "deepseek": ("DeepSeek", None, "https://chat.deepseek.com/"),
}

SITIOS = {
    "gmail": "https://mail.google.com/", "correo": "https://mail.google.com/",
    "mi correo": "https://mail.google.com/", "el correo": "https://mail.google.com/",
    "clementina": "https://uvcv.edu.pe/my/courses.php", "uvcv": "https://uvcv.edu.pe/my/courses.php",
    "ucv virtual": "https://uvcv.edu.pe/my/courses.php", "aula virtual": "https://uvcv.edu.pe/my/courses.php",
    "campus virtual": "https://uvcv.edu.pe/my/courses.php", "mis cursos": "https://uvcv.edu.pe/my/courses.php",
    "maestria": "https://uvcv.edu.pe/my/courses.php", "la maestria": "https://uvcv.edu.pe/my/courses.php",
    "calendario de clementina": "https://uvcv.edu.pe/calendar/view.php?view=upcoming",
    "drive": "https://drive.google.com/", "google drive": "https://drive.google.com/",
    "calendario": "https://calendar.google.com/", "google calendar": "https://calendar.google.com/",
    "google fotos": "https://photos.google.com/", "google docs": "https://docs.google.com/",
    "documentos de google": "https://docs.google.com/", "google sheets": "https://sheets.google.com/",
    "hotmail": "https://outlook.live.com/", "github": "https://github.com/",
    "facebook": "https://www.facebook.com/", "instagram": "https://www.instagram.com/",
    "tiktok": "https://www.tiktok.com/", "linkedin": "https://www.linkedin.com/",
    "twitter": "https://x.com/", "netflix": "https://www.netflix.com/", "canva": "https://www.canva.com/",
    "notion": "https://www.notion.so/", "traductor": "https://translate.google.com/",
    "google traductor": "https://translate.google.com/", "maps": "https://maps.google.com/",
    "google maps": "https://maps.google.com/", "mapas": "https://maps.google.com/",
    "railway": "https://railway.com/dashboard", "turnitin": "https://www.turnitin.com/",
    "sunat": "https://www.sunat.gob.pe/", "youtube music": "https://music.youtube.com/",
    "google": "https://www.google.com/", "youtube": "https://www.youtube.com/",
}
# Si la app está instalada se abre la app; si no, la versión web.
APP_O_WEB = {
    "whatsapp": ("WhatsApp", "https://web.whatsapp.com/"),
    "spotify": ("Spotify", "https://open.spotify.com/"),
    "teams": ("Microsoft Teams", "https://teams.microsoft.com/"),
    "outlook": ("Outlook", "https://outlook.live.com/"),
    "discord": ("Discord", "https://discord.com/app"),
}


class Navegador:
    def __init__(self, cfg: dict, sitios_extra: dict | None = None):
        self.programa = cfg.get("programa", "chrome").lower()
        self.perfil_pred = cfg.get("perfil_predeterminado", "")
        self.sitios = dict(SITIOS)
        for k, v in (sitios_extra or {}).items():
            self.sitios[normalizar(k)] = v

    # ---------- programa y perfiles ----------
    def _datos(self) -> Path:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / ("Microsoft/Edge/User Data" if self.programa == "edge" else "Google/Chrome/User Data")

    def ejecutable(self) -> str | None:
        partes = (("Microsoft", "Edge", "Application", "msedge.exe") if self.programa == "edge"
                  else ("Google", "Chrome", "Application", "chrome.exe"))
        for raiz in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"),
                     os.environ.get("LOCALAPPDATA")):
            if raiz and Path(raiz, *partes).exists():
                return str(Path(raiz, *partes))
        return shutil.which("msedge" if self.programa == "edge" else "chrome")

    def perfiles(self) -> dict[str, list[str]]:
        """{carpeta_perfil: [nombres con los que lo puedes llamar]}."""
        try:
            estado = json.loads((self._datos() / "Local State").read_text(encoding="utf-8"))
            info = estado["profile"]["info_cache"]
        except Exception:
            return {}
        perfiles = {}
        for carpeta, d in info.items():
            nombres = [d.get("name", ""), d.get("gaia_name", ""), d.get("gaia_given_name", ""),
                       d.get("shortcut_name", ""), (d.get("user_name", "") or "").split("@")[0]]
            perfiles[carpeta] = [n for n in dict.fromkeys(nombres) if n]
        return perfiles

    def nombres_perfiles(self) -> list[str]:
        return [n[0] for n in self.perfiles().values() if n]

    def resolver_perfil(self, nombre: str | None) -> tuple[str, str] | None:
        """'trabajo' → ('Profile 2', 'Trabajo'). None si no existe."""
        nombre = nombre or self.perfil_pred
        if not nombre:
            return None
        q = numeros_a_digitos(normalizar(nombre))
        exactos, parciales = {}, {}
        for carpeta, nombres in self.perfiles().items():
            ficha = (carpeta, nombres[0] if nombres else carpeta)
            for alias in [carpeta, *nombres]:
                n = numeros_a_digitos(normalizar(alias))
                if n == q:
                    exactos[carpeta] = ficha
                elif q and f" {q} " in f" {n} ":
                    parciales[carpeta] = ficha
        candidatos = exactos or parciales
        return next(iter(candidatos.values())) if len(candidatos) == 1 else None

    def abrir(self, url: str | None = None, perfil: str | None = None, incognito: bool = False,
              *, nueva_ventana: bool = False) -> Apertura | None:
        exe = self.ejecutable()
        if not exe:
            if nueva_ventana or perfil or self.perfil_pred or incognito:
                raise FileNotFoundError(f"No encontré el ejecutable de {self.programa}.")
            if url:
                webbrowser.open(url)
            return
        args = [exe]
        if nueva_ventana:
            args.append("--new-window")
        p = self.resolver_perfil(perfil)
        if (perfil or self.perfil_pred) and not p:
            raise ValueError("No pude identificar el perfil del navegador.")
        if p:
            args.append(f"--profile-directory={p[0]}")
        if incognito:
            args.append("--inprivate" if self.programa == "edge" else "--incognito")
        apertura = Apertura.para(url, self.programa) if not nueva_ventana else None
        if url:
            args.append(apertura.url if apertura else url)
        if ES_WINDOWS:
            subprocess.Popen(args)
            return apertura
        else:
            log.info("[simulado] %s", " ".join(args))

    def nueva_ventana(self, perfil: str | None, incognito: bool) -> str:
        """Abre una ventana vacía y exige que Windows muestre una nueva de este navegador."""
        from . import ventanas
        procesos = ("msedge",) if self.programa == "edge" else ("chrome",)
        antes = ventanas.foto(procesos)
        try:
            self.abrir("about:blank", perfil, incognito, nueva_ventana=True)
        except (OSError, ValueError):
            return f"No pude iniciar {self.programa} para abrir otra ventana."
        if ventanas.esperar_y_enfocar(procesos, antes, espera=4, exigir_nueva=True):
            return "Abrí una nueva ventana del navegador."
        return "Solicité una ventana nueva. No pude confirmar que apareciera y quedara al frente."

    # ---------- búsquedas ----------
    @staticmethod
    def url_servicio(servicio: str, consulta: str | None) -> tuple[str, bool]:
        """(url, hay_que_pegar_la_pregunta)."""
        _, con_q, inicio = SERVICIOS[servicio]
        if not consulta:
            return inicio, False
        if con_q:
            return con_q.format(q=quote_plus(consulta)), False
        return inicio, True

    def preguntar(self, servicio: str, consulta: str, perfil=None, incognito=False) -> None:
        url, pegar = self.url_servicio(servicio, consulta)
        if consulta and servicio not in ("google", "youtube"):
            copiar(consulta)  # respaldo: si la página no la envía sola, basta Ctrl+V
        self.abrir(url, perfil, incognito)

    @staticmethod
    def primer_video(consulta: str) -> str:
        """URL del primer video de YouTube para esa búsqueda (o la página de resultados)."""
        resultados = SERVICIOS["youtube"][1].format(q=quote_plus(consulta))
        try:
            r = requests.get("https://www.youtube.com/results", params={"search_query": consulta},
                             headers={"User-Agent": UA, "Accept-Language": "es-PE,es;q=0.9"},
                             timeout=6)
            m = re.search(r'"videoId":"([\w-]{11})"', r.text)
            if m:
                return f"https://www.youtube.com/watch?v={m.group(1)}"
        except requests.RequestException as e:
            log.warning("YouTube no respondió: %s", e)
        return resultados

    def poner_video(self, consulta: str, perfil=None, incognito=False) -> None:
        self.abrir(self.primer_video(consulta), perfil, incognito)

    # ---------- sitios ----------
    def sitio(self, consulta: str) -> tuple[str, str] | None:
        """'clementina' → ('Clementina', url); 'facebook punto com' → url."""
        q = normalizar(consulta)
        q = re.sub(r"^(?:la |el |mi |mis )", "", q)
        if q in self.sitios:
            return consulta, self.sitios[q]
        dominio = re.fullmatch(r"(?:https?://)?([\w-]+(?:\.[\w-]+)*\.(?:com|pe|org|net|edu|io|ai|app|dev|gob|es|co)"
                               r"(?:\.[a-z]{2})?)/?", consulta.lower().strip())
        if dominio:
            return dominio.group(1), "https://" + dominio.group(1)
        hablado = re.fullmatch(r"([a-z0-9 ]+?) punto (com|pe|org|net|edu|io|ai|app|dev|es|co)"
                               r"(?: punto (pe|mx|ar|co|es))?", q)
        if hablado:
            dom = hablado.group(1).replace(" ", "") + "." + hablado.group(2)
            if hablado.group(3):
                dom += "." + hablado.group(3)
            return dom, "https://" + dom
        return None
