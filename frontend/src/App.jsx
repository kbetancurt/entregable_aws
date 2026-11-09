import React, { useCallback, useEffect, useRef, useState } from "react";
import { configured } from "./config.js";
import { buscarHabitaciones, crearReserva, listarAlertas, marcarAlertasLeidas } from "./api.js";
import { cerrarSesion, confirmar, login, reenviarCodigo, registrar, sesionGuardada, sesionVigente } from "./auth.js";
import { conectarAvisos } from "./avisos.js";
import { FOTOS_HOTEL, fotoHabitacion, fotosHabitacion } from "./imagenes.js";
import { Campanita, DetalleAlerta, HistorialAlertas, ToastAlerta, alertaDesdeEvento, fusionarAlertas } from "./alertas.jsx";
import "./styles.css";
import "./conectado.css";
import "./mejoras.css";

// Vitrina inicial; la disponibilidad real sale de GET /habitaciones.
const types = [
  { id: "KING-RIVER", name: "Habitación King con vista al río", description: "Una cama King, Wi-Fi y vistas al Río de la Plata.", guests: 2, price: 165 },
  { id: "QUEEN-QUEEN-CITY", name: "Habitación con dos camas Queen", description: "Dos camas Queen para una estadía cómoda en Punta Carretas.", guests: 4, price: 185 },
  { id: "KING-ALOFT", name: "Habitación King Aloft", description: "Una cama King, minibar y espacio de trabajo ergonómico.", guests: 2, price: 145 },
  { id: "TWIN-TWIN", name: "Habitación Twin Twin", description: "Dos camas individuales, ideal para viajar acompañado.", guests: 2, price: 145 },
];
const dateString = d => [d.getFullYear(),String(d.getMonth()+1).padStart(2,"0"),String(d.getDate()).padStart(2,"0")].join("-");
const defaultDates = () => { const a=new Date(); a.setDate(a.getDate()+1); const b=new Date(a); b.setDate(b.getDate()+2); return [dateString(a),dateString(b)]; };
const nightsBetween = (a,b) => Math.round((new Date(b+"T00:00:00Z")-new Date(a+"T00:00:00Z"))/86400000);
const dateLabel = d => new Intl.DateTimeFormat("es-UY",{day:"numeric",month:"short",year:"numeric",timeZone:"UTC"}).format(new Date(d+"T00:00:00Z"));
const price = n => new Intl.NumberFormat("es-UY",{style:"currency",currency:"USD",maximumFractionDigits:0}).format(n);
const comboTitle = c => c.habitaciones.map(h => (h.cantidad>1?h.cantidad+" × ":"")+h.nombre).join(" + ");
const comboKey = c => c.habitaciones.map(h => h.tipo_id+":"+h.cantidad).join("|");
const vistaDesdeHash = () => (window.location.hash === "#/alertas" ? "alertas" : "inicio");
const nuevaClave = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now())+Math.random());

// Foto con respaldo: si el archivo no existe se ve el degradado de siempre (clase "sin-foto").
function Foto({ src, alt, className, children }) {
  const [fallo, setFallo] = useState(false);
  useEffect(() => setFallo(false), [src]);
  return <div className={className + (fallo ? " sin-foto" : "")}>
    {!fallo && <img src={src} alt={alt} loading="lazy" onError={() => setFallo(true)} />}
    {children}
  </div>;
}

