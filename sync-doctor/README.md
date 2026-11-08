# Sync Doctor

Docker que revisa las sincronizaciones de subtítulos que ha hecho Bazarr, te dice cuáles están bien y cuáles no, y **le pide a Bazarr que las arregle**: con un botón o automáticamente.

Sync Doctor no toca tus archivos: monta la carpeta de vídeo en solo lectura. Los cambios los hace Bazarr cuando Sync Doctor se lo pide por su API.

## Cómo decide

Para cada subtítulo amarillo o rojo (offset de Bazarr > 1 s), y para unos cuantos verdes como grupo de control:

1. Busca en Bazarr el vídeo y el subtítulo **actuales** (si la película se renombró o se actualizó después, usa el sub que hay ahora).
2. Extrae el audio una vez y pasa dos programas independientes sobre una copia del subtítulo: **ffsubsync** (el mismo que usa Bazarr) y **alass** (otro método, capaz de detectar montajes distintos).
3. Mide cuánto movería cada uno el subtítulo:

| Veredicto | Significa | Acción disponible |
|---|---|---|
| **Bien** | Los dos lo dejan donde está (menos de 0,5 s). Bazarr acertó aunque el offset fuera grande. | — |
| **Corregible** | Los dos coinciden en moverlo lo mismo. | **Aplicar ±X s** |
| **Otra versión** | alass tuvo que trocear el sub: probablemente es de otro montaje. | **Descartar y buscar otro** |
| **Dudoso** | Los programas no se ponen de acuerdo. | **Descartar y buscar otro** |
| **Ya no está** | Ese sub se borró o el vídeo cambió después. | **Buscar sub** |

Cada comprobación tarda unos minutos por película (lo que más tarda es leer el audio). Trabaja de una en una.

### Subtítulos sin sincronización en el historial

Por defecto solo se revisa lo que aparece en el historial de Bazarr. Con **Ajustes → "También los subtítulos del disco sin sincronización en el historial"** (o `SCAN_DISK=1`) revisa además todos los `.srt` **externos** que Bazarr tiene registrados para cada película o episodio: los descargados antes de que empezara el historial, los subidos a mano, los renombrados… Los subtítulos internos del vídeo no se revisan.

Se comprueban todos una vez (y otra si el archivo cambia), después de los rojos y amarillos. En la lista **solo aparecen los que tienen algún problema**; los que están bien se ven marcando "Del disco que están bien".

## Acciones sobre Bazarr

| Botón | Qué hace Bazarr |
|---|---|
| **Aplicar ±X s** | Desplaza el sub con su herramienta "ajustar tiempos" (la media de lo que dicen ffsubsync y alass). Después Sync Doctor lo vuelve a comprobar. |
| **Descartar y buscar otro** | Borra el sub, lo mete en su **lista negra** y descarga el siguiente mejor (que sincroniza como siempre). Si el sub no tiene proveedor (subido a mano), solo lo borra y pide otro: podría volver a elegir el mismo. |
| **Buscar sub** | Busca y descarga el mejor sub de ese idioma. |
| **Marcar como bien** | No toca Bazarr: si lo has comprobado tú y está bien, deja de salir como problema ("Bien ✓ a mano") y las acciones automáticas no lo tocan. **Quitar marca** lo deshace. Si se vuelve a comprobar (o el sub cambia), manda la comprobación nueva. |

Tras descartar o buscar, Sync Doctor mira el historial de Bazarr cada minuto durante 30 minutos. En cuanto aparece el sub nuevo, **lo comprueba al momento**, a cualquier hora. Si Bazarr no encuentra nada, la fila lo indica para que lo revises a mano.

**Freno:** como máximo 3 acciones automáticas por título e idioma (configurable). Así no vacía todos los proveedores con una película que no tiene buenos subs. Cuando un sub queda **Bien**, el contador vuelve a cero.

## Uso desde la web

