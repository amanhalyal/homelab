# Bulk changes through the Nginx Proxy Manager and Uptime Kuma APIs

Two admin tasks that are painful in a UI and easy to get subtly wrong. Credentials are never stored in this repository. They are supplied by whoever makes the change.

## Nginx Proxy Manager

Use the API whenever a setting must be checked or changed across all hosts at once. This is how `block_exploits` was enabled on all 19 hosts in one pass ([0004](../decisions/0004-reverse-proxy-hardening-and-redundancy.md)).

Get a token and list a field across all hosts:

```bash
TOKEN=$(curl -s -X POST http://localhost:81/api/tokens \
  -H "Content-Type: application/json" \
  -d '{"identity":"<email>","secret":"<password>"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")

curl -s http://localhost:81/api/nginx/proxy-hosts -H "Authorization: Bearer $TOKEN" \
  | python3 -c "
import json,sys
for h in json.load(sys.stdin):
    print(h['id'], h['domain_names'], h.get('block_exploits'))"
```

**The update gotcha.** `GET` returns read-only fields that `PUT` rejects with `data must NOT have additional properties` if you echo them back. Strip them first:

```python
for f in ['id', 'created_on', 'modified_on', 'owner_user_id', 'meta']:
    h.pop(f, None)
```

**Verify at the config level.** An external curl test is unreliable, because loopback TLS against NPM's own container can fail with SNI or handshake artifacts that look like a breakage but are not. Instead:

```bash
docker exec nginx-proxy-manager nginx -t
docker exec nginx-proxy-manager grep -c "block-exploits" /data/nginx/proxy_host/<id>.conf
```

`nginx -t` validates the whole regenerated config, and the grep confirms the setting reached a specific host.

## Uptime Kuma

Kuma 1.23 has no REST API for monitor create, update or delete. Everything goes through Socket.IO events, so this is the way to add, edit or remove monitors without the UI.

**Match the client to the server's major version.** The server speaks Engine.IO and Socket.IO v4. A client on the wrong major fails with unhelpful `xhr poll error` or `websocket error` messages and no version hint. Confirm the server's version first:

```bash
curl -s "http://<container-ip>:3001/socket.io/?EIO=4&transport=polling"
# a real handshake payload, not {"code":5,"message":"Unsupported protocol version"}
npm install socket.io-client@4      # @2 uses the wrong protocol and never connects
```

Kuma is not published on a host port. Reach it through its container's bridge IP, which the Docker host can access directly:

```bash
docker inspect uptime-kuma --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
```

Log in with a `login` event and wait for `{ok: true, token}`. The events you need are `add`, `editMonitor` (which also needs the monitor `id`) and `deleteMonitor` (the id only).

**Getting a correct payload.** There is no schema document. The fields are defined in the server source (`socket.on("add"` in `server.js`). The fastest route is to read an existing working monitor's row, read-only, and mirror its shape. Note that `accepted_statuscodes` must be an array of strings such as `["200-299"]`, and `notificationIDList` can be `{}`.

**Never write to the SQLite database directly while Kuma runs.** Read-only queries are fine for inspection. For writes there is no safe pattern while the container is up, so mutate through the events above. The one exception, used once during the incident cleanup in [0022](../decisions/0022-citadel-unreachable-alert.md), is to stop the container, back up the database, edit it, verify, and start the container again.
