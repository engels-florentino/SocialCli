"""`<Marca>/metricas/piezas.yml`: los atributos editoriales de cada pieza.

Un snapshot dice **cuánto** rindió una pieza. Este fichero dice **qué era**:
de qué episodio salía, de qué pilar, con qué tipo de gancho abría, cuánto
duraba, a qué hora salió. Cruzar los dos es lo que convierte una lista de
números en un patrón.

El reparto de trabajo es estricto y es lo que hace este fichero fiable:

- **`stats` rellena lo técnico** (red, id, tipo, fecha, título, y el slug y
  la duración cuando los tiene) desde lo que devuelve la API y desde `posts/`.
- **El gestor de redes rellena lo editorial** (`CAMPOS_EDITORIALES`)
  preguntando al usuario lo que no pueda deducir.
- **`stats` nunca pisa un campo editorial ya escrito**, ni borra una entrada
  cuya pieza haya desaparecido de la red: un vídeo borrado sigue siendo
  historia editorial. Y cualquier clave que el usuario haya añadido a mano
  se conserva tal cual: el fichero es suyo tanto como nuestro.

Y como el fichero se edita a mano, puede llegar mal formado: una entrada que
no sea un diccionario, o dos entradas que compartan la misma identidad
`(red, id)`. Ninguno de los dos casos tumba `stats` -degradar es mejor que
abortar, el mismo criterio que ya siguen los lectores de cada red ante un
fallo parcial-, y de los dos se avisa con un `UserWarning` que dice qué
entrada y por qué (ver `fusionar`).

Y como es el **único fichero de `<Marca>/metricas/` que no se regenera** -los
snapshots vuelven a pedirse a la API; lo editorial de aquí, no-, las dos
puntas del ciclo de vida lo tratan en consecuencia: se escribe de forma
atómica (ver `guardar_piezas`) y, si no se puede leer, el comando falla en
vez de sobrescribirlo (ver `PiezasIlegibles`).
"""

from __future__ import annotations

import warnings
from pathlib import Path

import yaml

from socialctl.brands import Brand
from socialctl.metricas.almacen import dir_metricas, escribir_atomico
from socialctl.metricas.modelos import Snapshot


class PiezasIlegibles(Exception):
    """`piezas.yml` existe pero no se puede leer, así que no se puede reescribir.

    Es el único fichero de `<Marca>/metricas/` que **no se regenera**: los
    campos de `CAMPOS_EDITORIALES` los escribe el usuario o el gestor a mano
    y no hay API a la que volver a pedírselos. De ahí la decisión, que es
    distinta a la de `cargar_snapshot` (ver su docstring): ante un fichero
    ilegible esta capa **falla** en vez de degradar.

    Devolver `[]` sería mucho peor que fallar: la siguiente escritura
    fusionaría sobre una lista vacía y dejaría en disco solo lo técnico de
    hoy, **borrando** el fichero bueno que no se pudo leer -por ejemplo, uno
    al que el YAML se le rompió por una comilla y que se arregla en un
    minuto a mano-. Un fichero ilegible casi nunca es un fichero perdido; un
    fichero sobrescrito sí.

    Quien la captura es `socialctl/cli.py`, que la traduce a un mensaje en
    español diciendo qué se guardó y qué no.
    """

CAMPOS_EDITORIALES = ("episodio", "pilar", "gancho", "notas")
"""Los que rellena el gestor de redes. `stats` los crea vacíos y no los toca más.

`duracion_seg` **no** está aquí a propósito: YouTube da la duración real de
cada vídeo y Facebook la del vídeo adjunto, así que tratarlo como editorial
obligaría al gestor a preguntarle al usuario un dato que la API acaba de dar.
Se trata como técnico, pero con una salvedad (ver `fusionar`): solo se
refresca cuando la red lo trae; si la red no lo da -TikTok e Instagram hoy-,
lo que hubiera escrito se conserva.
"""


