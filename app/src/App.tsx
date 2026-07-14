/**
 * Flujo principal: perfiles de conexión → bases de datos → explorador → diagramas.
 */
import { useEffect, useState } from "react";
import { api, health, type ApiError, type DatabaseInfo, type PgDiagFile, type Profile } from "./api/client";
import Explorer from "./Explorer";
import DiagramView, { OfflineDiagramView } from "./DiagramView";
import ThemeMenu from "./ThemeMenu";
import { openTextFile } from "./files";
import { registerZoomShortcuts } from "./zoom";

type View =
  | { name: "profiles" }
  | { name: "databases"; profile: Profile }
  | { name: "workbench"; profile: Profile; dbname: string; mode: "explorer" | "diagram" }
  | { name: "offline"; doc: PgDiagFile };

function errText(e: unknown): string {
  const err = e as ApiError;
  return `${err.code ?? "ERROR"}: ${err.message ?? String(e)}${err.hint ? ` — ${err.hint}` : ""}`;
}

export default function App() {
  const [view, setView] = useState<View>({ name: "profiles" });
  const [sidecarOk, setSidecarOk] = useState<string | null>(null);

  useEffect(() => {
    health()
      .then((h) => setSidecarOk(`v${h.version}`))
      .catch((e) => setSidecarOk(`error: ${String(e)}`));
  }, []);

  // Atajos de zoom de la interfaz (Ctrl/⌘ + '=', '-', '0').
  useEffect(() => registerZoomShortcuts(), []);

  if (sidecarOk === null) {
    return (
      <div className="d-flex vh-100 align-items-center justify-content-center gap-2 text-body-secondary">
        <div className="spinner-border spinner-border-sm" role="status" />
        Conectando con el sidecar…
      </div>
    );
  }
  if (sidecarOk.startsWith("error")) {
    return (
      <div className="container py-5" style={{ maxWidth: 640 }}>
        <div className="alert alert-danger">
          <strong>Sidecar no disponible.</strong> {sidecarOk}
        </div>
      </div>
    );
  }

  switch (view.name) {
    case "profiles":
      return (
        <ProfilesScreen
          onOpen={(profile) => setView({ name: "databases", profile })}
          onOpenOffline={(doc) => setView({ name: "offline", doc })}
        />
      );
    case "databases":
      return (
        <DatabasesScreen
          profile={view.profile}
          onBack={() => setView({ name: "profiles" })}
          onOpen={(dbname) => setView({ name: "workbench", profile: view.profile, dbname, mode: "explorer" })}
        />
      );
    case "workbench":
      return (
        <>
          <div style={{ display: view.mode === "explorer" ? "block" : "none", height: "100vh" }}>
            <Explorer
              profileId={view.profile.id}
              dbname={view.dbname}
              onBack={() => setView({ name: "databases", profile: view.profile })}
              onOpenDiagram={() => setView({ ...view, mode: "diagram" })}
            />
          </div>
          <div style={{ display: view.mode === "diagram" ? "block" : "none", height: "100vh" }}>
            <DiagramView
              profileId={view.profile.id}
              dbname={view.dbname}
              onBack={() => setView({ ...view, mode: "explorer" })}
            />
          </div>
        </>
      );
    case "offline":
      return <OfflineDiagramView doc={view.doc} onBack={() => setView({ name: "profiles" })} />;
  }
}

// --- Pantalla 1: perfiles ---

