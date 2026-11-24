"""Preferencias explícitas de nombres de aplicaciones para pedidos de apertura."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from .acciones.sistema import ESPECIALES
from .respuesta import Respuesta
from .texto import Frase, normalizar


_PREFIJO = "correccion_apertura/"
_APRENDER = re.compile(
    r"(?:recuerda que |aprende que )?cuando (?:(?:yo )?(?:te )?(?:pida|diga)|me escuches decir) "
    r"(?:abrir|abre|abras|que abras) (?P<alias>.+?) "
    r"(?:me refiero a|me refiero al|quiero decir|quiero que abras) "
    r"(?:(?:el |la )?(?:programa|aplicacion|app) )?(?P<destino>.+)"
)
_ABRIR = re.compile(r"(?:abre|abreme|abrir|abras|ejecuta|lanza|inicia) "
                    r"(?:(?:el |la |mi )?(?:aplicacion|programa|app) )?(?P<alias>.+)")


@dataclass(frozen=True)
class Correccion:
    alias: str
    nombre: str
    identificador: str
    especial: bool = False

    def serializar(self):
        return json.dumps(asdict(self), ensure_ascii=False)


class Correcciones:
    def __init__(self, store):
        self.store = store

    def listar(self):
        with self.store.transaccion() as con:
            filas = con.execute("SELECT valor FROM meta WHERE clave GLOB ? ORDER BY clave",
                                (_PREFIJO + "*",)).fetchall()
        return [Correccion(**json.loads(fila[0])) for fila in filas]

    def borrar(self, correccion):
        """No borrar otra asociación agregada después de cargar el panel."""
        with self.store.transaccion() as con:
            return bool(con.execute("DELETE FROM meta WHERE clave=? AND valor=?",
                                    (_PREFIJO + normalizar(correccion.alias),
                                     correccion.serializar())).rowcount)

    def _aprender(self, alias, destino, apps):
        if not 1 <= len(alias) <= 90 or len(destino) > 160:
            return "Dime un nombre corto y la aplicación a la que te refieres."
        app = apps.buscar(destino)
        if not app:
            return f"No encontré «{destino}» en las aplicaciones de este equipo."
        nombre = app["Name"]
        if "especial" in app:
            correccion = Correccion(alias, nombre, app["especial"], especial=True)
        else:
            # Get-StartApps puede devolver entradas homónimas. Conservar el AppID
            # seleccionado evita que un cambio de nombre abra otro programa.
            candidatos = {a["AppID"] for a in apps.apps if normalizar(a["Name"]) == normalizar(nombre)}
            if candidatos != {app["AppID"]}:
                return f"No pude identificar una sola aplicación llamada «{nombre}». Dime su nombre completo."
            correccion = Correccion(alias, nombre, app["AppID"])
        self.store.set_meta(_PREFIJO + normalizar(alias), correccion.serializar())
        return f"Cuando me pidas abrir «{alias}», usaré {nombre}."

    def procesar(self, texto, apps, abrir_aplicacion):
        frase = Frase(texto).sin_cortesia()
        aprender = _APRENDER.fullmatch(frase.txt)
        if aprender:
            alias = frase.grupo(aprender, "alias")
            destino = frase.grupo(aprender, "destino")
            return Respuesta(ejecutar=lambda: self._aprender(alias, destino, apps))
        abrir = _ABRIR.fullmatch(frase.txt)
        if not abrir:
            return None
        alias = abrir["alias"]
        valor = self.store.get_meta(_PREFIJO + alias)
        if not valor and alias.startswith(("el ", "la ", "mi ")):
            valor = self.store.get_meta(_PREFIJO + alias.split(" ", 1)[1])
        if not isinstance(valor, str) or not valor:
            return None
        c = Correccion(**json.loads(valor))
        if c.especial:
            especial = ESPECIALES.get(normalizar(c.nombre))
            app = {"Name": c.nombre, "especial": especial} if especial == c.identificador else None
        else:
            app = next((a for a in apps.apps if a["AppID"] == c.identificador), None)
        if not app:
            return Respuesta(f"Tu corrección apunta a {c.nombre}, que ya no aparece entre las aplicaciones. "
                             "Puedes enseñarme otro nombre.")
        return abrir_aplicacion(app)
