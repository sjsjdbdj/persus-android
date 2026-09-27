"""
google_contacts.py
==================
Wrapper de Google Contacts / People API para Mark-XLVIII.
"""
from __future__ import annotations

from typing import Any

from .google_auth import build_service_for


def _get_people_service():
    return build_service_for("contacts", account="primary")


# ─────────────────────────────────────────────────────────────────────────────
# Búsqueda y listado
# ─────────────────────────────────────────────────────────────────────────────

def search_contacts(query: str, *, page_size: int = 20) -> list[dict[str, Any]]:
    svc = _get_people_service()
    res = svc.people().searchContacts(
        query=query,
        readMask="names,emailAddresses,phoneNumbers,organizations,photos",
        pageSize=page_size,
    ).execute()
    out: list[dict[str, Any]] = []
    for r in res.get("results", []):
        person = r.get("person", {})
        out.append(_normalize(person))
    return out


def list_other_contacts(*, page_size: int = 50) -> list[dict[str, Any]]:
    """Contactos con los que has intercambiado email (no están en 'Mis Contactos')."""
    svc = _get_people_service()
    res = svc.otherContacts().list(
        readMask="names,emailAddresses,phoneNumbers",
        pageSize=page_size,
    ).execute()
    return [_normalize(p) for p in res.get("otherContacts", [])]


def get_contact(resource_name: str) -> dict[str, Any]:
    """`resource_name` tiene el formato 'people/c123…'."""
    svc = _get_people_service()
    res = svc.people().get(
        resourceName=resource_name,
        personFields="names,emailAddresses,phoneNumbers,organizations,addresses,biographies",
    ).execute()
    return _normalize(res)


# ─────────────────────────────────────────────────────────────────────────────
# Mutaciones
# ─────────────────────────────────────────────────────────────────────────────

def create_contact(
    *,
    given_name: str,
    family_name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    organization: str | None = None,
) -> dict[str, Any]:
    """Crea un contacto en 'Mis Contactos' (funciona en cuentas personales
    Gmail y en cuentas Workspace). `createDirectory` solo sirve para
    cuentas de dominio y devuelve PERMISSION_DENIED en cuentas personales."""
    svc = _get_people_service()
    body: dict[str, Any] = {"names": [{"givenName": given_name}]}
    if family_name:
        body["names"][0]["familyName"] = family_name
    if email:
        body["emailAddresses"] = [{"value": email}]
    if phone:
        body["phoneNumbers"] = [{"value": phone}]
    if organization:
        body["organizations"] = [{"name": organization}]
    return svc.people().createContact(body=body).execute()


def update_contact(
    resource_name: str,
    *,
    etag: str,
    given_name: str | None = None,
    family_name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
) -> dict[str, Any]:
    svc = _get_people_service()
    body: dict[str, Any] = {"etag": etag}
    names = {}
    if given_name:  names["givenName"]  = given_name
    if family_name: names["familyName"] = family_name
    if names: body["names"] = [names]
    if email:
        body["emailAddresses"] = [{"value": email}]
    if phone:
        body["phoneNumbers"] = [{"value": phone}]
    update_fields = ",".join(
        k for k in ("names", "emailAddresses", "phoneNumbers") if k in body
    )
    return svc.people().updateContact(
        resourceName=resource_name,
        updatePersonFields=update_fields,
        body=body,
    ).execute()


def delete_contact(resource_name: str) -> None:
    svc = _get_people_service()
    svc.people().deleteContact(resourceName=resource_name).execute()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _normalize(person: dict[str, Any]) -> dict[str, Any]:
    names = person.get("names", [])
    primary_name = (
        names[0].get("displayName") if names else "(sin nombre)"
    )
    given = (names[0].get("givenName", "") if names else "")
    family = (names[0].get("familyName", "") if names else "")

    emails = [e.get("value") for e in person.get("emailAddresses", []) if e.get("value")]
    phones = [p.get("value") for p in person.get("phoneNumbers", []) if p.get("value")]
    orgs   = [o.get("name")  for o in person.get("organizations", [])   if o.get("name")]

    return {
        "resource_name": person.get("resourceName"),
        "etag":          person.get("etag"),
        "display_name":  primary_name,
        "given_name":    given,
        "family_name":   family,
        "emails":        emails,
        "phones":        phones,
        "organizations": orgs,
    }