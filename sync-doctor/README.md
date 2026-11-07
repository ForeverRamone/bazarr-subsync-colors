# Sync Doctor (fase 1: solo informe)

Docker que revisa las sincronizaciones de subtítulos que ha hecho Bazarr y te dice cuáles están bien y cuáles no. **No modifica ningún archivo**: monta tu carpeta de vídeo en solo lectura.

## Cómo decide

Para cada subtítulo amarillo o rojo (offset de Bazarr > 1 s), y para unos cuantos verdes como grupo de control:

1. Busca en Bazarr el vídeo y el subtítulo **actuales** (si la película se renombró o se actualizó después, usa el sub que hay ahora).
2. Extrae el audio una vez y pasa dos programas independientes sobre una copia del subtítulo: **ffsubsync** (el mismo que usa Bazarr) y **alass** (otro método, capaz de detectar montajes distintos).
3. Mide cuánto movería cada uno el subtítulo:

| Veredicto | Significa |
|---|---|
| **Bien** | Los dos lo dejan donde está (menos de 0,5 s). Bazarr acertó aunque el offset fuera grande. |
| **Corregible** | Los dos coinciden en moverlo lo mismo. Se podrá corregir automáticamente en la fase 2. |
| **Otra versión** | alass tuvo que trocear el sub: probablemente es de otro montaje. Conviene buscar otro sub. |
| **Dudoso** | Los programas no se ponen de acuerdo. Conviene buscar otro sub o revisarlo a mano. |
| **Ya no está** | Ese sub se borró o el vídeo cambió después: la sincronización ya no aplica. |

Cada comprobación tarda unos minutos por película (lo que más tarda es leer el audio). Trabaja de una en una y, si quieres, solo de noche.

## Instalación en Synology (Container Manager)

1. Crea la carpeta `/volume1/docker/sync-doctor`.
2. Container Manager → **Proyecto** → **Crear**. Nombre `sync-doctor`, ruta `/volume1/docker/sync-doctor`, y en origen elige **Crear docker-compose.yml** y pega el contenido de [`docker-compose.yml`](docker-compose.yml).
3. Cambia `BAZARR_API_KEY` por la clave de Bazarr (*Settings → General → Security → API Key*).
4. Crea el proyecto. Abre `http://192.168.0.60:8687` para ver el informe.

## Ajustes (variables de entorno)

| Variable | Por defecto | Qué hace |
|---|---|---|
| `BAZARR_URL` | — | Dirección de Bazarr |
| `BAZARR_API_KEY` | — | Clave de la API de Bazarr (solo se usa para leer) |
| `MEDIA_TYPES` | `movies,series` | Qué revisar: `movies` (películas), `series` o las dos. Al quitar un tipo, sus resultados salen del informe en el siguiente escaneo. |
| `ACTIVE_HOURS` | vacío (siempre) | Horas en las que trabaja, p. ej. `1-8` |
| `SCAN_INTERVAL_MINUTES` | `60` | Cada cuánto lee el historial de Bazarr |
| `CONTROL_SAMPLES` | `5` | Subs verdes que también comprueba, para calibrar |
| `GREEN_MAX` / `YELLOW_MAX` | `1` / `10` | Umbrales de color (segundos), como el userscript |
| `OK_TOLERANCE` / `AGREE_TOLERANCE` | `0.5` / `0.5` | Margen para "bien" y para "los dos coinciden" |
| `SPLIT_THRESHOLD` | `0.15` | Proporción de líneas fuera de sitio para considerar "otra versión" |
| `PATH_MAP` | vacío | Solo si el contenedor ve los archivos en otra ruta que Bazarr: `/video=/otra/ruta` |

## Seguridad

- La carpeta de vídeo se monta en **solo lectura** (`:ro`): el contenedor no puede modificar ni borrar nada.
- La clave de la API solo se usa para **leer** el historial y los datos de películas y episodios.
- La web del informe no tiene contraseña: no abras el puerto 8687 fuera de tu red.

## Próximas fases

- **Fase 2**: corregir automáticamente los "corregibles" (con copia de seguridad) y pedir a Bazarr otro sub para los de "otra versión" y "dudoso".
- **Fase 3**: que el userscript muestre el veredicto en el historial de Bazarr.
