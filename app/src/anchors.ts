/**
 * Anclajes del contorno de un nodo — versión CONTINUA.
 *
 * Historia, porque explica la forma del código:
 *
 * 1. Al principio cada lado tenía un único handle centrado: una tabla con
 *    muchas FKs sacaba todas las aristas del mismo píxel.
 * 2. Después se puso una rejilla de N slots por lado. Resolvió el amontonamiento
 *    pero introdujo otro defecto: al arrastrar una tabla el punto de enlace
 *    saltaba de slot en slot, con tirones de decenas de píxeles.
 * 3. Ahora el punto de enlace es una **fracción real** del lado (0..1). No hay
 *    rejilla, así que al mover una tabla el anclaje se desliza por el contorno
 *    de forma continua.
 *
 * Para que esto funcione, la geometría de la arista NO puede depender de dónde
 * esté el `<Handle>` en el DOM: React Flow solo recalcula esas posiciones si se
 * llama a `updateNodeInternals`, que además mediría el DOM en cada fotograma del
 * arrastre. En su lugar, cada arista (ver `RelEdge`) calcula sus dos extremos a
 * partir de la caja del nodo y de la fracción que le toca. Los `<Handle>` que
 * quedan son solo el mínimo que React Flow necesita para enlazar source/target,
 * uno por lado y rol, invisibles y siempre en el centro del lado.
 */
import { createContext } from "react";
import { Position } from "@xyflow/react";

export type Side = "left" | "right" | "top" | "bottom";
export const SIDES: Side[] = ["left", "right", "top", "bottom"];

/** "s" = source (sale la arista), "t" = target (entra la arista). */
export type HandleRole = "s" | "t";

export const SIDE_POSITION: Record<Side, Position> = {
  left: Position.Left,
  right: Position.Right,
  top: Position.Top,
  bottom: Position.Bottom,
};

/** Los lados izquierdo/derecho se recorren en Y; arriba/abajo, en X. */
export function isVertical(side: Side): boolean {
  return side === "left" || side === "right";
}

/** Un handle por lado y rol: `s-right`, `t-top`, … */
export function handleId(role: HandleRole, side: Side): string {
  return `${role}-${side}`;
}

export function sideOfHandle(id: string): Side {
  return id.split("-")[1] as Side;
}

/** Punto de enganche de una arista sobre el contorno de un nodo. */
export interface Anchor {
  side: Side;
  /** Posición a lo largo del lado, 0..1 (fracción del alto o del ancho). */
  frac: number;
}

/**
 * Anclajes ocupados por nodo: `{ "public.orders": [{side:"right",frac:.42}, …] }`.
 *
 * Va por contexto y no por `node.data` a propósito: si viviera en los datos del
 * nodo, actualizarlo dispararía `setNodes`, que recalcularía cajas → aristas →
 * anclajes, en bucle. Por contexto, TableNode se entera sin tocar el estado.
 *
 * TableNode los usa solo para pintar el puntito decorativo del contorno; la
 * geometría de la arista no depende de ellos.
 */
export type AnchorUsage = Record<string, Anchor[]>;

export const AnchorUsageContext = createContext<AnchorUsage>({});
