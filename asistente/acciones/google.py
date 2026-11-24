"""Cuenta de Google con un solo inicio de sesión: Gmail (solo lectura) y Google Calendar.

conectar-google.bat pide ambos permisos y guarda el token en datos/google_token.json
(solo en tu laptop). Si aún tienes el token antiguo de Gmail, se sigue usando para
los correos hasta que conectes de nuevo.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..config import DATOS, RAIZ

log = logging.getLogger("google")

GMAIL = "https://www.googleapis.com/auth/gmail.readonly"
CALENDAR = "https://www.googleapis.com/auth/calendar.events"
SCOPES = [GMAIL, CALENDAR]
CREDENCIALES = RAIZ / "credentials.json"
TOKEN = DATOS / "google_token.json"
TOKEN_VIEJO = DATOS / "gmail_token.json"


class GoogleNoConectado(RuntimeError):
    pass


def _cargar():
    from google.oauth2.credentials import Credentials
    for ruta in (TOKEN, TOKEN_VIEJO):
        if ruta.exists():
            return Credentials.from_authorized_user_file(str(ruta)), ruta
    return None, TOKEN


def credenciales(necesita: str | None = None):
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    creds, ruta = _cargar()
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            ruta.write_text(creds.to_json(), encoding="utf-8")
        except RefreshError:
            creds = None  # revocado o vencido (las apps en "Prueba" vencen a los 7 días)
    if not creds or not creds.valid:
        raise GoogleNoConectado("Google no está conectado. Ejecuta conectar-google.bat")
    if necesita and necesita not in (creds.scopes or []):
        raise GoogleNoConectado("Falta el permiso de Google Calendar. Activa la API de Calendar y "
                                "ejecuta conectar-google.bat")
    return creds


def conectar() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not CREDENCIALES.exists():
        raise FileNotFoundError(f"Falta {CREDENCIALES.name}. Mira el README, paso Google.")
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENCIALES), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    TOKEN.write_text(creds.to_json(), encoding="utf-8")


def servicio(nombre: str, version: str, necesita: str | None = None):
    from googleapiclient.discovery import build
    return build(nombre, version, credentials=credenciales(necesita), cache_discovery=False)


class Calendario:
    def __init__(self, zona: str = "America/Lima"):
        self.zona = zona
        self.tz = ZoneInfo(zona)

    def conectado(self) -> bool:
        try:
            credenciales(CALENDAR)
            return True
        except Exception:
            return False

    def crear(self, titulo: str, inicio: datetime, fin: datetime | None = None,
              todo_el_dia: bool = False) -> str:
        if todo_el_dia:
            cuerpo = {"summary": titulo, "start": {"date": inicio.date().isoformat()},
                      "end": {"date": (inicio.date() + timedelta(days=1)).isoformat()}}
        else:
            fin = fin or inicio + timedelta(hours=1)
            cuerpo = {"summary": titulo,
                      "start": {"dateTime": inicio.isoformat(), "timeZone": self.zona},
                      "end": {"dateTime": fin.isoformat(), "timeZone": self.zona},
                      "reminders": {"useDefault": True}}
        ev = servicio("calendar", "v3", CALENDAR).events().insert(calendarId="primary", body=cuerpo).execute()
        return ev.get("htmlLink", "")

    def eventos(self, desde: datetime, hasta: datetime) -> list[dict]:
        res = servicio("calendar", "v3", CALENDAR).events().list(
            calendarId="primary", timeMin=desde.isoformat(), timeMax=hasta.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=20).execute()
        eventos = []
        for e in res.get("items", []):
            ini = e.get("start", {})
            if "dateTime" in ini:
                eventos.append({"titulo": e.get("summary", "(sin título)"), "todo_el_dia": False,
                                "inicio": datetime.fromisoformat(ini["dateTime"]).astimezone(self.tz)})
            elif "date" in ini:
                eventos.append({"titulo": e.get("summary", "(sin título)"), "todo_el_dia": True,
                                "inicio": datetime.fromisoformat(ini["date"]).replace(tzinfo=self.tz)})
        return eventos

    def eventos_dia(self, dia: int = 0) -> list[dict]:
        base = datetime.now(self.tz).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=dia)
        return self.eventos(base, base + timedelta(days=1))
