// Registro y login con Cognito (secciones 3 y 7.2 del documento).
// Cognito gestiona las contraseñas; el frontend solo guarda los tokens de la
// sesión en sessionStorage (se borran al cerrar la pestaña).
import { CONFIG } from "./config.js";

const KEY = "aloft-sesion";

async function cognito(action, body) {
  const res = await fetch(`https://cognito-idp.${CONFIG.region}.amazonaws.com/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-amz-json-1.1",
      "X-Amz-Target": `AWSCognitoIdentityProviderService.${action}`,
    },
    body: JSON.stringify({ ClientId: CONFIG.userPoolClientId, ...body }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const error = new Error(traducir(data.__type, data.message));
    error.code = (data.__type || "").split("#").pop();
    throw error;
  }
  return data;
}

function traducir(type = "", message = "") {
  const code = type.split("#").pop();
  return {
    NotAuthorizedException: "Correo o contraseña incorrectos.",
    UserNotConfirmedException: "Tenés que confirmar tu correo con el código que te enviamos.",
    UsernameExistsException: "Ya existe una cuenta con ese correo.",
    InvalidPasswordException: "La contraseña necesita al menos 8 caracteres, con mayúsculas, minúsculas y números.",
    CodeMismatchException: "El código no es correcto.",
    ExpiredCodeException: "El código venció; pedí uno nuevo.",
    LimitExceededException: "Demasiados intentos; probá en unos minutos.",
  }[code] || message || "No se pudo completar la operación.";
}

function claims(jwt) {
  const part = jwt.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
  const json = decodeURIComponent(
    atob(part).split("").map(c => "%" + c.charCodeAt(0).toString(16).padStart(2, "0")).join("")
  );
  return JSON.parse(json);
}

function guardar(result, refreshToken) {
  const id = claims(result.IdToken);
  const session = {
    idToken: result.IdToken,
    accessToken: result.AccessToken,
    refreshToken: result.RefreshToken || refreshToken,
    expiresAt: id.exp * 1000,
    email: id.email,
    groups: id["cognito:groups"] || [],
  };
  try { sessionStorage.setItem(KEY, JSON.stringify(session)); } catch { /* sin storage */ }
  return session;
}

export async function login(email, password) {
  const data = await cognito("InitiateAuth", {
    AuthFlow: "USER_PASSWORD_AUTH",
    AuthParameters: { USERNAME: email, PASSWORD: password },
  });
  return guardar(data.AuthenticationResult);
}

export async function registrar(email, password) {
  await cognito("SignUp", {
    Username: email,
    Password: password,
    UserAttributes: [{ Name: "email", Value: email }],
  });
}

export async function confirmar(email, code) {
  await cognito("ConfirmSignUp", { Username: email, ConfirmationCode: code });
}

export async function reenviarCodigo(email) {
  await cognito("ResendConfirmationCode", { Username: email });
}

export function sesionGuardada() {
  try { return JSON.parse(sessionStorage.getItem(KEY) || "null"); } catch { return null; }
}

export function cerrarSesion() {
  try { sessionStorage.removeItem(KEY); } catch { /* sin storage */ }
}

// Devuelve una sesión vigente, renovando con el refresh token si hace falta.
export async function sesionVigente(session) {
  if (!session) return null;
  if (Date.now() < session.expiresAt - 60_000) return session;
  try {
    const data = await cognito("InitiateAuth", {
      AuthFlow: "REFRESH_TOKEN_AUTH",
      AuthParameters: { REFRESH_TOKEN: session.refreshToken },
    });
    return guardar(data.AuthenticationResult, session.refreshToken);
  } catch {
    cerrarSesion();
    return null;
  }
}