def ruta_piezas(brand: Brand) -> Path:
    return dir_metricas(brand) / "piezas.yml"


def cargar_piezas(brand: Brand) -> list[dict]:
    """Carga `piezas.yml`, o `[]` si no existe. Si existe y no se puede leer, falla.

    Solo comprueba la forma exterior (que el YAML sea una lista): es lo
    único que se puede exigir sin conocer la regla de identidad `(red, id)`,
    así que la forma de cada entrada individual la valida `fusionar`, que es
    quien de verdad la necesita (ver su docstring).

    Un fichero que existe pero no se puede leer -YAML mal formado, o un
    YAML válido que no es una lista- levanta `PiezasIlegibles` en vez de
    devolver `[]`: ver el porqué en esa excepción. Un fichero que **no
    existe** sigue siendo `[]`, que es el caso normal de la primera
    ejecución y no tiene nada que perder.
    """
    ruta = ruta_piezas(brand)
    if not ruta.is_file():
        return []
    texto = ruta.read_text(encoding="utf-8")
    try:
        datos = yaml.safe_load(texto)
    except yaml.YAMLError as exc:
        raise PiezasIlegibles(
            f"{ruta} no es un YAML válido ({exc.__class__.__name__}); no se "
            "toca para no borrar lo editorial que contenga. Arréglalo a mano "
            "-o muévelo aparte si prefieres empezar de cero- y vuelve a "
            "ejecutar stats."
        ) from exc
    if datos is None and not texto.strip():
        # Un fichero vacío del todo: no hay nada escrito que perder.
        return []
    if not isinstance(datos, list):
        raise PiezasIlegibles(
            f"{ruta} debería ser una lista de piezas y es "
            f"{type(datos).__name__}; no se toca para no borrar lo editorial "
            "que contenga. Arréglalo a mano y vuelve a ejecutar stats."
        )
    return datos


