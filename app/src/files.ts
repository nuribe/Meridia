/**
 * Guardado y apertura de archivos.
 * En Tauri usa diálogos nativos (comandos export_file / import_file del shell);
 * en navegador cae a descarga con anchor y <input type="file">.
 */
import { invoke } from "@tauri-apps/api/core";

const inTauri = () => "__TAURI_INTERNALS__" in window;

/** Guarda un archivo (dataUrl). Devuelve la ruta/nombre o null si se canceló. */
export async function saveFile(filename: string, dataUrl: string): Promise<string | null> {
  if (inTauri()) {
    const buf = await (await fetch(dataUrl)).arrayBuffer();
    const bytes = new Uint8Array(buf);
    let binary = "";
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
    }
    return invoke<string | null>("export_file", { filename, dataBase64: btoa(binary) });
  }
  const a = document.createElement("a");
  a.href = dataUrl;
  a.download = filename;
  a.click();
  return filename;
}

export async function saveTextFile(
  filename: string,
  content: string,
  mime = "text/plain"
): Promise<string | null> {
  const url = URL.createObjectURL(new Blob([content], { type: mime }));
  try {
    return await saveFile(filename, url);
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** Abre un diálogo nativo para elegir una carpeta. null si canceló o no hay Tauri. */
export async function pickDirectory(): Promise<string | null> {
  if (!inTauri()) return null;
  return invoke<string | null>("pick_directory");
}

/** Abre un archivo de texto elegido por el usuario; null si canceló. */
export async function openTextFile(
  extensions: string[]
): Promise<{ name: string; content: string } | null> {
  if (inTauri()) {
    const r = await invoke<{ name: string; data_base64: string } | null>("import_file", {
      extensions,
    });
    if (!r) return null;
    const bytes = Uint8Array.from(atob(r.data_base64), (c) => c.charCodeAt(0));
    return { name: r.name, content: new TextDecoder().decode(bytes) };
  }
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = extensions.map((e) => `.${e}`).join(",");
    input.onchange = () => {
      const f = input.files?.[0];
      if (!f) return resolve(null);
      const fr = new FileReader();
      fr.onload = () => resolve({ name: f.name, content: String(fr.result) });
      fr.readAsText(f);
    };
    input.click();
  });
}
