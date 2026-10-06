"""Carga y creacion de marcas.

Una marca es una carpeta de datos. El codigo no contiene nada especifico
de ninguna marca; toda la personalidad vive en brand.md.
"""

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
    """No existe una carpeta para esa marca."""


class BrandYaExiste(Exception):
    """Ya existe una carpeta para esa marca."""


class NombreDeMarcaInvalido(Exception):
    """El nombre de marca no es una única carpeta directa dentro de la raíz social.

    Se lanza cuando el nombre está vacío o solo tiene espacios, es una ruta
    absoluta, contiene separadores de ruta ('/' u ``os.sep``), es '.' o '..',
    o cuando -tras resolver enlaces simbólicos- la carpeta resultante queda
    fuera de la raíz social. El aislamiento entre marcas (y por tanto el de
    sus credenciales, que viven bajo esa misma carpeta) depende de que el
    nombre de marca nunca pueda escapar de su raíz.
    """


class AccountsInvalido(Exception):
    """El fichero accounts.yml existe pero no se puede interpretar como cuentas.

    Se lanza cuando el YAML está mal formado, o cuando es válido pero no
    representa un mapping (por ejemplo, un escalar o una lista sueltos).
    """


def _validar_nombre_de_marca(raiz_social: Path, nombre: str) -> Path:
    """Valida ``nombre`` y devuelve la carpeta de esa marca en ``raiz_social``.

    La usan tanto ``cargar_brand`` como ``crear_brand`` para que el
    aislamiento entre marcas no dependa de que quien llama pase nombres bien
    formados. El criterio en sí (rechazo por forma, y después por destino
    tras resolver enlaces simbólicos) vive en
    ``socialctl.rutas.validar_componente_de_ruta``, compartido con el
    mismo tipo de comprobación sobre el slug de un post y el nombre de un
    fichero de media (ver ``socialctl/rutas.py``).
    """
    return validar_componente_de_ruta(
        raiz_social, nombre, NombreDeMarcaInvalido, "nombre de marca"
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
        """Escribe ``open_id`` en la clave ``tiktok.open_id`` de ``accounts.yml``.

        A diferencia de ``guardar_secreto`` (que sí puede volcar el diccionario
        entero, porque ``.secrets/*.json`` no lleva comentarios para nadie),
        aquí hace falta una sustitución dirigida sobre el TEXTO del fichero:
        ``accounts.yml`` lleva comentarios explicativos que el usuario lee, y
        un volcado completo con ``yaml.safe_dump`` los borraría todos. Este
        método localiza la sección ``tiktok:`` de nivel superior y, dentro de
        ella, la línea ``open_id: ...``, y sustituye solo esa línea,
        conservando su indentación y todo lo demás del fichero (comentarios
        incluidos) byte a byte.

        Devuelve el valor de ``open_id`` que hubiera guardado ANTES si era
        distinto del nuevo -para que quien llama pueda avisar de que cambia-,
        o ``None`` si no había ninguno guardado o coincidía con el nuevo.

        Lanza ``AccountsInvalido`` si no encuentra la sección ``tiktok:`` o
        la clave ``open_id:`` dentro de ella: en ese caso no se escribe nada
        (mejor fallar con un mensaje claro que adivinar dónde insertar la
        línea en un fichero con una forma inesperada).
        """
        fichero = self.raiz / "accounts.yml"
        texto = fichero.read_text(encoding="utf-8") if fichero.exists() else ""
        lineas = texto.splitlines(keepends=True)

        idx_tiktok = next(
            (i for i, linea in enumerate(lineas) if re.match(r"^tiktok:\s*(#.*)?$", linea)),
            None,
        )
        if idx_tiktok is None:
            raise AccountsInvalido(
                f"no se encontró la sección 'tiktok:' en {fichero}; no se pudo "
                "guardar el open_id automáticamente. Añade la sección a mano "
                "(ver socialctl/plantillas/accounts.yml) y vuelve a intentarlo."
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
                f"no se encontró la clave 'open_id:' dentro de 'tiktok:' en "
                f"{fichero}; no se pudo guardar automáticamente. Añádela a "
                "mano (ver socialctl/plantillas/accounts.yml)."
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
    """Crea la carpeta de una marca nueva a partir de las plantillas."""
    raiz = _validar_nombre_de_marca(raiz_social, nombre)
    if raiz.exists():
        raise BrandYaExiste(
            f"ya existe una carpeta para '{nombre}' en {raiz}"
        )

    for sub in ("media", "posts", ".secrets"):
        (raiz / sub).mkdir(parents=True)
    (raiz / ".secrets").chmod(0o700)

    shutil.copy(PLANTILLAS / "brand.md", raiz / "brand.md")
    shutil.copy(PLANTILLAS / "accounts.yml", raiz / "accounts.yml")
    shutil.copy(PLANTILLAS / "estrategia.md", raiz / "estrategia.md")
    (raiz / "historial.md").write_text(f"# Historial de {nombre}\n", encoding="utf-8")

    return cargar_brand(raiz_social, nombre)


def cargar_brand(raiz_social: Path, nombre: str) -> Brand:
    """Carga una marca existente."""
    raiz = _validar_nombre_de_marca(raiz_social, nombre)
    if not raiz.is_dir():
        disponibles = sorted(
            p.name for p in raiz_social.iterdir()
            if p.is_dir() and (p / "brand.md").exists()
        )
        raise BrandNoEncontrada(
            f"no existe la marca '{nombre}' en {raiz_social}. "
            f"Marcas disponibles: {', '.join(disponibles) or 'ninguna'}"
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
                f"'{fichero_cuentas}' no es un YAML válido: {exc}"
            ) from exc

        if contenido_cuentas is None:
            cuentas = {}
        elif isinstance(contenido_cuentas, dict):
            cuentas = contenido_cuentas
        else:
            raise AccountsInvalido(
                f"'{fichero_cuentas}' debe contener un mapping (clave: valor) "
                f"de cuentas; contiene un {type(contenido_cuentas).__name__}"
            )

    return Brand(
        nombre=nombre,
        raiz=raiz,
        voz=fichero_voz.read_text(encoding="utf-8") if fichero_voz.exists() else "",
        cuentas=cuentas,
    )
