// Direcciones del backend desplegado. Se inyectan al compilar con variables
// VITE_* (scripts/publicar_frontend.py las toma de las salidas del stack).
// Para desarrollo local: definirlas antes de `npm run dev`.
export const CONFIG = {
  region: import.meta.env.VITE_REGION || "us-east-1",
  apiUrl: (import.meta.env.VITE_API_URL || "").replace(/\/$/, ""),
  wsUrl: import.meta.env.VITE_WS_URL || "",
  userPoolClientId: import.meta.env.VITE_COGNITO_CLIENT_ID || "",
};

export const configured = Boolean(CONFIG.apiUrl && CONFIG.userPoolClientId);
