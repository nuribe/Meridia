/// <reference types="vite/client" />

/** Variables de entorno propias inyectadas en tiempo de compilación. */
interface ImportMetaEnv {
  /** Versión de la build (`0.1.0+build.42`); ausente en desarrollo. */
  readonly VITE_APP_VERSION?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
