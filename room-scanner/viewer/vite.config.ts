import { defineConfig } from 'vite';
import fs from 'node:fs';
import path from 'node:path';

const DATA = path.resolve(__dirname, '../data');
const mime: Record<string, string> = { '.json': 'application/json', '.jpg': 'image/jpeg', '.png': 'image/png', '.bin': 'application/octet-stream', '.ply': 'application/octet-stream', '.glb': 'model/gltf-binary' };

// Serves ../data at /data and lists scans at /data/index.json
function dataPlugin() {
  return {
    name: 'room-data',
    configureServer(server: any) {
      server.middlewares.use('/data', (req: any, res: any, next: any) => {
        const url = decodeURIComponent((req.url || '/').split('?')[0]);
        if (url === '/index.json') {
          const scans = fs.existsSync(DATA) ? fs.readdirSync(DATA).filter((d) => fs.existsSync(path.join(DATA, d, 'manifest.json'))).sort().reverse() : [];
          res.setHeader('Content-Type', 'application/json');
          return res.end(JSON.stringify(scans));
        }
        const f = path.join(DATA, url);
        if (!f.startsWith(DATA) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) { res.statusCode = 404; return res.end('not found'); }  // no SPA fallback for data files
        res.setHeader('Content-Type', mime[path.extname(f)] || 'application/octet-stream');
        fs.createReadStream(f).pipe(res);
      });
    },
  };
}

export default defineConfig({ plugins: [dataPlugin()], server: { host: '127.0.0.1', port: 5187, strictPort: true } });
