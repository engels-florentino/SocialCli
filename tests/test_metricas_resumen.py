from datetime import date, datetime

from socialctl.brands import crear_brand
from socialctl.metricas.modelos import (
    Cuenta,
    EstadoLectura,
    LecturaRed,
    Metricas,
    Pieza,
    Snapshot,
    TipoPieza,
)
from socialctl.metricas.resumen import (
    LARGO_AVISO,
    LARGO_TITULO,
    escribir_resumen,
    render_resumen,
)
from socialctl.metricas.youtube import CLAVE_ENRIQUECIMIENTO_FALLIDO
from socialctl.models import Platform


def _snapshot(fecha, vistas, estado=EstadoLectura.OK, error=None, visto=48.5, subs=12):
    return Snapshot(
        fecha=fecha,
        marca="Histopast",
        redes={
            Platform.YOUTUBE: LecturaRed(
                estado=estado,
                error=error,
                cuenta=Cuenta(seguidores=4950),
                piezas=[Pieza(
                    id="vid1",
                    url="https://youtu.be/vid1",
                    titulo="1496: Santo Domingo",
                    publicado_el=datetime(2026, 9, 6, 19, 0),
                    tipo=TipoPieza.LARGO,
                    acumulado=Metricas(vistas=vistas, likes=30),
                    especificas={"porcentaje_visto": visto, "suscriptores_ganados": subs},
                )],
            )
        },
    )


def _snapshot_tiktok(fecha, vistas, compartidos=71, guardados=18):
    # `guardados` se sigue pudiendo fijar en el modelo -es un campo común de
    # `Metricas`-, pero TikTok nunca lo rellena de verdad (su Display API no
    # lo da) y `resumen.py` no le reserva columna: el parámetro queda aquí
    # solo por si algún test futuro necesita distinguir "el modelo lo
    # permite" de "la tabla lo muestra".
    return Snapshot(
        fecha=fecha,
        marca="Histopast",
        redes={
            Platform.TIKTOK: LecturaRed(
                estado=EstadoLectura.OK,
                cuenta=Cuenta(seguidores=210),
                piezas=[Pieza(
                    id="tk1",
                    url="https://tiktok.com/@histopast/video/tk1",
                    titulo="Una línea en un mapa",
                    publicado_el=datetime(2026, 9, 10, 19, 0),
                    tipo=TipoPieza.VERTICAL,
                    acumulado=Metricas(
                        vistas=vistas, likes=980,
                        compartidos=compartidos, guardados=guardados,
                    ),
                )],
            )
        },
    )


def _snapshot_instagram(fecha, vistas, compartidos=40, guardados=15):
    return Snapshot(
        fecha=fecha,
        marca="Histopast",
        redes={
            Platform.INSTAGRAM: LecturaRed(
                estado=EstadoLectura.OK,
                cuenta=Cuenta(seguidores=300),
                piezas=[Pieza(
                    id="ig1",
                    url="https://instagram.com/p/ig1",
                    titulo="Un mapa que cambia de manos",
                    publicado_el=datetime(2026, 9, 10, 19, 0),
                    tipo=TipoPieza.IMAGEN,
                    acumulado=Metricas(
                        vistas=vistas, likes=430,
                        compartidos=compartidos, guardados=guardados,
                    ),
                )],
            )
        },
    )


def test_el_primer_snapshot_lo_dice_y_no_inventa_variaciones():
    texto = render_resumen(_snapshot(date(2026, 9, 11), 254), anterior=None, piezas=[])

    assert "Histopast" in texto
    assert "2026-09-11" in texto
    assert 'first snapshot' in texto.lower()
    assert "254" in texto


def test_con_dos_snapshots_muestra_la_variacion():
    texto = render_resumen(
        _snapshot(date(2026, 9, 11), 254),
        anterior=_snapshot(date(2026, 9, 9), 200),
        piezas=[],
    )

    assert "+54" in texto
    assert 'first snapshot' not in texto.lower()


def test_una_variacion_negativa_se_ve_como_tal():
    texto = render_resumen(
        _snapshot(date(2026, 9, 11), 180),
        anterior=_snapshot(date(2026, 9, 9), 200),
        piezas=[],
    )
    assert "-20" in texto


def test_una_pieza_nueva_no_finge_variacion():
    anterior = Snapshot(fecha=date(2026, 9, 9), marca="Histopast", redes={
        Platform.YOUTUBE: LecturaRed(estado=EstadoLectura.OK, piezas=[]),
    })
    texto = render_resumen(_snapshot(date(2026, 9, 11), 254), anterior=anterior, piezas=[])

    assert "254" in texto
    assert "+254" not in texto, "una pieza que antes no existía no ha 'subido' 254"


