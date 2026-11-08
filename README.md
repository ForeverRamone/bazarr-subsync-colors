# Bazarr – colores de sincronización

> En este repositorio hay también **[Sync Doctor](sync-doctor/)**, un docker que comprueba con ffsubsync y alass si las sincronizaciones de Bazarr han salido bien.

Userscript para Chrome que hace visible el desplazamiento (offset) que aplica Bazarr al sincronizar subtítulos. No modifica Bazarr: solo cambia cómo se ve la web en tu navegador, así que las actualizaciones de Bazarr siguen llegando con normalidad.

## Qué hace

- **Historial de películas y series** (`/history/movies`, `/history/series`): cada fila de sincronización se colorea y lleva una etiqueta con el offset junto al icono de Info.
  - 🟢 verde: |offset| ≤ 1 s
  - 🟡 amarillo: |offset| ≤ 10 s, o el framerate se ha corregido (factor ≠ 1.00)
  - 🔴 rojo: |offset| > 10 s
- **Botón "⏱ Syncs"** (abajo a la derecha, en cualquier página de Bazarr): abre un panel con los subtítulos a revisar en todo el historial, ordenados de peor a mejor y con enlace a la película o serie. Solo cuenta la última sincronización de cada archivo de subtítulos: si después se descargó otro sub en la misma ruta, la sincronización antigua no aparece.
- **Veredicto de Sync Doctor** (si lo usas): junto al título de cada sincronización que Sync Doctor haya comprobado aparece su etiqueta (⚕ Bien, ⚕ Otra versión, ⚕ Dudoso…), y la fila se colorea según ese veredicto en vez de por el offset. Así, un rojo que Sync Doctor ha comprobado y está bien sale en verde. Al pasar el ratón ves qué dijeron ffsubsync y alass; al hacer clic se abre Sync Doctor filtrado por ese título. El panel "⏱ Syncs" también muestra el veredicto.

## Instalación (Chrome)

1. Instala la extensión **Tampermonkey** desde la Chrome Web Store (Violentmonkey también vale).
2. En `chrome://extensions` → Tampermonkey → **Detalles**, activa **"Permitir scripts de usuario"**. En algunas versiones de Chrome lo que hay que activar es el **Modo de desarrollador**, arriba a la derecha.
3. Abre este enlace y pulsa **Instalar** en la pantalla de Tampermonkey:
   **[Instalar bazarr-subsync-colors.user.js](https://raw.githubusercontent.com/ForeverRamone/bazarr-subsync-colors/main/bazarr-subsync-colors.user.js)**
4. Recarga Bazarr (`http://192.168.0.60:6767/history/movies`).

Tampermonkey comprueba de vez en cuando si hay versión nueva en este repositorio y la instala sola. Para que una actualización se aplique hay que subir el número de `@version` del script.

## Ajustes

Al principio del script:

```js
const CONFIG = {
  greenMax: 1,      // segundos
  yellowMax: 10,    // segundos
  framerateCountsAsWarning: true,
  doctorUrl: "http://192.168.0.60:8687",  // Sync Doctor; vacío = no mostrar veredictos
  doctorColors: true,                     // colorear por el veredicto de Sync Doctor
};
```

Si Bazarr cambia de IP o de puerto, edita la línea `// @match http://192.168.0.60:6767/*`, o en Tampermonkey añade la nueva dirección en *Ajustes del script → Coincidencias del usuario*, que se mantiene aunque el script se actualice.

## Seguridad

- El script no lleva claves ni contraseñas. Usa la clave de la API que Bazarr ya carga en tu página cuando has iniciado sesión y solo la envía a tu propio Bazarr.
- Solo hace peticiones a tu Bazarr y, si está configurado, a tu Sync Doctor (para leer sus veredictos). No usa librerías externas.
- Con las actualizaciones automáticas, lo que se suba a este repositorio se ejecuta en tu página de Bazarr. Protege la cuenta de GitHub con verificación en dos pasos.

## Cómo funciona (por si algún día deja de funcionar)

- Bazarr guarda cada sincronización como una entrada del historial con el texto *"… synchronization ended with an offset of X seconds and a framerate scale factor of Y."*.
- En la tabla, el script lee los datos de cada fila desde React (TanStack Table pasa `row.original` a cada celda) y los compara con el texto visible de la fila, para no confundirse cuando React reutiliza filas al cambiar de página.
- El panel usa la API de Bazarr (`/api/movies/history` y `/api/episodes/history`) con la clave que Bazarr carga en la página cuando has iniciado sesión.

Si una actualización de Bazarr cambia ese texto o la tabla, lo normal es que solo haya que retocar la expresión `SYNC_RE` o la función `getRowData`.

Probado contra Bazarr con React 19 (tu instancia, 2026-11-07) y con una maqueta en React 18.
