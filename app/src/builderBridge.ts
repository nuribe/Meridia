/**
 * Puente entre el constructor gráfico de consultas (QueryBuilder) y el único
 * árbol de objetos del Explorador.
 *
 * En vez de montar un segundo ObjectTree dentro del lienzo del builder, el
 * QueryBuilder «registra» una sesión con el Explorador: expone cómo añadir una
 * tabla al lienzo y qué tablas ya están presentes. El Explorador usa esa sesión
 * para conmutar su árbol compartido a modo arrastrable — así los filtros del
 * explorador nunca se pierden al pasar de la consulta al diagrama.
 */
import { createContext, useContext } from "react";

export interface BuilderSession {
  /** Añade la tabla (`schema.nombre`) al lienzo del builder. */
  addTable: (key: string) => void;
  /** Tablas ya presentes en el lienzo (para atenuarlas en el árbol). */
  presentKeys: Set<string>;
}

/**
 * El Explorador provee esta función; el QueryBuilder la llama para registrar
 * (o limpiar, con `null`) su sesión activa. El setter de `useState` es estable,
 * así que consumirlo no reintroduce renders.
 */
export const SetBuilderSessionContext =
  createContext<(session: BuilderSession | null) => void>(() => {});

export function useSetBuilderSession() {
  return useContext(SetBuilderSessionContext);
}
