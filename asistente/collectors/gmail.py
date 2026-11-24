"""Gmail en solo lectura. Usa la cuenta conectada en acciones/google.py."""
from __future__ import annotations

import email.utils
import html

from ..acciones import google
from ..store import Item


def conectar() -> None:
    """Compatibilidad: conectar Gmail ahora conecta Gmail y Calendar juntos."""
    google.conectar()


def _remitente(valor: str) -> str:
    nombre, direccion = email.utils.parseaddr(valor)
    return nombre or direccion.split("@")[0]


def recolectar(cfg: dict) -> list[Item]:
    svc = google.servicio("gmail", "v1", google.GMAIL)
    res = svc.users().messages().list(userId="me", q=cfg["consulta"],
                                      maxResults=cfg["max_correos"]).execute()
    items: list[Item] = []
    for m in res.get("messages", []):
        msg = svc.users().messages().get(
            userId="me", id=m["id"], format="metadata",
            metadataHeaders=["From", "Subject"]).execute()
        h = {x["name"]: x["value"] for x in msg["payload"]["headers"]}
        importante = "IMPORTANT" in msg.get("labelIds", [])
        items.append(Item(
            fuente="gmail", id=m["id"], tipo="importante" if importante else "correo",
            titulo=(h.get("Subject") or "(sin asunto)")[:120],
            detalle=_remitente(h.get("From", "")),
            vence_ts=int(msg.get("internalDate", 0)) / 1000 or None,  # fecha de llegada
            url=f"https://mail.google.com/mail/u/0/#inbox/{m['id']}",
            nota=html.unescape(msg.get("snippet", ""))[:300],
        ))
    return items
