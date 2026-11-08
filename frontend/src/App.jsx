import React, { useCallback, useEffect, useRef, useState } from "react";
import { configured } from "./config.js";
import { buscarHabitaciones, crearReserva } from "./api.js";
import { cerrarSesion, confirmar, login, reenviarCodigo, registrar, sesionGuardada, sesionVigente } from "./auth.js";
import { conectarAvisos } from "./avisos.js";
import "./conectado.css";

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
const nuevaClave = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now())+Math.random());

const EVENTOS = {
  CAPACIDAD_AGOTADA: e => `Se agotó ${e.tipoHabitacion} para ${(e.noches||[]).map(dateLabel).join(", ")}.`,
  HOTEL_COMPLETO: e => `Hotel completo para ${(e.noches||[]).map(dateLabel).join(", ")}.`,
  RESERVA_CREADA: e => `Nueva reserva ${e.reservaId||""}.`,
  PRECIO_MENOR_EN_PLATAFORMA: e => e.mensaje || `${e.plataforma||"Una plataforma"} muestra un precio menor que la web del hotel.`,
  CAMBIO_PRECIO: e => e.mensaje || `Cambió el precio en ${e.plataforma||"una plataforma"}.`,
  AMENITIES_DISTINTAS: e => e.mensaje || `Las amenities de ${e.plataforma||"una plataforma"} no coinciden.`,
};
const describir = e => (EVENTOS[e.tipo] || (x => x.mensaje || x.tipo))(e);
const hora = iso => iso ? new Intl.DateTimeFormat("es-UY",{dateStyle:"short",timeStyle:"short"}).format(new Date(iso)) : "";

