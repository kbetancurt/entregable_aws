// Avisos en tiempo real por WebSocket (secciones 6.6 y 7.2 del documento).
// El navegador no puede mandar encabezados en un WebSocket: el access token
// viaja como ?token=. API Gateway corta conexiones inactivas a los 10 minutos
// y las que duran más de 2 horas, así que se manda un ping periódico y se
// reconecta solo.
import { CONFIG } from "./config.js";

const PING_MS = 5 * 60 * 1000;

export function conectarAvisos(getAccessToken, { onEvento, onEstado }) {
  let ws, ping, retry, cerrado = false, intentos = 0;

  async function abrir() {
    if (cerrado) return;
    const token = await getAccessToken();
    if (!token || cerrado) return;
    onEstado?.("conectando");
    ws = new WebSocket(`${CONFIG.wsUrl}?token=${encodeURIComponent(token)}`);
    ws.onopen = () => {
      intentos = 0;
      onEstado?.("conectado");
      ping = setInterval(() => ws.readyState === 1 && ws.send(JSON.stringify({ action: "ping" })), PING_MS);
    };
    ws.onmessage = msg => {
      try {
        const evento = JSON.parse(msg.data);
        if (evento.tipo !== "PONG") onEvento(evento);
      } catch { /* mensaje no JSON */ }
    };
    ws.onclose = () => {
      clearInterval(ping);
      if (cerrado) return;
      onEstado?.("reconectando");
      const espera = Math.min(30_000, 1000 * 2 ** intentos++);
      retry = setTimeout(abrir, espera);
    };
  }

  abrir();
  return () => {
    cerrado = true;
    clearInterval(ping);
    clearTimeout(retry);
    ws?.close();
  };
}
