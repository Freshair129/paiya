import { pathToFileURL } from 'node:url'

export function createO5VercelConfig(ingress) {
  const url = new URL(ingress)
  if (url.protocol !== 'https:' || !/^[a-z0-9-]+(?:\.[a-z0-9-]+)+$/.test(url.hostname) ||
      url.origin !== ingress || url.port || /^[0-9.]+$/.test(url.hostname)) {
    throw new Error('Pass the exact HTTPS ingress origin with a DNS hostname and no port or path.')
  }
  return {
    $schema: 'https://openapi.vercel.sh/vercel.json',
    framework: 'vite',
    routes: [
      {
        src: '/v1(/.*)?',
        dest: `${url.origin}/v1$1`,
        headers: {
          'Cache-Control': 'no-store',
          'CDN-Cache-Control': 'no-store',
          'Vercel-CDN-Cache-Control': 'no-store',
        },
        transforms: [{
          type: 'request.headers',
          op: 'set',
          target: { key: 'x-paiya-proxy-key' },
          args: '$PAIYA_O5_PROXY_KEY',
          env: ['PAIYA_O5_PROXY_KEY'],
        }],
      },
      { handle: 'filesystem' },
      { src: '/(.*)', dest: '/index.html' },
    ],
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.stdout.write(`${JSON.stringify(createO5VercelConfig(process.argv[2]), null, 2)}\n`)
}
