// Static server for the canvas boards. `support.js` is the Claude Design runtime, which the
// canvas host normally injects; set DC_RUNTIME to a saved copy of artifact-type/dc-runtime.js.
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(fileURLToPath(new URL('.', import.meta.url)), '..', 'project');
const runtime = process.env.DC_RUNTIME;
const port = Number(process.env.PORT ?? 4173);
const types = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.json': 'application/json' };

createServer(async (req, res) => {
  try {
    const path = new URL(req.url, 'http://x').pathname;
    const file = path === '/support.js' ? runtime : join(root, normalize(path).replace(/^(\.\.[/\\])+/, ''));
    if (!file) throw new Error('DC_RUNTIME not set');
    const body = await readFile(file);
    res.writeHead(200, { 'content-type': types[extname(file)] ?? 'application/octet-stream' });
    res.end(body);
  } catch {
    res.writeHead(404).end('not found');
  }
}).listen(port, () => console.log(`canvas boards on http://localhost:${port}`));
