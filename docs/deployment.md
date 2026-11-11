# Deployment & Operations

## Run with Podman Compose or Docker Compose

Download the configuration and start the server:

```bash
# 1. Download compose configuration
curl -fsSL https://raw.githubusercontent.com/Delnegend/l4d2-server-container/main/compose.yaml -o compose.yaml

# 2. Start the server
podman compose up -d

# 3. View startup logs
podman compose logs -f
```

Look for `Self-check OK: server answers A2S queries` in the container log to confirm public discoverability.

## Run with Container CLI

```bash
podman run -d --name l4d2-server \
  --restart unless-stopped \
  -p 27015:27015/tcp -p 27015:27015/udp \
  -e SERVER_NAME="Left 4 Dead 2 Dedicated Server" \
  -e SERVER_MODE="8players" \
  -v ./data:/data:Z \
  ghcr.io/delnegend/l4d2-server-container:latest
```

## Logs & Verification

Monitor server status, Steam connection, and player slots:

```bash
podman compose logs -f
```

## Upgrades

Pull the latest container image and restart the service:

```bash
podman compose pull
podman compose up -d
```
