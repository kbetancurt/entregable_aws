import React, { useState } from "react";

const types = [
  { id: "KING-RIVER", name: "Habitación King con vista al río", description: "Una cama King, Wi-Fi y vistas al Río de la Plata.", guests: 2, price: 165, stock: 4 },
  { id: "QUEEN-QUEEN", name: "Habitación con dos camas Queen", description: "Dos camas Queen para una estadía cómoda en Punta Carretas.", guests: 4, price: 185, stock: 3 },
  { id: "KING-ALOFT", name: "Habitación King Aloft", description: "Una cama King, minibar y espacio de trabajo ergonómico.", guests: 2, price: 145, stock: 5 },
  { id: "TWIN-TWIN", name: "Habitación Twin Twin", description: "Dos camas individuales, ideal para viajar acompañado.", guests: 2, price: 145, stock: 3 },
];
const KEY = "aloft-demo-reservations-v1";
const stored = () => { try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch { return []; } };
const dateString = d => [d.getFullYear(),String(d.getMonth()+1).padStart(2,"0"),String(d.getDate()).padStart(2,"0")].join("-");
const defaultDates = () => { const a=new Date(); a.setDate(a.getDate()+1); const b=new Date(a); b.setDate(b.getDate()+2); return [dateString(a),dateString(b)]; };
const nightsBetween = (a,b) => Math.round((new Date(b+"T00:00:00Z")-new Date(a+"T00:00:00Z"))/86400000);
const dateLabel = d => new Intl.DateTimeFormat("es-UY",{day:"numeric",month:"short",year:"numeric",timeZone:"UTC"}).format(new Date(d+"T00:00:00Z"));
const price = n => new Intl.NumberFormat("es-UY",{style:"currency",currency:"USD",maximumFractionDigits:0}).format(n);