def fusionar(existentes: list[dict], snapshot: Snapshot) -> list[dict]:
    """Mezcla lo que dice la red con lo que ya había escrito, sin pisar nada editorial.

    Función pura: no toca disco, para poder probar la regla que importa sin
    montar ficheros. Por eso mismo -y no en `cargar_piezas`- es aquí donde se
    valida la forma de cada entrada de `existentes`: `cargar_piezas` solo
    puede garantizar que el YAML es una lista, porque no conoce la regla de
    identidad `(red, id)` que usa esta función para construir `por_clave`;
    validar en el sitio que de verdad necesita la forma evita que el
    resultado dependa de si `piezas.yml` existe en disco o se está
    fusionando a mano, como en el repro de este arreglo:
    `fusionar(["esto no es un dict"], snapshot)`.

    Dos formas de entrada mal formada, y las dos degradan en vez de abortar
    -mismo criterio que ya siguen los lectores de cada red ante un fallo
    parcial (ver `socialctl/metricas/youtube.py`, `facebook.py`)-, avisando
    con un `UserWarning` que dice qué entrada y por qué, para que el usuario
    pueda ir al fichero y arreglarla:

    - **La entrada no es un diccionario** (un renglón suelto tras una
      edición apresurada): se descarta. Su posición en la lista es la única
      referencia posible, porque no tiene claves que citar.
    - **Dos entradas comparten la misma identidad `(red, id)`** -el caso
      límite es que ninguna de las dos tenga `red` ni `id`, y las dos caigan
      en `(None, None)`-: sin una clave más que las distinga no hay manera
      de fusionarlas de verdad, así que la segunda gana, igual que antes de
      este arreglo; la diferencia es que ahora se avisa de cuál se descarta
      y con qué título, para que el usuario pueda separarlas a mano en el
      fichero (añadiéndoles `red` e `id`, por ejemplo).
    """
    por_clave: dict[tuple, dict] = {}
    for posicion, entrada_cruda in enumerate(existentes):
        if not isinstance(entrada_cruda, dict):
            warnings.warn(
                f"piezas.yml: la entrada nº {posicion + 1} no es un "
                f"diccionario (es {type(entrada_cruda).__name__}: "
                f"{entrada_cruda!r}); se ignora. Repárala a mano en el "
                "fichero y vuelve a ejecutar stats.",
                stacklevel=2,
            )
            continue

        clave = (entrada_cruda.get("red"), entrada_cruda.get("id"))
        anterior = por_clave.get(clave)
        if anterior is not None:
            identidad = (
                "sin 'red' ni 'id'"
                if clave == (None, None)
                else f"red={clave[0]!r}, id={clave[1]!r}"
            )
            warnings.warn(
                f"piezas.yml: dos entradas comparten la misma identidad "
                f"({identidad}): la de título {anterior.get('titulo')!r} se "
                f"descarta en favor de la de título "
                f"{entrada_cruda.get('titulo')!r}. Dales 'red' e 'id' "
                "propios en el fichero para distinguirlas.",
                stacklevel=2,
            )
        por_clave[clave] = dict(entrada_cruda)

    for platform, lectura in snapshot.redes.items():
        for pieza in lectura.piezas:
            clave = (platform.value, pieza.id)
            entrada = por_clave.get(clave, {campo: None for campo in CAMPOS_EDITORIALES})
            entrada.update({
                "red": platform.value,
                "id": pieza.id,
                "tipo": pieza.tipo.value,
                "publicado_el": pieza.publicado_el.isoformat(),
                "titulo": pieza.titulo,
            })
            if pieza.slug is not None:
                # El cruce con `posts/` lo resolvió: manda el cruce.
                entrada["slug"] = pieza.slug
            else:
                # No se resolvió: una pieza publicada fuera de `socialctl`, o
                # un TikTok cuyo `resultado.json` no guarda la URL, que es el
                # caso habitual. Se conserva lo que hubiera, porque puede
                # haberlo escrito el gestor a mano. Misma regla asimétrica
                # que `duracion_seg`, y por el mismo motivo: lo que esta
                # ejecución no trae, no se toca.
                entrada.setdefault("slug", None)
            if pieza.duracion_seg is not None:
                # La red la da: manda la red.
                entrada["duracion_seg"] = pieza.duracion_seg
            else:
                # La red no la da (TikTok, Instagram): se conserva lo que
                # hubiera, que puede haberlo escrito el gestor a mano.
                entrada.setdefault("duracion_seg", None)
            for campo in CAMPOS_EDITORIALES:
                entrada.setdefault(campo, None)
            por_clave[clave] = entrada

    return sorted(
        por_clave.values(),
        key=lambda e: (e.get("publicado_el") or "", e.get("red") or ""),
    )


def guardar_piezas(brand: Brand, piezas: list[dict]) -> Path:
    """Reescribe `piezas.yml` entero con las entradas dadas.

    **Los comentarios que el usuario escriba en el fichero se pierden.**
    `yaml.safe_dump` vuelca la estructura de datos, no el texto original, así
    que cualquier `# ...` desaparece en la siguiente ejecución de `stats`. Las
    claves sí se conservan todas, incluidas las que el usuario haya añadido a
    mano (ver `fusionar`), pero las anotaciones van en el campo `notas`, que
    es lo que sobrevive.

    La escritura es **atómica** (`escribir_atomico`, en
    `socialctl/metricas/almacen.py`): temporal en la misma carpeta y
    `os.replace`. Aquí importa más que en ningún otro sitio del proyecto,
    porque este es el único fichero que no se puede volver a pedir a la API:
    con un `write_text` directo, una interrupción a mitad de la escritura
    dejaba el fichero truncado y lo editorial perdido para siempre.
    """
    return escribir_atomico(
        ruta_piezas(brand),
        yaml.safe_dump(piezas, allow_unicode=True, sort_keys=False),
    )


def actualizar_piezas(brand: Brand, snapshot: Snapshot) -> Path:
    return guardar_piezas(brand, fusionar(cargar_piezas(brand), snapshot))