// Fotos de una opción. Un solo tipo: galería con las fotos que existan (-1 a -4), con flechas y puntos.
// Varios tipos: la foto principal de cada uno, lado a lado. Si no hay ninguna se ve el degradado.
function FotosHabitaciones({ habitaciones, className }) {
  const unicas = [...new Map(habitaciones.map(h => [h.tipo_id, h])).values()].slice(0, 2);
  const [fallos, setFallos] = useState({});
  const [actual, setActual] = useState(0);
  const falla = src => setFallos(f => (f[src] ? f : { ...f, [src]: true }));
  const rotulo = <><b>✳</b><small>{habitaciones.map(h => h.tipo_id).join(" + ")}</small></>;

  if (unicas.length > 1) {
    const visibles = unicas.filter(h => !fallos[fotoHabitacion(h.tipo_id)]);
    return <div className={className + (visibles.length === 0 ? " sin-foto" : "") + (visibles.length > 1 ? " mosaico" : "")}>
      {visibles.map(h => <img key={h.tipo_id} src={fotoHabitacion(h.tipo_id)} alt={h.nombre} loading="lazy" onError={() => falla(fotoHabitacion(h.tipo_id))}/>)}
      {rotulo}
    </div>;
  }

  const tipo = unicas[0];
  const fotos = fotosHabitacion(tipo.tipo_id).filter(src => !fallos[src]);
  const n = fotos.length;
  const i = Math.min(actual, Math.max(n - 1, 0));
  const ir = (e, paso) => { e.stopPropagation(); setActual((i + paso + n) % n); };
  return <div className={className + (n === 0 ? " sin-foto" : "") + (n > 1 ? " galeria" : "")}>
    {fotosHabitacion(tipo.tipo_id).map(src => !fallos[src] && <img key={src} src={src} alt={tipo.nombre}
      className={fotos[i] === src ? "visible" : ""} loading="lazy" onError={() => falla(src)}/>)}
    {n > 1 && <>
      <button type="button" className="gal-btn ant" onClick={e => ir(e, -1)} aria-label="Foto anterior">‹</button>
      <button type="button" className="gal-btn sig" onClick={e => ir(e, 1)} aria-label="Foto siguiente">›</button>
      <span className="gal-puntos">{fotos.map((src, k) => <i key={src} className={k === i ? "on" : ""}/>)}</span>
    </>}
    {rotulo}
  </div>;
}