- **⚙ Ajustes** (arriba a la derecha): qué revisar (películas, series), horario automático, verdes de control, **acciones automáticas** y máximo de acciones. Se guardan en la base de datos y mandan sobre las variables de entorno.
- **Comprobar ahora** (en cada fila): la comprueba en cuanto termine lo que esté en marcha, aunque esté fuera de horario.
- **Comprobar pendientes ahora**: procesa toda la cola sin esperar al horario automático.
- **Detener**: corta la comprobación en marcha (vuelve a la cola) y anula lo pedido a mano.
- **Leer Bazarr ahora**: busca sincronizaciones nuevas sin esperar al siguiente escaneo.
- **Registro** (abajo): qué está haciendo en cada momento, incluidas las acciones enviadas a Bazarr.

Las acciones automáticas vienen **desactivadas**. Actívalas en Ajustes cuando hayas visto que los veredictos aciertan.

## Instalación en Synology (Container Manager)

1. Crea la carpeta `/volume1/docker/sync-doctor`.
2. Container Manager → **Proyecto** → **Crear**. Nombre `sync-doctor`, ruta `/volume1/docker/sync-doctor`, y en origen elige **Crear docker-compose.yml** y pega el contenido de [`docker-compose.yml`](docker-compose.yml).
3. Cambia `BAZARR_API_KEY` por la clave de Bazarr (*Settings → General → Security → API Key*).
4. Crea el proyecto. Abre `http://192.168.0.60:8687` para ver el informe.

Para actualizar desde Portainer: **Containers → sync-doctor → Recreate** con **Re-pull image** activado.

## Variables de entorno

Las marcadas con ⚙ también se cambian desde Ajustes en la web (lo guardado allí manda).

| Variable | Por defecto | Qué hace |
|---|---|---|
| `BAZARR_URL` | — | Dirección de Bazarr |
| `BAZARR_API_KEY` | — | Clave de la API de Bazarr |
| `MEDIA_TYPES` ⚙ | `movies,series` | Qué revisar: `movies` (películas), `series` o las dos |
| `ACTIVE_HOURS` ⚙ | vacío (siempre) | Horas en las que trabaja solo, p. ej. `1-8` |
| `SCAN_DISK` ⚙ | vacío (no) | `1` = revisar también los `.srt` externos sin sincronización en el historial |
| `CONTROL_SAMPLES` ⚙ | `5` | Subs verdes que también comprueba, para calibrar |
| `AUTO_ACTIONS` ⚙ | vacío (ninguna) | Veredictos con acción automática: `other_cut,disagree,fixable,gone` |
| `MAX_ATTEMPTS` ⚙ | `3` | Máximo de acciones automáticas por título e idioma |
| `SCAN_INTERVAL_MINUTES` | `60` | Cada cuánto lee el historial de Bazarr |
| `GREEN_MAX` / `YELLOW_MAX` | `1` / `10` | Umbrales de color (segundos), como el userscript |
| `OK_TOLERANCE` / `AGREE_TOLERANCE` | `0.5` / `0.5` | Margen para "bien" y para "los dos coinciden" |
| `SPLIT_THRESHOLD` | `0.15` | Proporción de líneas fuera de sitio para considerar "otra versión" |
| `PATH_MAP` | vacío | Solo si el contenedor ve los archivos en otra ruta que Bazarr: `/video=/otra/ruta` |

## Seguridad

- La carpeta de vídeo se monta en **solo lectura** (`:ro`): el contenedor no puede modificar ni borrar nada por sí mismo.
- Con la clave de la API, Sync Doctor puede pedir a Bazarr que **desplace, borre o ponga en lista negra** subtítulos, siempre por un botón tuyo o por las acciones automáticas que actives.
- La web no tiene contraseña y quien la abra puede lanzar esas acciones: no abras el puerto 8687 fuera de tu red.

## Próximas ideas

- Elegir a mano otro sub entre los candidatos que ofrece Bazarr.
- Que el userscript muestre el veredicto en el historial de Bazarr.