export default function App() {
  const [defaults] = useState(defaultDates);
  const [checkIn,setCheckIn] = useState(defaults[0]);
  const [checkOut,setCheckOut] = useState(defaults[1]);
  const [guests,setGuests] = useState("2");
  const [searchedFor,setSearchedFor] = useState(null);   // {entrada, salida, huespedes, noches}
  const [combos,setCombos] = useState([]);
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

  // Panel admin: avisos en tiempo real por WebSocket.
  useEffect(() => {
    if (!isAdmin || !configured) return;
    return conectarAvisos(async () => (await freshSession())?.accessToken, {
      onEvento: e => setAlerts(a => [e, ...a].slice(0, 30)),
      onEstado: setWsState,
    });
  }, [isAdmin, session?.email, freshSession]);

  async function runSearch(entrada, salida, huespedes) {
    setLoading(true);
    try {
      const data = await buscarHabitaciones({ entrada, salida, huespedes });
      setCombos(data.combinaciones || []);
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

  function logout() { cerrarSesion(); setSession(null); setAlerts([]); setWsState(""); setNotice("Cerraste sesión."); }

  const nights = searchedFor?.noches ?? nightsBetween(checkIn, checkOut);

  return <div className="page">
    <div className="demo"><i/> {configured ? "Conectado a AWS · API Gateway, Lambda, DynamoDB y Cognito" : "Falta configurar la API (VITE_API_URL y VITE_COGNITO_CLIENT_ID)"}</div>
    <header><a className="brand" href="#inicio"><b>A</b><span><strong>aloft</strong><small>MONTEVIDEO</small></span></a><nav><a href="#habitaciones">Habitaciones</a><a href="#hotel">El hotel</a>{isAdmin&&<a href="#panel">Panel</a>}</nav>
      {session ? <button className="dark" onClick={logout} title={session.email}>{isAdmin?"Admin · ":""}{session.email} · Salir</button> : <button className="dark" onClick={()=>openAuth("login")}>Iniciar sesión ↗</button>}
    </header>
    <main id="inicio">
      {isAdmin&&<section className="bookings alerts" id="panel"><div className="section-head"><div><p className="eyebrow">PANEL DE ADMINISTRACIÓN</p><h2>Avisos en tiempo real</h2></div><span className={"ws "+wsState}>{{conectado:"● En vivo",conectando:"Conectando…",reconectando:"Reconectando…"}[wsState]||""}</span></div>
        {alerts.length===0?<p className="empty">Sin avisos todavía. Aparecen acá en cuanto ocurren (capacidad agotada, hotel completo, precios en otras plataformas).</p>
        :alerts.map(a=><article key={a.eventId}><div><strong>{a.tipo.replaceAll("_"," ")}</strong><small>{describir(a)}</small></div><small>{hora(a.ocurridoEn)}</small></article>)}
      </section>}
      <section className="hero"><div><p className="eyebrow">✳ PUNTA CARRETAS · MONTEVIDEO</p><h1>Montevideo te espera,<br/><em>quedate a disfrutar.</em></h1><p className="intro">Descubrí Punta Carretas desde un hotel moderno, con habitaciones cómodas y algunas opciones con vista al río.</p></div><div className="photo"><span>ALOFT MONTEVIDEO</span><strong>Un lugar para conectar.</strong></div></section>
      <section className="search"><form onSubmit={search}><label>Llegada<input type="date" min={today} value={checkIn} onChange={e=>setCheckIn(e.target.value)} required/></label><label>Salida<input type="date" min={checkIn} value={checkOut} onChange={e=>setCheckOut(e.target.value)} required/></label><label>Huéspedes<select value={guests} onChange={e=>setGuests(e.target.value)}>{[1,2,3,4,5,6,7,8].map(n=><option key={n} value={n}>{n} huésped{n>1?"es":""}</option>)}</select></label><button className="primary" disabled={loading||!configured}>{loading?"Buscando…":"Buscar habitaciones →"}</button></form>{notice&&<p className="notice" role="status">{notice}</p>}</section>
      <section className="rooms" id="habitaciones"><div className="section-head"><div><p className="eyebrow">DESCANSÁ COMO TE GUSTA</p><h2>Elegí tu habitación</h2><p>{searchedFor?dateLabel(searchedFor.entrada)+" — "+dateLabel(searchedFor.salida)+" · "+nights+" noches · "+searchedFor.huespedes+" huésped"+(searchedFor.huespedes>1?"es":""):"Elegí fechas y huéspedes para ver la disponibilidad real."}</p></div></div>
        <div className="grid">{searchedFor
          ? (combos.length===0
              ? <p className="empty">No hay habitaciones disponibles para esas fechas y huéspedes. Probá con otras fechas.</p>
              : combos.map((c,i)=><article className="card" key={comboKey(c)}><div className={"room art"+(i%4)}><b>✳</b><small>{c.habitaciones.map(h=>h.tipo_id).join(" + ")}</small></div><div className="card-content"><h3>{comboTitle(c)}</h3><p>{c.cantidad_habitaciones} habitación{c.cantidad_habitaciones>1?"es":""} · capacidad para {c.capacidad_total}</p><small>{c.habitaciones.map(h=>`${h.nombre}: ${price(h.precio_por_noche)} / noche`).join(" · ")}</small><div className="bottom"><div><strong>{price(c.precio_total/c.noches)}</strong><small> / noche</small><em>{price(c.precio_total)} total</em></div><button onClick={()=>choose(c)}>Reservar →</button></div><small className="available">Disponible para toda la estadía</small></div></article>))
          : types.map((room,i)=><article className="card" key={room.id}><div className={"room art"+i}><b>✳</b><small>{room.id}</small></div><div className="card-content"><h3>{room.name}</h3><p>{room.description}</p><small>Capacidad: {room.guests} · Wi-Fi · Aire acondicionado</small><div className="bottom"><div><strong>{price(room.price)}</strong><small> / noche</small></div><button onClick={()=>document.querySelector(".search")?.scrollIntoView({behavior:"smooth"})}>Ver fechas ↑</button></div></div></article>)}
        </div></section>
      <section className="hotel" id="hotel"><b>✳</b><div><p className="eyebrow">ALOFT MONTEVIDEO HOTEL</p><h2>En el corazón de Punta Carretas</h2><p>Victor Soliño 350 · Piscina interior · Gimnasio Re:Charge · Restaurante Nook · Bar W XYZ</p></div></section>
      {myBookings.length>0&&<section className="bookings"><div className="section-head"><h2>Tus reservas</h2></div>{myBookings.map(b=><article key={b.reserva_id}><div><strong>{b.titulo}</strong><small>{dateLabel(b.fecha_inicio)} — {dateLabel(b.fecha_fin)} · {b.reserva_id} · {b.estado}</small></div><b>{price(b.precio_total)}</b></article>)}</section>}
    </main><footer><a className="brand" href="#inicio"><b>A</b><span><strong>aloft</strong><small>MONTEVIDEO</small></span></a><span>Reservas hoteleras serverless · Montevideo, Uruguay</span></footer>

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