function ProfilesScreen({
  onOpen,
  onOpenOffline,
}: {
  onOpen: (p: Profile) => void;
  onOpenOffline: (doc: PgDiagFile) => void;
}) {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [keychain, setKeychain] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const emptyForm = { name: "", host: "localhost", port: 5432, user: "postgres", password: "", ssl_mode: "prefer", dbname: "postgres" };
  const [form, setForm] = useState(emptyForm);
  // Perfil que se está editando (null = creando uno nuevo).
  const [editingId, setEditingId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function reload() {
    try {
      const r = await api.listProfiles();
      setProfiles(r.profiles);
      setKeychain(r.keychain);
      if (r.profiles.length === 0) {
        setEditingId(null);
        setForm(emptyForm);
        setShowForm(true);
      }
    } catch (e) {
      setError(errText(e));
    }
  }

  useEffect(() => {
    void reload();
  }, []);

  function startCreate() {
    setEditingId(null);
    setForm(emptyForm);
    setError("");
    setShowForm(true);
  }

  function startEdit(p: Profile) {
    setEditingId(p.id);
    // La contraseña se deja vacía: vacío = conservar la existente.
    setForm({ name: p.name, host: p.host, port: p.port, user: p.user, password: "", ssl_mode: p.ssl_mode, dbname: p.dbname });
    setError("");
    setShowForm(true);
  }

  function closeForm() {
    setShowForm(false);
    setEditingId(null);
    setForm(emptyForm);
  }

  const formValid = !!(form.name && form.host && form.user && form.dbname);

  async function save() {
    if (!formValid) return;
    setBusy(true);
    setError("");
    try {
      if (editingId) {
        await api.updateProfile(editingId, form);
        await reload();
        closeForm();
      } else {
        const r = await api.createProfile(form);
        await reload();
        closeForm();
        onOpen(r.profile);
      }
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    await api.deleteProfile(id);
    if (editingId === id) closeForm();
    await reload();
  }

  return (
    <div className="bg-body-tertiary min-vh-100 py-5">
      <div className="container" style={{ maxWidth: 620 }}>
        <div className="d-flex justify-content-end mb-2">
          <ThemeMenu />
        </div>
        <div className="text-center mb-4">
          <h1 className="fs-3 mb-1">🐘 Meridia</h1>
          <p className="text-body-secondary mb-0">Explora PostgreSQL y crea diagramas ER</p>
        </div>

        {!keychain && (
          <div className="alert alert-warning py-2 small">
            Sin keychain del SO disponible: las contraseñas solo se recuerdan durante esta sesión.
          </div>
        )}
        {error && <div className="alert alert-danger py-2">{error}</div>}

        {profiles.length > 0 && (
          <div className="card shadow-sm mb-3">
            <div className="card-header py-2 fw-semibold">Conexiones</div>
            <div className="list-group list-group-flush">
              {profiles.map((p) => (
                <div key={p.id} className={`list-group-item d-flex align-items-center gap-2 py-2${editingId === p.id ? " active" : ""}`}>
                  <button
                    className="btn btn-link text-decoration-none text-start flex-grow-1 p-0"
                    onClick={() => onOpen(p)}
                  >
                    <span className="fw-semibold">🐘 {p.name}</span>{" "}
                    <small className="text-body-secondary">
                      {p.user}@{p.host}:{p.port}/{p.dbname} · SSL {p.ssl_mode}
                    </small>
                  </button>
                  <button
                    className="btn btn-sm btn-outline-secondary border-0"
                    onClick={() => startEdit(p)}
                    title="Editar conexión"
                  >
                    ✎
                  </button>
                  <button
                    className="btn btn-sm btn-outline-danger border-0"
                    onClick={() => void remove(p.id)}
                    title="Eliminar perfil"
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {showForm ? (
          <div className="card shadow-sm">
            <div className="card-header py-2 fw-semibold">
              {editingId ? "Editar conexión" : "Nueva conexión"}
            </div>
            <div className="card-body">
              <div className="row g-2">
                <div className="col-7">
                  <input className="form-control" placeholder="Nombre del perfil" value={form.name}
                         onChange={(e) => setForm({ ...form, name: e.target.value })} />
                </div>
                <div className="col-5">
                  <select className="form-select" value={form.ssl_mode}
                          onChange={(e) => setForm({ ...form, ssl_mode: e.target.value })}>
                    <option value="disable">SSL: disable</option>
                    <option value="prefer">SSL: prefer</option>
                    <option value="require">SSL: require</option>
                    <option value="verify-ca">SSL: verify-ca</option>
                    <option value="verify-full">SSL: verify-full</option>
                  </select>
                </div>
                <div className="col-8">
                  <input className="form-control" placeholder="Host" value={form.host}
                         onChange={(e) => setForm({ ...form, host: e.target.value })} />
                </div>
                <div className="col-4">
                  <input className="form-control" placeholder="Puerto" type="number" value={form.port}
                         onChange={(e) => setForm({ ...form, port: Number(e.target.value) })} />
                </div>
                <div className="col-6">
                  <input className="form-control" placeholder="Usuario" value={form.user}
                         onChange={(e) => setForm({ ...form, user: e.target.value })} />
                </div>
                <div className="col-6">
                  <input className="form-control"
                         placeholder={editingId ? "Contraseña (vacío = sin cambios)" : "Contraseña"}
                         type="password" value={form.password}
                         onChange={(e) => setForm({ ...form, password: e.target.value })}
                         onKeyDown={(e) => e.key === "Enter" && !busy && formValid && void save()} />
                </div>
                <div className="col-12">
                  <input
                    className="form-control"
                    placeholder="Nombre de la base de datos"
                    value={form.dbname}
                    onChange={(e) => setForm({ ...form, dbname: e.target.value })}
                  />
                  <div className="form-text">
                    Base a la que conectar. Con <strong>pgbouncer</strong> u otro pooler, indica una
                    base que exista en su pool. Desde ella se listan las demás del servidor.
                  </div>
                </div>
              </div>
              <div className="d-flex gap-2 mt-3">
                <button
                  className="btn btn-primary"
                  disabled={busy || !formValid}
                  onClick={() => void save()}
                >
                  {busy ? (
                    <>
                      <span className="spinner-border spinner-border-sm me-1" /> Guardando…
                    </>
                  ) : editingId ? (
                    "Guardar cambios"
                  ) : (
                    "Guardar y conectar"
                  )}
                </button>
                {(profiles.length > 0 || editingId) && (
                  <button className="btn btn-outline-secondary" onClick={closeForm}>
                    Cancelar
                  </button>
                )}
              </div>
            </div>
          </div>
        ) : (
          <button className="btn btn-outline-primary" onClick={startCreate}>
            ＋ Nueva conexión
          </button>
        )}

        <div className="mt-3">
          <button
            className="btn btn-outline-secondary"
            title="Ver un diagrama guardado como archivo, sin conectarse a la base de datos"
            onClick={() =>
              void (async () => {
                const f = await openTextFile(["pgdiag", "json"]);
                if (!f) return;
                try {
                  const doc = JSON.parse(f.content) as PgDiagFile;
                  if (doc.format !== "pgdiag" || !doc.tables) {
                    setError(
                      "El archivo no es un .pgdiag v2. Ábrelo conectado a la BD y vuelve a guardarlo con «💾 Archivo»."
                    );
                    return;
                  }
                  onOpenOffline(doc);
                } catch {
                  setError("Archivo .pgdiag inválido.");
                }
              })()
            }
          >
            📂 Abrir diagrama sin conexión…
          </button>
        </div>
      </div>
    </div>
  );
}

// --- Pantalla 2: bases de datos ---

function DatabasesScreen({
  profile,
  onBack,
  onOpen,
}: {
  profile: Profile;
  onBack: () => void;
  onOpen: (dbname: string) => void;
}) {
  const [databases, setDatabases] = useState<DatabaseInfo[] | null>(null);
  const [error, setError] = useState("");
  const [needsPassword, setNeedsPassword] = useState(false);
  const [password, setPassword] = useState("");
  const [manualDb, setManualDb] = useState("");

  async function load() {
    setError("");
    setNeedsPassword(false);
    try {
      const r = await api.listDatabases(profile.id);
      setDatabases(r.databases);
    } catch (e) {
      const err = e as ApiError;
      if (err.code === "PASSWORD_REQUIRED") {
        setNeedsPassword(true);
      } else {
        setError(errText(e));
      }
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profile.id]);

  async function submitPassword() {
    try {
      await api.setSessionPassword(profile.id, password);
      setPassword("");
      await load();
    } catch (e) {
      setError(errText(e));
    }
  }

  return (
    <div className="bg-body-tertiary min-vh-100 py-5">
      <div className="container" style={{ maxWidth: 620 }}>
        <button className="btn btn-sm btn-outline-secondary mb-3" onClick={onBack}>
          ← Conexiones
        </button>
        <h2 className="fs-4 mb-0">🐘 {profile.name}</h2>
        <p className="text-body-secondary">
          {profile.user}@{profile.host}:{profile.port}
        </p>

        {error && (
          <div className="alert alert-danger py-2">
            <div>{error}</div>
            {error.includes("bouncer") && (
              <div className="small mt-2">
                💡 Estás conectando a través de <strong>pgbouncer</strong>, que no permite listar
                todas las bases de datos. Usa «Conectar por nombre» más abajo con el nombre exacto
                de tu base (por ejemplo <code>{profile.name.toLowerCase()}</code>).
              </div>
            )}
          </div>
        )}

        {/* Alternativa por nombre: solo cuando el listado falla (pooler mal
            configurado, permisos, etc.). Siempre útil como escape. */}
        {error && !needsPassword && (
          <div className="card shadow-sm mb-3">
            <div className="card-header py-2 fw-semibold">Conectar por nombre de base de datos</div>
            <div className="card-body d-flex gap-2 align-items-center">
              <input
                className="form-control"
                placeholder="nombre de la base (p. ej. padron)"
                value={manualDb}
                onChange={(e) => setManualDb(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && manualDb.trim() && onOpen(manualDb.trim())}
              />
              <button
                className="btn btn-primary flex-shrink-0"
                disabled={!manualDb.trim()}
                onClick={() => onOpen(manualDb.trim())}
                title="Abrir directamente esa base de datos (sin listar todas)"
              >
                Conectar →
              </button>
            </div>
          </div>
        )}

        {needsPassword && (
          <div className="card shadow-sm mb-3">
            <div className="card-body d-flex gap-2">
              <input
                className="form-control"
                type="password"
                placeholder="Contraseña (esta sesión)"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && void submitPassword()}
              />
              <button className="btn btn-primary" onClick={() => void submitPassword()}>
                Conectar
              </button>
            </div>
          </div>
        )}

        {databases === null && !needsPassword && !error && (
          <div className="d-flex align-items-center gap-2 text-body-secondary">
            <div className="spinner-border spinner-border-sm" /> Cargando bases de datos…
          </div>
        )}

        {databases && databases.length > 0 && (
          <div className="card shadow-sm">
            <div className="card-header py-2 fw-semibold">Bases de datos ({databases.length})</div>
            <div className="list-group list-group-flush">
              {databases.map((db) => (
                <button
                  key={db.name}
                  className="list-group-item list-group-item-action d-flex align-items-center gap-2 py-2"
                  onClick={() => onOpen(db.name)}
                >
                  <span className="fw-semibold flex-grow-1">🗄 {db.name}</span>
                  <small className="text-body-secondary">{db.owner}</small>
                  <span className="badge text-bg-light border">{db.encoding}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
