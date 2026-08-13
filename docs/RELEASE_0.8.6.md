# Elyndra 0.8.6-alpha


- Corrección acumulativa de autenticación: `reset-password-local` funciona sin sesión previa y acepta usuario o correo; `/register` permanece visible y explica que el aislamiento multicuenta aún no está habilitado. No se crean cuentas adicionales sobre una bóveda compartida para evitar exposición de chats, memoria y datos personales.
## Scope

Identity and Dialogue Foundation: local account registration/login, profile separation, adult consent gate, Argon2id sessions, user/developer web modes, encrypted local export, telemetry opt-in preview, 2FA-ready schema, clarification continuity and capability-aware help.

## Security properties

- one active local account;
- no plaintext passwords or session tokens in SQLite;
- optional sensitive fields excluded from model context when blank;
- no remote backup, online gateway, telemetry delivery or active 2FA;
- direct-address guard prevents developer/current-user third-person mistakes;
- explicit approval for profile, security and export writes;
- CLI/web parity over the same account and dialogue repositories.

## Compatibility

SQLite advances from schema 46 to 47 without deleting existing chats, memory, knowledge, organizer, wellbeing or automation data. Installations without an account remain migratable; the web presents registration before exposing the application shell.

## Corrección de navegación de autenticación

- `/login` y `/register` son rutas web independientes.
- Una sesión válida redirige al chat principal.
- Una ruta privada sin sesión redirige a `/login`.
- La sesión persiste hasta su vencimiento o hasta que el usuario cierre sesión.
- Los contenedores ocultos no pueden reaparecer por reglas CSS de `display`.

## Corrección del shell autenticado

- La cabecera, navegación, búsqueda y cuenta permanecen fijas; solo el historial de chats se desplaza.
- La cuenta activa aparece siempre al pie de la barra lateral con acceso a Perfil y Cerrar sesión.
- El menú superior de acciones se muestra únicamente para un chat existente y ya no se cierra por propagación del mismo clic que lo abre.
- `/login` y `/register` siguen siendo páginas exclusivas para sesiones no autenticadas; una sesión activa redirige correctamente al chat.

- Se añadió recuperación local de contraseña desde el mismo usuario del sistema y la pantalla de login ya no ofrece registrar una segunda cuenta.
