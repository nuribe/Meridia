# Iconos

Icono placeholder generado programáticamente (BD + nodos de diagrama).
Para reemplazarlo desde un PNG cuadrado (1024×1024):

```bash
cd app
npm run tauri icon ruta/al/icono.png
```

Esto crea 32x32.png, 128x128.png, icon.ico e icon.icns que referencia `tauri.conf.json`.
`tauri dev` funciona sin iconos; `tauri build` los requiere.
