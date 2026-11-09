// Llamadas a la API REST (sección 4 del documento).
import { CONFIG } from "./config.js";

async function request(path, { token, headers, ...options } = {}) {
  let res;
  try {
    res = await fetch(CONFIG.apiUrl + path, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: token } : {}),
        ...headers,
      },
    });
  } catch {
    throw Object.assign(new Error("No se pudo conectar con el servidor."), { status: 0 });
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message = data.error || data.message ||
      (res.status === 401 ? "Tu sesión venció; volvé a iniciar sesión." : "Ocurrió un error.");
    throw Object.assign(new Error(message), { status: res.status });
  }
  return data;
}

// 4.1 Consultar disponibilidad: público, sin token.
export function buscarHabitaciones({ entrada, salida, huespedes }) {
  const qs = new URLSearchParams({ entrada, salida, huespedes: String(huespedes) });
  return request(`/habitaciones?${qs}`);
}

// 4.2 Hacer una reserva: con el ID token de Cognito. La Idempotency-Key evita
// duplicar la reserva si el mismo pedido se reintenta.
export function crearReserva({ entrada, salida, huespedes, habitaciones }, idToken, idempotencyKey) {
  return request("/reservas", {
    method: "POST",
    token: idToken,
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ entrada, salida, huespedes, habitaciones }),
  });
}

// Alertas del panel de administración (solo admin): con el ID token de Cognito.
export function listarAlertas(idToken, limite = 50) {
  return request(`/alertas?limite=${limite}`, { token: idToken });
}

export function marcarAlertasLeidas(cuerpo, idToken) {
  return request("/alertas/leer", { method: "POST", token: idToken, body: JSON.stringify(cuerpo) });
}
