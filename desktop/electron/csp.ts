/** Content-Security-Policy for the renderer. Strict in production; dev needs Vite's HMR. */
export function buildCsp(devOrigin: string | null): string {
  const scriptSrc = devOrigin ? "'self' 'unsafe-inline'" : "'self'"
  const connect = ["'self'", 'http://127.0.0.1:*']
  if (devOrigin) connect.push(devOrigin.replace(/^http/, 'ws'))
  return [
    "default-src 'self'",
    `script-src ${scriptSrc}`,
    "style-src 'self' 'unsafe-inline'", // Radix/sonner set inline style attributes
    "img-src 'self' data:",
    "font-src 'self' data:",
    `connect-src ${connect.join(' ')}`,
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
  ].join('; ')
}
