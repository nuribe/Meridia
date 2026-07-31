/**
 * Puntos de anclaje distribuidos por TODO el contorno del nodo.
 *
 * Antes cada lado de una tabla tenía un único handle centrado: si una tabla
 * tenía muchas relaciones, todas las aristas salían del mismo píxel y el
 * diagrama se volvía ilegible. Aquí definimos una rejilla de slots a lo largo
 * de cada lado; `DiagramView` reparte las relaciones entre esos slots para que
 * cada una tenga su propio punto de conexión.
 *
 * Los ids son estables (`s-left-3`, `t-bottom-0`, …) y todos los handles se
 * renderizan siempre, así React Flow no necesita `updateNodeInternals`.
 * Los que no se usan quedan invisibles (tamaño 1px, opacidad 0).
 */
import { createContext } from "react";
import { Position } from "@xyflow/react";

/** Slots por lado. Con 9 el contorno se percibe como continuo. */
export const SLOTS_PER_SIDE = 9;

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

export function clampSlot(slot: number): number {
  return Math.max(0, Math.min(SLOTS_PER_SIDE - 1, Math.round(slot)));
}

export function handleId(role: HandleRole, side: Side, slot: number): string {
  return `${role}-${side}-${clampSlot(slot)}`;
}

/** Slot central (el comportamiento clásico: un punto en mitad del lado). */
export const CENTER_SLOT = Math.floor((SLOTS_PER_SIDE - 1) / 2);

/**
 * Posición del slot a lo largo del lado, en porcentaje.
 * Con 9 slots: 10 %, 20 %, …, 90 % — sin llegar a las esquinas.
 */
export function slotOffset(slot: number): string {
  return `${((clampSlot(slot) + 1) * 100) / (SLOTS_PER_SIDE + 1)}%`;
}

/**
 * Reparte `n` aristas que comparten lado entre los slots disponibles.
 * - 1 arista → slot central (idéntico al comportamiento anterior).
 * - n ≥ 2 → repartidas por todo el contorno del lado, en orden.
 * - n > SLOTS_PER_SIDE → se comparten slots, pero manteniendo el orden.
 */
export function spreadSlots(n: number): number[] {
  if (n <= 0) return [];
  if (n === 1) return [CENTER_SLOT];
  return Array.from({ length: n }, (_, i) =>
    Math.round((i * (SLOTS_PER_SIDE - 1)) / (n - 1))
  );
}

/** Lado codificado en un id de handle (`s-left-3` → `left`). */
export function sideOfHandle(id: string): Side {
  return id.split("-")[1] as Side;
}

/**
 * Handles realmente ocupados por nodo: `{ "public.orders": ["s-right-0", …] }`.
 *
 * Va por contexto y no por `node.data` a propósito: si viviera en los datos del
 * nodo, actualizarlo dispararía `setNodes`, que recalcularía centros → aristas →
 * handles, en bucle. Por contexto, TableNode se entera sin tocar el estado.
 */
export type HandleUsage = Record<string, string[]>;

export const HandleUsageContext = createContext<HandleUsage>({});
