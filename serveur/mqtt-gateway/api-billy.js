// Petite API HTTP locale ajoutée à xiaozhi-mqtt-gateway pour parler à Billy hors conversation.
// La connexion MQTT du robot reste ouverte en permanence : on peut donc lui pousser un message
// (notification sonore) ou appeler un de ses outils MCP (LEDs, tête) même quand il attend.
//
// Branchement : une ligne à la fin d'app.js, après server.start() :
//   require('./api-billy').startApi(server);
//
//   GET  /appareils                          -> [{ mac, conversation }]
//   POST /envoyer { mac?, message }          -> pousse le JSON « message » au robot
//   POST /mcp     { mac?, method, params }   -> appelle le robot en MCP, renvoie le résultat
// Sans « mac », on vise le premier robot connecté.

const http = require('http');

const PORT = parseInt(process.env.BILLY_API_PORT) || 8885;
const MCP_TIMEOUT_MS = 5000;

function findConnection(server, mac) {
    const connections = Array.from(server.connections.values()).filter(c => c.macAddress);
    return mac ? connections.find(c => c.macAddress === mac.toLowerCase()) : connections[0];
}

function reply(res, status, data) {
    const body = JSON.stringify(data);
    res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(body);
}

function readJson(req) {
    return new Promise((resolve, reject) => {
        let body = '';
        req.on('data', chunk => {
            body += chunk;
            if (body.length > 65536) reject(new Error('corps trop gros'));
        });
        req.on('end', () => {
            try { resolve(body ? JSON.parse(body) : {}); } catch (e) { reject(e); }
        });
    });
}

function withTimeout(promise, ms) {
    return Promise.race([promise, new Promise((_, reject) =>
        setTimeout(() => reject(new Error('pas de réponse du robot')), ms))]);
}

function startApi(server) {
    http.createServer(async (req, res) => {
        try {
            if (req.method === 'GET' && req.url === '/appareils') {
                return reply(res, 200, Array.from(server.connections.values())
                    .filter(c => c.macAddress)
                    .map(c => ({ mac: c.macAddress, conversation: Boolean(c.isAlive()) })));
            }
            if (req.method !== 'POST' || !['/envoyer', '/mcp'].includes(req.url)) {
                return reply(res, 404, { erreur: 'introuvable' });
            }
            const body = await readJson(req);
            const connection = findConnection(server, body.mac);
            if (!connection) return reply(res, 404, { erreur: 'robot non connecté' });
            if (req.url === '/envoyer') {
                if (!body.message || typeof body.message.type !== 'string') {
                    return reply(res, 400, { erreur: 'message.type manquant' });
                }
                connection.sendMqttMessage(JSON.stringify(body.message));
                return reply(res, 200, { envoye: connection.macAddress });
            }
            if (typeof body.method !== 'string') return reply(res, 400, { erreur: 'method manquant' });
            const result = await withTimeout(connection.sendMcpRequest(body.method, body.params || {}),
                                             MCP_TIMEOUT_MS);
            return reply(res, 200, { resultat: result });
        } catch (error) {
            return reply(res, 500, { erreur: String(error.message || error) });
        }
    }).listen(PORT, '127.0.0.1', () => {
        console.warn(`API Billy sur http://127.0.0.1:${PORT}`);
    });
}

module.exports = { startApi };