export default function App() {
  const [defaults] = useState(defaultDates);
  const [checkIn,setCheckIn] = useState(defaults[0]);
  const [checkOut,setCheckOut] = useState(defaults[1]);
  const [guests,setGuests] = useState("2");
  const [bookings,setBookings] = useState(stored);
  const [searched,setSearched] = useState(false);
  const [notice,setNotice] = useState("");
  const [selected,setSelected] = useState(null);
  const [name,setName] = useState("");
  const [email,setEmail] = useState("");
  const [error,setError] = useState("");
  const today = dateString(new Date());
  const nights = nightsBetween(checkIn,checkOut);

  const availability = types.map(room => {
    let left = room.stock;
    for(let i=0;i<nights;i++){
      const d = new Date(checkIn+"T00:00:00Z"); d.setUTCDate(d.getUTCDate()+i);
      const day = d.toISOString().slice(0,10);
      const occupied = bookings.filter(b => b.type===room.id && b.checkIn<=day && day<b.checkOut).length;
      left = Math.min(left,room.stock-occupied);
    }
    return {...room,left:Math.max(0,left)};
  });

  function search(e) {
    e.preventDefault(); setNotice("");
    if(checkIn<today || checkOut<=checkIn) { setNotice("Elegí fechas válidas. La salida debe ser posterior a la llegada."); return; }
    if(Number(guests)>4) { setNotice("La demo permite hasta 4 huéspedes por habitación."); return; }
    setSearched(true);
  }
  function book(e) {
    e.preventDefault(); setError("");
    const room=availability.find(r=>r.id===selected.id);
    if(!room || room.left<1) { setError("Ya no hay disponibilidad para esas fechas."); return; }
    const reservation={id:"RESV-"+Date.now(),type:room.id,room:room.name,checkIn,checkOut,name:name.trim(),total:room.price*nights};
    const next=[reservation,...bookings]; localStorage.setItem(KEY,JSON.stringify(next)); setBookings(next);
    setSelected(null); setName(""); setEmail(""); setNotice("Reserva confirmada en esta demo local."); setSearched(true);
  }
  function reset() { localStorage.removeItem(KEY); setBookings([]); setNotice("Se borraron las reservas locales de demostración."); }

  return <div className="page">
    <div className="demo"><i/> Demo local · Los datos se guardan en este navegador</div>
    <header><a className="brand" href="#inicio"><b>A</b><span><strong>aloft</strong><small>MONTEVIDEO</small></span></a><nav><a href="#habitaciones">Habitaciones</a><a href="#hotel">El hotel</a></nav><button className="dark" title="Cognito se conectará después">Iniciar sesión ↗</button></header>
    <main id="inicio">
      <section className="hero"><div><p className="eyebrow">✳ PUNTA CARRETAS · MONTEVIDEO</p><h1>Montevideo te espera,<br/><em>quedate a disfrutar.</em></h1><p className="intro">Descubrí Punta Carretas desde un hotel moderno, con habitaciones cómodas y algunas opciones con vista al río.</p></div><div className="photo"><span>ALOFT MONTEVIDEO</span><strong>Un lugar para conectar.</strong></div></section>
      <section className="search"><form onSubmit={search}><label>Llegada<input type="date" min={today} value={checkIn} onChange={e=>setCheckIn(e.target.value)} required/></label><label>Salida<input type="date" min={checkIn} value={checkOut} onChange={e=>setCheckOut(e.target.value)} required/></label><label>Huéspedes<select value={guests} onChange={e=>setGuests(e.target.value)}><option value="1">1 huésped</option><option value="2">2 huéspedes</option><option value="3">3 huéspedes</option><option value="4">4 huéspedes</option></select></label><button className="primary">Buscar habitaciones →</button></form>{notice&&<p className="notice" role="status">{notice}</p>}</section>
      <section className="rooms" id="habitaciones"><div className="section-head"><div><p className="eyebrow">DESCANSÁ COMO TE GUSTA</p><h2>Elegí tu habitación</h2><p>{searched?dateLabel(checkIn)+" — "+dateLabel(checkOut)+" · "+nights+" noches":"Habitaciones pensadas para que disfrutes cada momento."}</p></div></div><div className="grid">{availability.map((room,i)=><article className="card" key={room.id}><div className={"room art"+i}><b>✳</b><small>{room.id}</small></div><div className="card-content"><h3>{room.name}</h3><p>{room.description}</p><small>Capacidad: {room.guests} · Wi-Fi · Aire acondicionado</small><div className="bottom"><div><strong>{price(room.price)}</strong><small> / noche</small>{searched&&<em>{price(room.price*nights)} total</em>}</div><button disabled={searched&&room.left===0} onClick={()=>{setSelected(room);setError("");}}>{searched&&room.left===0?"Sin cupo":"Reservar →"}</button></div>{searched&&<small className={room.left?"available":"sold"}>{room.left?room.left+" disponibles para esas fechas":"No disponible para esas fechas"}</small>}</div></article>)}</div></section>
      <section className="hotel" id="hotel"><b>✳</b><div><p className="eyebrow">ALOFT MONTEVIDEO HOTEL</p><h2>En el corazón de Punta Carretas</h2><p>Victor Soliño 350 · Piscina interior · Gimnasio Re:Charge · Restaurante Nook · Bar W XYZ</p></div></section>
      {bookings.length>0&&<section className="bookings"><div className="section-head"><h2>Reservas de demostración</h2><button onClick={reset}>Borrar datos de demo</button></div>{bookings.slice(0,5).map(b=><article key={b.id}><div><strong>{b.room}</strong><small>{dateLabel(b.checkIn)} — {dateLabel(b.checkOut)} · {b.name}</small></div><b>{price(b.total)}</b></article>)}</section>}
    </main><footer><a className="brand" href="#inicio"><b>A</b><span><strong>aloft</strong><small>MONTEVIDEO</small></span></a><span>Demo de reservas hoteleras · Montevideo, Uruguay</span></footer>
    {selected&&<div className="veil" onMouseDown={e=>e.target===e.currentTarget&&setSelected(null)}><section className="modal" role="dialog" aria-modal="true"><button className="close" onClick={()=>setSelected(null)}>×</button><p className="eyebrow">CONFIRMAR ESTADÍA</p><h2>{selected.name}</h2><p>{dateLabel(checkIn)} — {dateLabel(checkOut)} · {nights} noches</p><div className="total"><span>Total</span><strong>{price(selected.price*nights)}</strong></div><p className="local">Modo local; Cognito se integrará después.</p><form onSubmit={book}><label>Nombre y apellido<input value={name} onChange={e=>setName(e.target.value)} required/></label><label>Correo electrónico<input type="email" value={email} onChange={e=>setEmail(e.target.value)} required/></label>{error&&<p className="error">{error}</p>}<button className="primary">Confirmar reserva →</button></form></section></div>}
  </div>;
}




