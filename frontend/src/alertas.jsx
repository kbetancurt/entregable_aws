// Campanita de alertas del panel de administración: lista, detalle y aviso emergente.
import React, { useEffect, useState } from "react";

const INFO = {
  PRECIO_MENOR_EN_PLATAFORMA: { titulo: "Precio más bajo", icono: "$", clase: "precio" },
  CAMBIO_PRECIO: { titulo: "Cambio de precio", icono: "±", clase: "cambio" },
  AMENITIES_DISTINTAS: { titulo: "Amenities faltantes", icono: "≠", clase: "amenities" },
  CAPACIDAD_AGOTADA: { titulo: "Capacidad agotada", icono: "!", clase: "agotada" },
  HOTEL_COMPLETO: { titulo: "Hotel completo", icono: "!", clase: "agotada" },
};
const info = a => INFO[a.tipo] || { titulo: String(a.tipo || "Aviso").replaceAll("_", " ").toLowerCase(), icono: "•", clase: "cambio" };

const usd = n => new Intl.NumberFormat("es-UY", { style: "currency", currency: "USD", minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(n);
const dia = d => new Intl.DateTimeFormat("es-UY", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(new Date(d + "T00:00:00Z"));
const hora = iso => iso ? new Intl.DateTimeFormat("es-UY", { dateStyle: "short", timeStyle: "short" }).format(new Date(iso)) : "";
const horaCorta = iso => iso ? new Intl.DateTimeFormat("es-UY", { hour: "2-digit", minute: "2-digit" }).format(new Date(iso)) : "";
const pct = n => `${n > 0 ? "+" : ""}${n}%`;
const noches = a => (a.noches || (a.noche ? [a.noche] : [])).map(dia).join(", ");

// El id de una alerta guardada es su clave en DynamoDB; los avisos en vivo traen los mismos
// datos, así que el mismo id evita mostrar dos veces la misma alerta.
export const idAlerta = e => `ALERTA#${e.ocurridoEn}#${e.eventId}`;
export const alertaDesdeEvento = e => ({ ...e, id: e.id || idAlerta(e), leida: false });

export function fusionarAlertas(nuevas, actuales) {
  const porId = new Map(actuales.map(a => [a.id, a]));
  for (const a of nuevas) porId.set(a.id, { ...a, leida: a.leida || porId.get(a.id)?.leida || false });
  return [...porId.values()].sort((x, y) => String(y.ocurridoEn).localeCompare(String(x.ocurridoEn))).slice(0, 100);
}

// Una línea para la lista: lo justo para decidir si vale la pena abrirla.
function resumen(a) {
  switch (a.tipo) {
    case "PRECIO_MENOR_EN_PLATAFORMA":
      return `${a.plataforma} · ${a.tipoHabitacion || "habitación base"} · ${usd(a.precioPlataforma)} (${pct(a.diferenciaPct)})`;
    case "CAMBIO_PRECIO":
      return `${a.plataforma} · ${usd(a.precioAnterior)} → ${usd(a.precioNuevo)} (${pct(a.variacionPct)})`;
    case "AMENITIES_DISTINTAS": {
      const n = (a.faltantes || []).length;
      return `${a.plataforma}${n ? ` · faltan ${n}` : ""}`;
    }
    case "CAPACIDAD_AGOTADA":
      return `${a.tipoHabitacion} · ${noches(a)}`;
    case "HOTEL_COMPLETO":
      return noches(a);
    default:
      return a.mensaje || "";
  }
}

function filas(a) {
  switch (a.tipo) {
    case "PRECIO_MENOR_EN_PLATAFORMA":
      return [["Plataforma", a.plataforma], ["Habitación", a.tipoHabitacion || "Habitación base"], ["Noche", dia(a.noche)],
        ["Precio en la plataforma", usd(a.precioPlataforma)], ["Precio en la web del hotel", usd(a.precioPropio)],
        ["Diferencia", pct(a.diferenciaPct)]];
    case "CAMBIO_PRECIO":
      return [["Plataforma", a.plataforma], ["Habitación", a.tipoHabitacion || "Habitación base"], ["Noche", dia(a.noche)],
        ["Precio anterior", usd(a.precioAnterior)], ["Precio nuevo", usd(a.precioNuevo)], ["Variación", pct(a.variacionPct)]];
    case "AMENITIES_DISTINTAS":
      return [["Plataforma", a.plataforma]];
    case "CAPACIDAD_AGOTADA":
      return [["Habitación", a.tipoHabitacion], ["Noches", noches(a)]];
    case "HOTEL_COMPLETO":
      return [["Noches", noches(a)]];
    default:
      return [];
  }
}

const Campana = () => <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 8 3 8H3s3-1 3-8"/><path d="M10.3 21a1.9 1.9 0 0 0 3.4 0"/></svg>;

export function Campanita({ alertas, estado, onAbrir, onLeerTodas, onVerTodas }) {
  const [abierta, setAbierta] = useState(false);
  const noLeidas = alertas.filter(a => !a.leida).length;
  useEffect(() => {
    if (!abierta) return;
    const esc = e => e.key === "Escape" && setAbierta(false);
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [abierta]);

  return <div className="campana">
    <button className="campana-btn" onClick={() => setAbierta(v => !v)} aria-label={`Alertas, ${noLeidas} sin leer`} aria-expanded={abierta}>
      <Campana/>{noLeidas > 0 && <span className="campana-badge">{noLeidas > 9 ? "9+" : noLeidas}</span>}
    </button>
    {abierta && <>
      <div className="campana-fondo" onClick={() => setAbierta(false)}/>
      <section className="campana-lista" aria-label="Alertas">
        <div className="campana-cabecera">
          <strong>Alertas</strong>
          <span className={"ws " + estado}>{{ conectado: "● En vivo", conectando: "Conectando…", reconectando: "Reconectando…" }[estado] || ""}</span>
          {noLeidas > 0 && <button onClick={onLeerTodas}>Marcar todas como leídas</button>}
        </div>
        {alertas.length === 0
          ? <p className="empty">Sin alertas todavía. Van a aparecer acá cuando ocurran.</p>
          : alertas.slice(0, 8).map(a => <button key={a.id} className={"alerta " + info(a).clase + (a.leida ? "" : " nueva")}
              onClick={() => { setAbierta(false); onAbrir(a); }}>
              <i>{info(a).icono}</i>
              <span><strong>{info(a).titulo}</strong><small>{resumen(a)}</small></span>
              <time>{hora(a.ocurridoEn)}</time>
            </button>)}
        {onVerTodas && <button className="campana-todas" onClick={() => { setAbierta(false); onVerTodas(); }}>Ver todas las alertas</button>}
      </section>
    </>}
  </div>;
}

export function DetalleAlerta({ alerta, onCerrar }) {
  useEffect(() => {
    const esc = e => e.key === "Escape" && onCerrar();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onCerrar]);
  const { titulo, clase, icono } = info(alerta);
  const detalle = filas(alerta);
  const lista = alerta.tipo === "AMENITIES_DISTINTAS" ? alerta.faltantes || [] : [];
  return <div className="veil" onMouseDown={e => e.target === e.currentTarget && onCerrar()}>
    <section className={"modal detalle " + clase} role="dialog" aria-modal="true" aria-label={titulo}>
      <button className="close" onClick={onCerrar} aria-label="Cerrar">×</button>
      <p className="eyebrow"><i className="detalle-icono">{icono}</i> ALERTA · {hora(alerta.ocurridoEn)}</p>
      <h2>{titulo}</h2>
      {alerta.mensaje && <p>{alerta.mensaje}</p>}
      {detalle.length > 0 && <dl>{detalle.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>}
      {alerta.tipo === "AMENITIES_DISTINTAS" && <>
        <p className="detalle-sub">Amenities del hotel que {alerta.plataforma} no muestra ({lista.length})</p>
        {lista.length === 0 ? <p className="empty">Sin detalle en esta alerta.</p>
          : <ul className="chips">{lista.map(x => <li key={x}>{x}</li>)}</ul>}
      </>}
    </section>
  </div>;
}

export function ToastAlerta({ alerta, onAbrir, onCerrar }) {
  useEffect(() => {
    const t = setTimeout(onCerrar, 7000);
    return () => clearTimeout(t);
  }, [alerta.id, onCerrar]);
  const { titulo, icono, clase } = info(alerta);
  return <div className={"toast " + clase} role="status">
    <button className="toast-cuerpo" onClick={onAbrir}><i>{icono}</i><span><strong>{titulo}</strong><small>{resumen(alerta)}</small></span></button>
    <button className="toast-cerrar" onClick={onCerrar} aria-label="Cerrar aviso">×</button>
  </div>;
}

// ---------------------------------------------------------------------------
// Historial: pantalla propia con filtros y alertas agrupadas por día.
// ---------------------------------------------------------------------------
const FILTROS = [
  ["todas", "Todas"],
  ["precio", "Precio más bajo"],
  ["cambio", "Cambio de precio"],
  ["amenities", "Amenities"],
  ["agotada", "Disponibilidad"],
];

const claveDia = iso => iso ? new Intl.DateTimeFormat("sv-SE").format(new Date(iso)) : "";   // aaaa-mm-dd local

function etiquetaDia(clave) {
  const hoy = new Date();
  const ayer = new Date(hoy); ayer.setDate(hoy.getDate() - 1);
  const f = d => new Intl.DateTimeFormat("sv-SE").format(d);
  if (clave === f(hoy)) return "Hoy";
  if (clave === f(ayer)) return "Ayer";
  const [y, m, d] = clave.split("-").map(Number);
  const texto = new Intl.DateTimeFormat("es-UY", { weekday: "long", day: "numeric", month: "long" }).format(new Date(y, m - 1, d));
  return texto.charAt(0).toUpperCase() + texto.slice(1);
}

export function HistorialAlertas({ alertas, estado, cargando, fallo, onAbrir, onLeerTodas, onRecargar }) {
  const [filtro, setFiltro] = useState("todas");
  const [soloNuevas, setSoloNuevas] = useState(false);
  const noLeidas = alertas.filter(a => !a.leida).length;
  const cuentas = { todas: alertas.length };
  for (const a of alertas) cuentas[info(a).clase] = (cuentas[info(a).clase] || 0) + 1;

  const visibles = alertas.filter(a => (filtro === "todas" || info(a).clase === filtro) && (!soloNuevas || !a.leida));
  const dias = [];
  for (const a of visibles) {
    const clave = claveDia(a.ocurridoEn);
    const ultimo = dias[dias.length - 1];
    if (ultimo && ultimo.clave === clave) ultimo.items.push(a);
    else dias.push({ clave, items: [a] });
  }

  return <section className="historial" aria-labelledby="historial-titulo">
    <div className="historial-cabecera">
      <div>
        <h1 id="historial-titulo">Alertas</h1>
        <p>Cambios de precio y diferencias que detectamos en otras plataformas.
          {" "}<span className={"ws " + estado}>{{ conectado: "● En vivo", conectando: "Conectando…", reconectando: "Reconectando…" }[estado] || ""}</span></p>
      </div>
      <div className="historial-acciones">
        <button className="secundario" onClick={onRecargar} disabled={cargando}>{cargando ? "Actualizando…" : "Actualizar"}</button>
        <button className="secundario" onClick={onLeerTodas} disabled={noLeidas === 0}>Marcar todas como leídas</button>
      </div>
    </div>

    <div className="filtros" role="toolbar" aria-label="Filtrar alertas">
      {FILTROS.map(([id, nombre]) => <button key={id} className={"filtro" + (filtro === id ? " activo" : "")}
        aria-pressed={filtro === id} onClick={() => setFiltro(id)}>{nombre}<b>{cuentas[id] || 0}</b></button>)}
      <label className="solo-nuevas"><input type="checkbox" checked={soloNuevas} onChange={e => setSoloNuevas(e.target.checked)}/> Solo sin leer ({noLeidas})</label>
    </div>

    {fallo && <p className="error">No pudimos actualizar las alertas. Mostramos las que ya teníamos.</p>}

    {cargando && alertas.length === 0
      ? <div className="historial-lista">{[0, 1, 2, 3].map(i => <div key={i} className="alerta-esqueleto"/>)}</div>
      : visibles.length === 0
        ? <div className="historial-vacio">
            <strong>{alertas.length === 0 ? "Todavía no hay alertas" : "Ninguna alerta coincide con el filtro"}</strong>
            <p>{alertas.length === 0
              ? "Cuando el scraper encuentre un precio más bajo o un cambio importante, aparece acá."
              : "Probá con otro tipo de alerta o quitá el filtro de sin leer."}</p>
            {alertas.length > 0 && <button className="secundario" onClick={() => { setFiltro("todas"); setSoloNuevas(false); }}>Quitar filtros</button>}
          </div>
        : dias.map(d => <div key={d.clave} className="historial-dia">
            <h2>{etiquetaDia(d.clave)}<small>{d.items.length}</small></h2>
            <div className="historial-lista">
              {d.items.map(a => <button key={a.id} className={"alerta " + info(a).clase + (a.leida ? "" : " nueva")} onClick={() => onAbrir(a)}>
                <i>{info(a).icono}</i>
                <span><strong>{info(a).titulo}</strong><small>{resumen(a)}</small></span>
                <time>{horaCorta(a.ocurridoEn)}</time>
              </button>)}
            </div>
          </div>)}
    {alertas.length >= 100 && <p className="historial-nota">Mostramos las últimas 100 alertas.</p>}
  </section>;
}