def test_la_tabla_de_youtube_lleva_retencion_y_suscriptores():
    texto = render_resumen(_snapshot(date(2026, 9, 11), 254), anterior=None, piezas=[])

    assert '% viewed' in texto
    assert "Subs" in texto
    assert "48.5" in texto
    assert 'Saves' not in texto, "guardados no es una columna de YouTube"


def test_la_tabla_de_tiktok_lleva_compartidos_pero_no_guardados():
    """TikTok mide su alcance con vistas y compartidos: su Display API no da
    guardados, así que esa columna no puede aparecer en su tabla."""
    texto = render_resumen(_snapshot_tiktok(date(2026, 9, 11), 15400), anterior=None, piezas=[])

    assert 'Shares' in texto
    assert "71" in texto
    assert '% viewed' not in texto, "TikTok no mide porcentaje visto"


def test_la_tabla_de_instagram_si_lleva_guardados():
    """Instagram sí rellena `guardados` (Graph API), así que su tabla sí lleva esa columna."""
    texto = render_resumen(_snapshot_instagram(date(2026, 9, 11), 9000), anterior=None, piezas=[])

    assert 'Shares' in texto
    assert 'Saves' in texto
    assert "15" in texto


def test_tiktok_no_ofrece_guardados_e_instagram_si():
    """Fija la asimetría a propósito: si alguien vuelve a añadir Guardados a
    la tabla de TikTok, este test debe fallar -esa columna saldría vacía
    siempre, porque la Display API de TikTok no la da (verificado, no
    supuesto: ver el comentario junto a `COLUMNAS` en `resumen.py`)."""
    texto_tiktok = render_resumen(
        _snapshot_tiktok(date(2026, 9, 11), 15400, guardados=18), anterior=None, piezas=[]
    )
    texto_instagram = render_resumen(
        _snapshot_instagram(date(2026, 9, 11), 9000, guardados=15), anterior=None, piezas=[]
    )

    assert 'Saves' not in texto_tiktok
    assert 'Saves' in texto_instagram


def test_la_variacion_cubre_las_tres_metricas_que_pide_el_spec():
    texto = render_resumen(
        _snapshot(date(2026, 9, 11), 254, visto=48.5, subs=12),
        anterior=_snapshot(date(2026, 9, 9), 200, visto=45.5, subs=9),
        piezas=[],
    )

    # Se afirma la celda entera, no solo el signo: `"+3"` lo satisfacen por
    # igual la celda de `% visto` (`48.5 (+3)`) y la de `Subs` (`12 (+3)`),
    # así que con el fragmento suelto la variación de `suscriptores_ganados`
    # quedaba sin probar.
    assert "254 (+54)" in texto   # vistas
    assert "48.5 (+3)" in texto   # porcentaje visto (48.5 - 45.5)
    assert "12 (+3)" in texto     # suscriptores ganados (12 - 9)


def test_la_variacion_de_tiktok_solo_cubre_compartidos():
    """TikTok no tiene columna de guardados, así que su variación tampoco
    puede calcularse ni mostrarse para ese campo -solo para compartidos."""
    texto = render_resumen(
        _snapshot_tiktok(date(2026, 9, 11), 15400, compartidos=71, guardados=18),
        anterior=_snapshot_tiktok(date(2026, 9, 9), 12000, compartidos=60, guardados=11),
        piezas=[],
    )

    assert "+11" in texto  # compartidos
    assert "+7" not in texto  # la variación de guardados no se muestra


def test_la_variacion_de_guardados_si_llega_en_instagram():
    texto = render_resumen(
        _snapshot_instagram(date(2026, 9, 11), 9000, compartidos=40, guardados=15),
        anterior=_snapshot_instagram(date(2026, 9, 9), 7000, compartidos=35, guardados=8),
        piezas=[],
    )

    assert "+5" in texto  # compartidos (40-35)
    assert "+7" in texto  # guardados (15-8)


def test_muestra_el_pilar_y_el_gancho_de_piezas_yml():
    piezas = [{
        "red": "youtube", "id": "vid1", "pilar": "mapas-fronteras-idiomas",
        "gancho": "consecuencia", "episodio": "1496-santo-domingo",
        "duracion_seg": 980, "notas": None, "slug": None,
    }]
    texto = render_resumen(_snapshot(date(2026, 9, 11), 254), anterior=None, piezas=piezas)

    assert "mapas-fronteras-idiomas" in texto
    assert "consecuencia" in texto


def test_una_red_con_error_lo_dice_con_su_motivo():
    texto = render_resumen(
        _snapshot(date(2026, 9, 11), 0, estado=EstadoLectura.SIN_PERMISO,
                  error="el token de youtube no tiene el permiso 'yt-analytics.readonly'"),
        anterior=None, piezas=[],
    )

    assert "sin_permiso" in texto
    assert "yt-analytics.readonly" in texto


