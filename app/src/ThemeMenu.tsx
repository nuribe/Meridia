/** Selector de tema (🎨) + controles de zoom de la interfaz. */
import { useState } from "react";
import { THEMES, applyTheme, currentTheme } from "./theme";
import ZoomControls from "./ZoomControls";

export default function ThemeMenu() {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(currentTheme());

  return (
    <div className="d-flex align-items-center gap-2">
      <ZoomControls />
      <div className="position-relative">
      <button
        className="btn btn-sm btn-outline-secondary"
        onClick={() => setOpen(!open)}
        title="Cambiar tema de la aplicación"
      >
        🎨
      </button>
      {open && (
        <>
          <div
            style={{ position: "fixed", inset: 0, zIndex: 1049 }}
            onClick={() => setOpen(false)}
          />
          <div
            className="shadow border rounded bg-body py-1"
            style={{ position: "absolute", top: "calc(100% + 4px)", right: 0, zIndex: 1050, minWidth: 170 }}
          >
            {THEMES.map((t) => (
              <div
                key={t.id}
                className="d-flex align-items-center gap-2 px-3 py-2 pgtree-item"
                style={{ cursor: "pointer", fontSize: 13 }}
                onClick={() => {
                  applyTheme(t.id);
                  setActive(t.id);
                  setOpen(false);
                }}
              >
                <span
                  style={{
                    width: 14,
                    height: 14,
                    borderRadius: 4,
                    background: t.swatch,
                    border: "1px solid rgba(0,0,0,.2)",
                    flexShrink: 0,
                  }}
                />
                <span className={active === t.id ? "fw-semibold" : ""}>{t.label}</span>
                {active === t.id && <span className="ms-auto text-success">✓</span>}
              </div>
            ))}
          </div>
        </>
      )}
      </div>
    </div>
  );
}
