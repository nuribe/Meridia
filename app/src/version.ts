/**
 * Versión de la build en curso.
 *
 * La inyecta el proceso de compilación (`VITE_APP_VERSION`) con el número que
 * lleva el release: `0.1.0+build.42` en las builds de `main`, o la versión
 * limpia del tag en los releases versionados. Compilando en local no está
 * definida y queda como `dev`.
 *
 * Sirve para que quien reporta un problema pueda decir exactamente qué build
 * está usando; es el mismo número que encabeza el NOVEDADES.md del release.
 */
export const APP_VERSION: string = import.meta.env.VITE_APP_VERSION ?? "dev";

/** ¿Es una build local sin versionar? */
export const IS_DEV_BUILD = APP_VERSION === "dev";