export default function App() {
  const [defaults] = useState(defaultDates);
  const [checkIn,setCheckIn] = useState(defaults[0]);
  const [checkOut,setCheckOut] = useState(defaults[1]);
  const [guests,setGuests] = useState("2");
  const [searchedFor,setSearchedFor] = useState(null);   // {entrada, salida, huespedes, noches}
  const [combos,setCombos] = useState([]);
  const [totalOpciones,setTotalOpciones] = useState(0);
  const [loading,setLoading] = useState(false);
  const [notice,setNotice] = useState("");
  const [selected,setSelected] = useState(null);         // {combo, key}
  const [error,setError] = useState("");
  const [sending,setSending] = useState(false);
  const [myBookings,setMyBookings] = useState([]);
  const [session,setSession] = useState(sesionGuardada);
  const [auth,setAuth] = useState(null);                 // "login" | "registro" | "confirmar"
  const [authError,setAuthError] = useState("");
  const [email,setEmail] = useState("");
  const [password,setPassword] = useState("");
  const [code,setCode] = useState("");
  const [alerts,setAlerts] = useState([]);
  const [wsState,setWsState] = useState("");
  const [detalle,setDetalle] = useState(null);
  const [toast,setToast] = useState(null);
  const [vista,setVista] = useState(vistaDesdeHash);
  const [cargandoAlertas,setCargandoAlertas] = useState(false);
  const [falloAlertas,setFalloAlertas] = useState(false);
  const pending = useRef(null);
  const sessionRef = useRef(session);
  sessionRef.current = session;
  const today = dateString(new Date());
  const isAdmin = session?.groups?.includes("admin");

  const freshSession = useCallback(async () => {
    const s = await sesionVigente(sessionRef.current);
    if (s !== sessionRef.current) setSession(s);
    return s;
  }, []);

  // Alertas del admin: las guardadas (REST) más las que llegan en vivo (WebSocket).
  const cargarAlertas = useCallback(async (limite = 50) => {
    setCargandoAlertas(true);
    try {
      const s = await freshSession();
      if (!s) return;
      const { alertas } = await listarAlertas(s.idToken, limite);
      setAlerts(actuales => fusionarAlertas(alertas || [], actuales));
      setFalloAlertas(false);
    } catch { setFalloAlertas(true); /* la campanita sigue con lo que ya tiene */ }
    finally { setCargandoAlertas(false); }
  }, [freshSession]);

  useEffect(() => {
    const alCambiar = () => { setVista(vistaDesdeHash()); window.scrollTo(0, 0); };
    window.addEventListener("hashchange", alCambiar);
    return () => window.removeEventListener("hashchange", alCambiar);
  }, []);

  // Al entrar al historial se pide la lista completa (hasta 100).
  useEffect(() => {
    if (vista === "alertas" && isAdmin && configured) cargarAlertas(100);
  }, [vista, isAdmin, cargarAlertas]);

  // Ir a una sección de la home desde cualquier pantalla.
  function irA(id) {
    const ir = () => (id === "inicio" ? window.scrollTo({ top: 0, behavior: "smooth" }) : document.getElementById(id)?.scrollIntoView({ behavior: "smooth" }));
    if (window.location.hash === "#/alertas") { window.location.hash = "#/"; setTimeout(ir, 80); } else ir();
  }

  useEffect(() => {
    if (!isAdmin || !configured) return;
    cargarAlertas();
    return conectarAvisos(async () => (await freshSession())?.accessToken, {
      onEvento: e => { const a = alertaDesdeEvento(e); setAlerts(x => fusionarAlertas([a], x)); setToast(a); },
      // Al (re)conectar se vuelve a pedir la lista: cubre lo ocurrido mientras no había conexión.
      onEstado: estado => { setWsState(estado); if (estado === "conectado") cargarAlertas(vista === "alertas" ? 100 : 50); },
    });
  }, [isAdmin, session?.email, freshSession, cargarAlertas]); // eslint-disable-line react-hooks/exhaustive-deps

  function abrirAlerta(a) {
    setDetalle(a); setToast(null);
    if (a.leida) return;
    setAlerts(x => x.map(y => y.id === a.id ? { ...y, leida: true } : y));
    freshSession().then(s => s && marcarAlertasLeidas({ ids: [a.id] }, s.idToken)).catch(() => {});
  }

  function leerTodas() {
    setAlerts(x => x.map(y => ({ ...y, leida: true })));
    freshSession().then(s => s && marcarAlertasLeidas({ todas: true }, s.idToken)).catch(() => {});
  }

  async function runSearch(entrada, salida, huespedes) {
    setLoading(true);
    try {
      const data = await buscarHabitaciones({ entrada, salida, huespedes });
      setCombos(data.combinaciones || []);
      setTotalOpciones(data.opciones_totales ?? (data.combinaciones || []).length);
      setSearchedFor({ entrada, salida, huespedes, noches: data.noches ?? nightsBetween(entrada, salida) });
      return true;
    } catch (e) {
      setNotice(e.message);
      return false;
    } finally {
      setLoading(false);
    }
  }

  async function search(e) {
    e.preventDefault(); setNotice("");
    if(checkIn<today || checkOut<=checkIn) { setNotice("Elegí fechas válidas. La salida debe ser posterior a la llegada."); return; }
    if(await runSearch(checkIn, checkOut, Number(guests))) document.getElementById("habitaciones")?.scrollIntoView({behavior:"smooth"});
  }

  function choose(combo) {
    setError("");
    const pick = { combo, key: nuevaClave() };
    if (!session) { pending.current = pick; openAuth("login"); return; }
    setSelected(pick);
  }

  async function book(e) {
    e.preventDefault(); setError(""); setSending(true);
    const { combo, key } = selected;
    const s = await freshSession();
    if (!s) { setSending(false); setSelected(null); pending.current = selected; openAuth("login"); return; }
    try {
      const { reserva } = await crearReserva({
        entrada: searchedFor.entrada, salida: searchedFor.salida, huespedes: searchedFor.huespedes,
        habitaciones: combo.habitaciones.map(h => ({ tipo_id: h.tipo_id, cantidad: h.cantidad })),
      }, s.idToken, key);
      setMyBookings(b => [{ ...reserva, titulo: comboTitle(combo) }, ...b.filter(x => x.reserva_id !== reserva.reserva_id)]);
      setSelected(null);
      setNotice(`Reserva ${reserva.reserva_id} confirmada.`);
      runSearch(searchedFor.entrada, searchedFor.salida, searchedFor.huespedes);
    } catch (err) {
      if (err.status === 409) {
        setError("Ya no hay disponibilidad para esas fechas. Elegí otra opción.");
        runSearch(searchedFor.entrada, searchedFor.salida, searchedFor.huespedes);
      } else setError(err.message);
    } finally {
      setSending(false);
    }
  }

  function openAuth(mode) { setAuth(mode); setAuthError(""); setPassword(""); setCode(""); }
  function closeAuth() { setAuth(null); pending.current = null; }

  async function submitAuth(e) {
    e.preventDefault(); setAuthError("");
    try {
      if (auth === "registro") { await registrar(email.trim(), password); setAuth("confirmar"); return; }
      if (auth === "confirmar") { await confirmar(email.trim(), code.trim()); }
      const s = await login(email.trim(), password);
      setSession(s); setAuth(null); setPassword(""); setCode("");
      setNotice(`Sesión iniciada como ${s.email}.`);
      if (pending.current) { setSelected(pending.current); pending.current = null; }
    } catch (err) {
      if (err.code === "UserNotConfirmedException") setAuth("confirmar");
      setAuthError(err.message);
    }
  }

  function logout() { cerrarSesion(); setSession(null); setAlerts([]); setDetalle(null); setToast(null); setWsState(""); setNotice("Cerraste sesión."); }

  const noLeidas = alerts.filter(a => !a.leida).length;
  const nights = searchedFor?.noches ?? nightsBetween(checkIn, checkOut);

  return <div className="page">
    <div className="demo"><i/> {configured ? "Conectado a AWS · API Gateway, Lambda, DynamoDB y Cognito" : "Falta configurar la API (VITE_API_URL y VITE_COGNITO_CLIENT_ID)"}</div>
    <header>
      <a className="brand" href="#/" onClick={e=>{e.preventDefault();irA("inicio")}}><b>A</b><span><strong>aloft</strong><small>MONTEVIDEO</small></span></a>
      <nav aria-label="Principal">
        <a href="#/" onClick={e=>{e.preventDefault();irA("habitaciones")}}>Habitaciones</a>
        <a href="#/" onClick={e=>{e.preventDefault();irA("hotel")}}>El hotel</a>
        {isAdmin&&<a href="#/alertas" className={vista==="alertas"?"activo":""} aria-current={vista==="alertas"?"page":undefined}>Alertas{noLeidas>0&&<b className="nav-badge">{noLeidas>9?"9+":noLeidas}</b>}</a>}
      </nav>
      <div className="acciones">
        {isAdmin&&<Campanita alertas={alerts} estado={wsState} onAbrir={abrirAlerta} onLeerTodas={leerTodas} onVerTodas={()=>{window.location.hash="#/alertas"}}/>}
        {session ? <button className="dark" onClick={logout} title={session.email}>{isAdmin?"Admin · ":""}{session.email} · Salir</button> : <button className="dark" onClick={()=>openAuth("login")}>Iniciar sesión</button>}
      </div>
    </header>
    {vista==="alertas"
      ? <main className="pantalla-alertas">{isAdmin
          ? <HistorialAlertas alertas={alerts} estado={wsState} cargando={cargandoAlertas} fallo={falloAlertas} onAbrir={abrirAlerta} onLeerTodas={leerTodas} onRecargar={()=>cargarAlertas(100)}/>
          : <section className="historial-vacio acceso"><strong>Esta pantalla es para administradores</strong><p>{session?"Tu cuenta no tiene permisos de administración.":"Iniciá sesión con una cuenta de administrador para ver las alertas."}</p>{!session&&<button className="primary" onClick={()=>openAuth("login")}>Iniciar sesión</button>}<a href="#/">Volver al inicio</a></section>}</main>
      : <main id="inicio">
      <section className="hero"><div><p className="eyebrow">✳ PUNTA CARRETAS · MONTEVIDEO</p><h1>Montevideo te espera,<br/><em>quedate a disfrutar.</em></h1><p className="intro">Descubrí Punta Carretas desde un hotel moderno, con habitaciones cómodas y algunas opciones con vista al río.</p><ul className="servicios"><li>Piscina interior</li><li>Gimnasio Re:Charge</li><li>Restaurante Nook</li><li>Bar W XYZ</li></ul></div><Foto className="photo" src={FOTOS_HOTEL.portada} alt="Aloft Montevideo"><span>ALOFT MONTEVIDEO</span><strong>Un lugar para conectar.</strong></Foto></section>
      <section className="search"><form onSubmit={search}><label>Llegada<input type="date" min={today} value={checkIn} onChange={e=>setCheckIn(e.target.value)} required/></label><label>Salida<input type="date" min={checkIn} value={checkOut} onChange={e=>setCheckOut(e.target.value)} required/></label><label>Huéspedes<select value={guests} onChange={e=>setGuests(e.target.value)}>{[1,2,3,4,5,6,7,8].map(n=><option key={n} value={n}>{n} huésped{n>1?"es":""}</option>)}</select></label><button className="primary" disabled={loading||!configured}>{loading?"Buscando…":"Buscar habitaciones →"}</button></form>{notice&&<p className="notice" role="status"><span>{notice}</span><button type="button" onClick={()=>setNotice("")} aria-label="Cerrar mensaje">×</button></p>}</section>
      <section className="rooms" id="habitaciones"><div className="section-head"><div><p className="eyebrow">DESCANSÁ COMO TE GUSTA</p><h2>Elegí tu habitación</h2><p>{searchedFor?dateLabel(searchedFor.entrada)+" — "+dateLabel(searchedFor.salida)+" · "+nights+" noches · "+searchedFor.huespedes+" huésped"+(searchedFor.huespedes>1?"es":""):"Elegí fechas y huéspedes para ver la disponibilidad real."}</p></div></div>
        <div className="grid" aria-busy={loading}>{loading
          ? [0,1,2].map(i=><article className="card esqueleto" key={i} aria-hidden="true"><div className="room"/><div className="card-content"><i/><i/><i/></div></article>)
          : searchedFor
          ? (combos.length===0
              ? <p className="empty">No hay habitaciones disponibles para esas fechas y huéspedes. Probá con otras fechas.</p>
              : <>{totalOpciones>combos.length&&<p className="empty">Mostramos las {combos.length} mejores opciones de {totalOpciones}: primero las que usan menos habitaciones y cuestan menos.</p>}{combos.map((c,i)=><article className="card" key={comboKey(c)}>{i===0&&combos.length>1&&<span className="insignia">Recomendada</span>}<FotosHabitaciones className={"room art"+(i%4)} habitaciones={c.habitaciones}/><div className="card-content"><h3>{comboTitle(c)}</h3><p>{c.cantidad_habitaciones} {c.cantidad_habitaciones>1?"habitaciones":"habitación"} · capacidad para {c.capacidad_total}</p><small>{c.habitaciones.map(h=>`${h.nombre}: ${price(h.precio_por_noche)} / noche`).join(" · ")}</small><div className="bottom"><div><strong>{price(c.precio_total/c.noches)}</strong><small> / noche</small><em>{price(c.precio_total)} total</em></div><button onClick={()=>choose(c)}>Reservar →</button></div><small className="available">Disponible para toda la estadía</small></div></article>)}</>)
          : types.map((room,i)=><article className="card" key={room.id}><FotosHabitaciones className={"room art"+i} habitaciones={[{tipo_id:room.id,nombre:room.name}]}/><div className="card-content"><h3>{room.name}</h3><p>{room.description}</p><small>Capacidad: {room.guests} · Wi-Fi · Aire acondicionado</small><div className="bottom"><div><strong>{price(room.price)}</strong><small> / noche</small></div><button onClick={()=>document.querySelector(".search")?.scrollIntoView({behavior:"smooth"})}>Ver fechas ↑</button></div></div></article>)}
        </div></section>
      <section className="hotel" id="hotel"><b>✳</b><div><p className="eyebrow">ALOFT MONTEVIDEO HOTEL</p><h2>En el corazón de Punta Carretas</h2><p>Victor Soliño 350 · Piscina interior · Gimnasio Re:Charge · Restaurante Nook · Bar W XYZ</p></div><Foto className="hotel-foto" src={FOTOS_HOTEL.exterior} alt="Exterior de Aloft Montevideo"/></section>
      {myBookings.length>0&&<section className="bookings"><div className="section-head"><h2>Tus reservas</h2></div>{myBookings.map(b=><article key={b.reserva_id}><div><strong>{b.titulo}</strong><small>{dateLabel(b.fecha_inicio)} — {dateLabel(b.fecha_fin)} · {b.reserva_id} · {b.estado}</small></div><b>{price(b.precio_total)}</b></article>)}</section>}
    </main>}
    <footer><a className="brand" href="#/" onClick={e=>{e.preventDefault();irA("inicio")}}><b>A</b><span><strong>aloft</strong><small>MONTEVIDEO</small></span></a><span>Reservas hoteleras serverless · Montevideo, Uruguay</span></footer>

    {detalle&&<DetalleAlerta alerta={detalle} onCerrar={()=>setDetalle(null)}/>}
    {toast&&<ToastAlerta alerta={toast} onAbrir={()=>abrirAlerta(toast)} onCerrar={()=>setToast(null)}/>}

    {selected&&<div className="veil" onMouseDown={e=>e.target===e.currentTarget&&!sending&&setSelected(null)}><section className="modal" role="dialog" aria-modal="true"><button className="close" onClick={()=>setSelected(null)} disabled={sending}>×</button><p className="eyebrow">CONFIRMAR ESTADÍA</p><h2>{comboTitle(selected.combo)}</h2><p>{dateLabel(searchedFor.entrada)} — {dateLabel(searchedFor.salida)} · {nights} noches · {searchedFor.huespedes} huésped{searchedFor.huespedes>1?"es":""}</p><div className="total"><span>Total</span><strong>{price(selected.combo.precio_total)}</strong></div><p className="local">Reservás como {session?.email}. La reserva queda confirmada al instante.</p><form onSubmit={book}>{error&&<p className="error">{error}</p>}<button className="primary" disabled={sending}>{sending?"Confirmando…":"Confirmar reserva →"}</button></form></section></div>}

    {auth&&<div className="veil" onMouseDown={e=>e.target===e.currentTarget&&closeAuth()}><section className="modal" role="dialog" aria-modal="true"><button className="close" onClick={closeAuth}>×</button>
      <p className="eyebrow">{auth==="registro"?"CREAR CUENTA":auth==="confirmar"?"CONFIRMAR CORREO":"INICIAR SESIÓN"}</p>
      <h2>{auth==="confirmar"?"Revisá tu correo":"Bienvenido"}</h2>
      {auth==="confirmar"&&<p>Te enviamos un código a {email}.</p>}
      <form onSubmit={submitAuth}>
        <label>Correo electrónico<input type="email" value={email} onChange={e=>setEmail(e.target.value)} required autoComplete="email"/></label>
        {auth==="confirmar"&&<label>Código<input value={code} onChange={e=>setCode(e.target.value)} required inputMode="numeric" autoComplete="one-time-code"/></label>}
        <label>Contraseña<input type="password" value={password} onChange={e=>setPassword(e.target.value)} required minLength={8} autoComplete={auth==="registro"?"new-password":"current-password"}/></label>
        {authError&&<p className="error">{authError}</p>}
        <button className="primary">{auth==="registro"?"Crear cuenta →":auth==="confirmar"?"Confirmar e ingresar →":"Ingresar →"}</button>
      </form>
      <p className="switch">{auth==="login"&&<>¿No tenés cuenta? <button onClick={()=>openAuth("registro")}>Registrate</button></>}
        {auth==="registro"&&<>¿Ya tenés cuenta? <button onClick={()=>openAuth("login")}>Iniciá sesión</button></>}
        {auth==="confirmar"&&<button onClick={()=>reenviarCodigo(email.trim()).then(()=>setAuthError("Te reenviamos el código."),err=>setAuthError(err.message))}>Reenviar código</button>}</p>
    </section></div>}
  </div>;
}
