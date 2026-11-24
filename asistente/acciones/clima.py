"""Clima con Open-Meteo (gratis, sin clave)."""
from __future__ import annotations

import requests

WMO = {
    0: "despejado", 1: "mayormente despejado", 2: "parcialmente nublado", 3: "nublado",
    45: "con neblina", 48: "con neblina", 51: "con llovizna ligera", 53: "con llovizna",
    55: "con llovizna intensa", 56: "con llovizna helada", 57: "con llovizna helada",
    61: "con lluvia ligera", 63: "con lluvia", 65: "con lluvia fuerte", 66: "con lluvia helada",
    67: "con lluvia helada", 71: "con nieve ligera", 73: "con nieve", 75: "con nieve fuerte",
    80: "con chubascos", 81: "con chubascos", 82: "con chubascos fuertes",
    95: "con tormenta", 96: "con tormenta y granizo", 99: "con tormenta y granizo",
}


class Clima:
    def __init__(self, cfg: dict, zona: str = "America/Lima"):
        self.ciudad = cfg["ciudad"]
        self.lat, self.lon = cfg["latitud"], cfg["longitud"]
        self.zona = zona

    def _datos(self) -> dict:
        r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=8, params={
            "latitude": self.lat, "longitude": self.lon, "timezone": self.zona,
            "current": "temperature_2m,weather_code",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
            "forecast_days": 3,
        })
        r.raise_for_status()
        return r.json()

    def breve(self) -> str:
        d = self._datos()
        actual, diario = d["current"], d["daily"]
        lluvia = diario["precipitation_probability_max"][0] or 0
        texto = (f"En {self.ciudad} hay {round(actual['temperature_2m'])} grados, "
                 f"{WMO.get(actual['weather_code'], '')}; máxima de {round(diario['temperature_2m_max'][0])}")
        return texto + (f" y {lluvia} por ciento de probabilidad de lluvia." if lluvia >= 30 else ".")

    def consultar(self, dia: int = 0) -> str:
        d = self._datos()
        diario = d["daily"]
        maxi = round(diario["temperature_2m_max"][dia])
        mini = round(diario["temperature_2m_min"][dia])
        lluvia = diario["precipitation_probability_max"][dia] or 0
        estado = WMO.get(diario["weather_code"][dia], "")
        if dia == 0:
            actual = d["current"]
            texto = (f"Ahora en {self.ciudad} hay {round(actual['temperature_2m'])} grados, "
                     f"{WMO.get(actual['weather_code'], '')}. Hoy la máxima será {maxi} y la mínima {mini}")
        else:
            cuando = "Mañana" if dia == 1 else "Pasado mañana"
            texto = f"{cuando} en {self.ciudad} estará {estado}, con máxima de {maxi} y mínima de {mini}"
        if lluvia >= 20:
            texto += f", y {lluvia} por ciento de probabilidad de lluvia"
        return texto + "."
