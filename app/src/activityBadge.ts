/**
 * Contador de llamadas MCP sin revisar, para el badge del conmutador de modo.
 *
 * Es un store externo y no estado de React a propósito: `ModeSwitch` está
 * montado a la vez en Explorador y en Diagramas (ambos viven en el DOM, uno
 * con `display: none`), así que con un `useEffect` por instancia habría dos
 * sondeos midiendo lo mismo. Aquí hay un único intervalo mientras haya al menos
 * un suscriptor, y ninguno cuando no hay nadie mirando.
 */
import { useSyncExternalStore } from "react";
import { mcpApi } from "./api/client";

/** Sondeo lento: el badge solo avisa, el panel abierto refresca por su cuenta. */
const POLL_MS = 10_000;

let unseen = 0;
let total = 0;
let seen = 0;
let timer: ReturnType<typeof setInterval> | null = null;
const listeners = new Set<() => void>();

function emit() {
  for (const fn of listeners) fn();
}

function set(next: number) {
  if (next === unseen) return; // sin cambio: no repintamos
  unseen = next;
  emit();
}

async function poll() {
  try {
    // `since = total` hace que la respuesta venga vacía de eventos: aquí solo
    // interesa el contador, los eventos los pide el panel cuando está abierto.
    const r = await mcpApi.activity(total, 1);
    total = r.total;
    if (seen > total) seen = total; // hubo rotación o limpieza
    set(Math.max(0, total - seen));
  } catch {
    // Sin sidecar no hay badge; el error real ya se ve en el resto de la app.
  }
}

/** Lo llama el panel: a partir de aquí, todo lo registrado cuenta como visto. */
export function markActivitySeen(seenTotal: number) {
  total = Math.max(total, seenTotal);
  seen = seenTotal;
  set(Math.max(0, total - seen));
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  if (timer === null) {
    void poll();
    timer = setInterval(poll, POLL_MS);
  }
  return () => {
    listeners.delete(fn);
    if (listeners.size === 0 && timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  };
}

export function useUnseenActivity(): number {
  return useSyncExternalStore(
    subscribe,
    () => unseen,
    () => 0, // sin servidor de render, pero React lo exige
  );
}