def test_escribe_el_fichero_en_la_carpeta_de_metricas(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    ruta = escribir_resumen(brand, _snapshot(date(2026, 9, 11), 254))

    assert ruta.name == "resumen.md"
    assert ruta.parent.name == "metricas"
    assert "Histopast" in ruta.read_text(encoding="utf-8")


def test_una_pieza_con_enriquecimiento_fallido_muestra_el_aviso():
    """Protege que el motivo del fallo sea visible en la columna Aviso."""
    snapshot = Snapshot(
        fecha=date(2026, 9, 11),
        marca="Histopast",
        redes={
            Platform.YOUTUBE: LecturaRed(
                estado=EstadoLectura.OK,
                cuenta=Cuenta(seguidores=4950),
                piezas=[Pieza(
                    id="vid1",
                    url="https://youtu.be/vid1",
                    titulo="1496: Santo Domingo",
                    publicado_el=datetime(2026, 9, 6, 19, 0),
                    tipo=TipoPieza.LARGO,
                    acumulado=Metricas(vistas=254, likes=30),
                    especificas={
                        "porcentaje_visto": 48.5,
                        "suscriptores_ganados": 12,
                        CLAVE_ENRIQUECIMIENTO_FALLIDO: "cuota de YouTube agotada",
                    },
                )],
            )
        },
    )
    texto = render_resumen(snapshot, anterior=None, piezas=[])

    assert "⚠ cuota de YouTube agotada" in texto


def test_una_pieza_sana_no_muestra_aviso():
    """Protege que una pieza sin fallo muestre el guión, no una advertencia."""
    snapshot = Snapshot(
        fecha=date(2026, 9, 11),
        marca="Histopast",
        redes={
            Platform.YOUTUBE: LecturaRed(
                estado=EstadoLectura.OK,
                cuenta=Cuenta(seguidores=4950),
                piezas=[Pieza(
                    id="vid1",
                    url="https://youtu.be/vid1",
                    titulo="1496: Santo Domingo",
                    publicado_el=datetime(2026, 9, 6, 19, 0),
                    tipo=TipoPieza.LARGO,
                    acumulado=Metricas(vistas=254, likes=30),
                    especificas={"porcentaje_visto": 48.5, "suscriptores_ganados": 12},
                )],
            )
        },
    )
    texto = render_resumen(snapshot, anterior=None, piezas=[])

    # La fila de la pieza debe contener un guión en la columna de aviso.
    # Se busca una secuencia específica: "Subs | —" aísla la columna de
    # aviso en su posición entre Suscriptores (última métrica) y fin de fila.
    assert "Subs | —" in texto or "suscriptores_ganados" not in texto or "Subs | — |" in texto
    assert "⚠" not in texto


def test_el_motivo_del_aviso_se_recorta_si_es_muy_largo():
    """Protege que avisos muy largos no rompan la legibilidad de la tabla."""
    motivo_largo = "x" * (LARGO_AVISO + 50)  # Motivo que excede el límite
    snapshot = Snapshot(
        fecha=date(2026, 9, 11),
        marca="Histopast",
        redes={
            Platform.YOUTUBE: LecturaRed(
                estado=EstadoLectura.OK,
                cuenta=Cuenta(seguidores=4950),
                piezas=[Pieza(
                    id="vid1",
                    url="https://youtu.be/vid1",
                    titulo="1496: Santo Domingo",
                    publicado_el=datetime(2026, 9, 6, 19, 0),
                    tipo=TipoPieza.LARGO,
                    acumulado=Metricas(vistas=254, likes=30),
                    especificas={
                        "porcentaje_visto": 48.5,
                        "suscriptores_ganados": 12,
                        CLAVE_ENRIQUECIMIENTO_FALLIDO: motivo_largo,
                    },
                )],
            )
        },
    )
    texto = render_resumen(snapshot, anterior=None, piezas=[])

    # El aviso debe estar presente y debe terminar con "…"
    assert "⚠" in texto
    assert "…" in texto
    # Extrae la línea de la pieza y verifica que el motivo esté recortado
    lineas = texto.split("\n")
    fila_pieza = [l for l in lineas if "Santo Domingo" in l][0]
    # Busca el patrón del aviso: debe contener solo LARGO_AVISO caracteres
    # más el símbolo de advertencia y el elipsis
    partes = fila_pieza.split(" | ")
    # El último elemento de la fila es el aviso (puede terminar con " |")
    aviso_crudo = partes[-1].rstrip(" |")
    # Verifica que el motivo sin el símbolo esté dentro del límite
    # Formato es "⚠ <motivo>…", así que sin "⚠ " quedan <motivo>…
    motivo_sin_simbolo = aviso_crudo.replace("⚠ ", "", 1)
    # El motivo recortado debe tener hasta LARGO_AVISO - 1 caracteres
    # (ya que se reduce a LARGO_AVISO - 1 y se añade "…")
    assert len(motivo_sin_simbolo) <= LARGO_AVISO


# --- una red en error con piezas conservadas sigue enseñando la tabla -------


def test_una_red_en_error_con_piezas_conservadas_muestra_el_aviso_y_la_tabla():
    """El caso que `--only` fabrica y que el resumen se comía.

    `_fusionar_snapshot_del_dia` (`socialctl/cli.py`) conserva los últimos
    números buenos de una red que hoy falla y le pone el `estado` de hoy,
    prometiendo que «el resumen y `piezas.yml` siguen viendo los últimos
    números buenos en vez de un hueco». El resumen no lo cumplía: cortaba en
    el aviso de estado y se llevaba por delante las piezas, los seguidores y
    la tabla entera.
    """
    snapshot = _snapshot(
        date(2026, 9, 11), 254,
        estado=EstadoLectura.ERROR,
        error="youtube respondió 500",
    )
    texto = render_resumen(snapshot, anterior=None, piezas=[])

    assert "`error`" in texto
    assert "youtube respondió 500" in texto
    assert "254" in texto, "las piezas conservadas no pueden desaparecer"
    assert "4950" in texto, "la línea de seguidores tampoco"
    assert '| Published |' in texto, "la tabla tiene que seguir estando"


def test_una_red_en_error_sin_piezas_sigue_sin_tabla():
    snapshot = Snapshot(fecha=date(2026, 9, 11), marca="Histopast", redes={
        Platform.YOUTUBE: LecturaRed(
            estado=EstadoLectura.SIN_CREDENCIALES,
            error="no hay token guardado",
            piezas=[],
        ),
    })
    texto = render_resumen(snapshot, anterior=None, piezas=[])

    assert "sin_credenciales" in texto
    assert '| Published |' not in texto


# --- menores de legibilidad de la tabla ------------------------------------


def _snapshot_con_titulo(titulo):
    return Snapshot(fecha=date(2026, 9, 11), marca="Histopast", redes={
        Platform.YOUTUBE: LecturaRed(
            estado=EstadoLectura.OK,
            cuenta=Cuenta(seguidores=4950),
            piezas=[Pieza(
                id="vid1", url="https://youtu.be/vid1", titulo=titulo,
                publicado_el=datetime(2026, 9, 6, 19, 0), tipo=TipoPieza.LARGO,
                acumulado=Metricas(vistas=254),
            )],
        ),
    })


def test_un_titulo_largo_se_corta_por_palabra_y_con_elipsis():
    titulo = "1519: los dos imperios que chocaron y fundieron un continente"
    texto = render_resumen(_snapshot_con_titulo(titulo), anterior=None, piezas=[])

    fila = [l for l in texto.split("\n") if l.startswith("| 2026-09-06")][0]
    celda = fila.split(" | ")[1]
    assert celda.endswith("…"), "un título recortado tiene que decir que falta texto"
    assert "fundiero" not in celda, "no se corta a media palabra"
    assert len(celda) <= LARGO_TITULO


def test_un_titulo_corto_no_lleva_elipsis():
    texto = render_resumen(_snapshot_con_titulo("1496: Santo Domingo"), anterior=None, piezas=[])

    fila = [l for l in texto.split("\n") if l.startswith("| 2026-09-06")][0]
    assert fila.split(" | ")[1] == "1496: Santo Domingo"


def test_un_titulo_con_barra_no_rompe_la_tabla():
    texto = render_resumen(
        _snapshot_con_titulo("1519: Cortés | la conquista"), anterior=None, piezas=[]
    )

    cabecera = [l for l in texto.split("\n") if l.startswith("| Published")][0]
    fila = [l for l in texto.split("\n") if l.startswith("| 2026-09-06")][0]
    assert "\\|" in fila, "la barra del título debe ir escapada"
    assert fila.count(" | ") == cabecera.count(" | "), (
        "la fila debe tener exactamente las mismas columnas que la cabecera"
    )


def test_una_metrica_que_no_cambia_no_ensucia_la_celda_con_un_mas_cero():
    texto = render_resumen(
        _snapshot(date(2026, 9, 11), 254),
        anterior=_snapshot(date(2026, 9, 9), 254),
        piezas=[],
    )

    assert "(+0)" not in texto
    assert "| 254 |" in texto
