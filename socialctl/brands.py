"""Load and create explicitly named brands."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import yaml
from pydantic import BaseModel

from socialctl.models import Platform
from socialctl.rutas import validar_componente_de_ruta

PLANTILLAS = Path(__file__).parent / "plantillas"


class BrandNoEncontrada(Exception):
    """The requested brand directory does not exist."""


class BrandYaExiste(Exception):
    """The requested brand directory already exists."""


class NombreDeMarcaInvalido(Exception):
    """The brand name is not a safe direct child of the workspace root."""


class AccountsInvalido(Exception):
    """accounts.yml cannot be interpreted as account configuration."""


def _validar_nombre_de_marca(raiz_social: Path, nombre: str) -> Path:
    """Validate a brand name and return its directory within the workspace root."""
    return validar_componente_de_ruta(
        raiz_social, nombre, NombreDeMarcaInvalido, "brand name"
    )


class Brand(BaseModel):
    nombre: str
    raiz: Path
    voz: str = ""
    cuentas: dict = {}

    @property
    def dir_posts(self) -> Path:
        return self.raiz / "posts"

    @property
    def dir_secretos(self) -> Path:
        return self.raiz / ".secrets"

    def leer_secreto(self, platform: Platform) -> dict:
        fichero = self.dir_secretos / f"{platform.value}.json"
        if not fichero.exists():
            return {}
        return json.loads(fichero.read_text(encoding="utf-8"))

    def guardar_secreto(self, platform: Platform, datos: dict) -> None:
        self.dir_secretos.mkdir(parents=True, exist_ok=True)
        self.dir_secretos.chmod(0o700)

        fichero = self.dir_secretos / f"{platform.value}.json"
        contenido = json.dumps(datos, indent=2).encode("utf-8")

        descriptor = os.open(fichero, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            # os.open solo aplica el modo 0o600 al CREAR el fichero: si ya
            # existia con permisos mas laxos, hay que forzarlos aqui, antes
            # de escribir el contenido, para que el secreto nunca toque disco
            # con permisos mas abiertos que 0o600 (ni siquiera un instante).
            os.fchmod(descriptor, 0o600)
            os.write(descriptor, contenido)
        finally:
            os.close(descriptor)

    def guardar_open_id_tiktok(self, open_id: str) -> str | None:
        """Write open_id to tiktok.open_id in accounts.yml without rewriting other configuration."""
        fichero = self.raiz / "accounts.yml"
        texto = fichero.read_text(encoding="utf-8") if fichero.exists() else ""
        lineas = texto.splitlines(keepends=True)

        idx_tiktok = next(
            (i for i, linea in enumerate(lineas) if re.match(r"^tiktok:\s*(#.*)?$", linea)),
            None,
        )
        if idx_tiktok is None:
            raise AccountsInvalido(
                f"the 'tiktok:' section was not found in {fichero}; could not save open_id automatically. Add the section manually (see socialctl/plantillas/accounts.yml) and try again."
            )

        idx_open_id = None
        for i in range(idx_tiktok + 1, len(lineas)):
            # Una línea que no empieza por espacio/tab (y no está en blanco)
            # marca el fin de la sección tiktok: dejar de buscar ahí.
            if lineas[i].strip() and not lineas[i][0].isspace():
                break
            coincidencia = re.match(r"^([ \t]*)open_id:.*$", lineas[i])
            if coincidencia:
                idx_open_id = i
                break

        if idx_open_id is None:
            raise AccountsInvalido(
                f"the 'open_id:' key was not found within 'tiktok:' in {fichero}; could not save it automatically. Add it manually (see socialctl/plantillas/accounts.yml)."
            )

        anterior = (self.cuentas.get("tiktok") or {}).get("open_id") or None

        indentacion = re.match(r"^([ \t]*)", lineas[idx_open_id]).group(1)
        valor_escapado = open_id.replace("\\", "\\\\").replace('"', '\\"')
        lineas[idx_open_id] = f'{indentacion}open_id: "{valor_escapado}"\n'

        fichero.write_text("".join(lineas), encoding="utf-8")

        # Mantener en memoria la marca ya cargada en sintonía con lo que se
        # acaba de escribir en disco, por si algo más adelante en el mismo
        # proceso vuelve a leer brand.cuentas sin recargar desde el fichero.
        self.cuentas.setdefault("tiktok", {})["open_id"] = open_id

        if anterior is not None and anterior != open_id:
            return anterior
        return None


def crear_brand(raiz_social: Path, nombre: str) -> Brand:
    """Create a new brand directory from templates."""
    raiz = _validar_nombre_de_marca(raiz_social, nombre)
    if raiz.exists():
        raise BrandYaExiste(
            f"a directory already exists for '{nombre}' in {raiz}"
        )

    for sub in ("media", "posts", ".secrets"):
        (raiz / sub).mkdir(parents=True)
    (raiz / ".secrets").chmod(0o700)

    shutil.copy(PLANTILLAS / "brand.md", raiz / "brand.md")
    shutil.copy(PLANTILLAS / "accounts.yml", raiz / "accounts.yml")
    shutil.copy(PLANTILLAS / "estrategia.md", raiz / "estrategia.md")
    (raiz / "historial.md").write_text(f"# History of {nombre}\n", encoding="utf-8")

    return cargar_brand(raiz_social, nombre)


def cargar_brand(raiz_social: Path, nombre: str) -> Brand:
    """Load an existing explicitly named brand."""
    raiz = _validar_nombre_de_marca(raiz_social, nombre)
    if not raiz.is_dir():
        disponibles = sorted(
            p.name for p in raiz_social.iterdir()
            if p.is_dir() and (p / "brand.md").exists()
        )
        raise BrandNoEncontrada(
            f"brand does not exist: '{nombre}' in {raiz_social}. "
            f"Marcas disponibles: {', '.join(disponibles) or 'none'}"
        )

    fichero_voz = raiz / "brand.md"
    fichero_cuentas = raiz / "accounts.yml"

    cuentas: dict = {}
    if fichero_cuentas.exists():
        try:
            contenido_cuentas = yaml.safe_load(
                fichero_cuentas.read_text(encoding="utf-8")
            )
        except yaml.YAMLError as exc:
            raise AccountsInvalido(
                f"'{fichero_cuentas}' is not valid YAML: {exc}"
            ) from exc

        if contenido_cuentas is None:
            cuentas = {}
        elif isinstance(contenido_cuentas, dict):
            cuentas = contenido_cuentas
        else:
            raise AccountsInvalido(
                f"'{fichero_cuentas}' must contain an account mapping (key: value); received {type(contenido_cuentas).__name__}"
            )

    return Brand(
        nombre=nombre,
        raiz=raiz,
        voz=fichero_voz.read_text(encoding="utf-8") if fichero_voz.exists() else "",
        cuentas=cuentas,
    )
