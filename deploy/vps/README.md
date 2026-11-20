# Deploy Gridpilot AI on a VPS (Docker + nginx)

The app runs in Docker on `127.0.0.1:7860`; the server's existing nginx forwards your hostname to it and
certbot adds free HTTPS. (If the server has nothing on ports 80/443 you can use Caddy in front instead.) The app must run as a **single process** (the solver lock
and the caches live in memory), which is how the Dockerfile starts it.

## 0. Before you start
- DNS: an **A record** for your hostname (e.g. `energybrain.xven.app`) pointing at the server's IPv4 address.
- Server: Ubuntu/Debian, about 2 GB RAM, ports 80 and 443 open in the provider firewall.
- Prefer a normal user with SSH keys over root with a password.

## 1. Build the upload folder (on your own computer, in the repo root)
```bash
bash scripts/make_hf_space.sh build/vps-app      # includes your trained data/models and a checksum manifest
mkdir -p build/vps && cp -r deploy/vps/. build/vps/ && mv build/vps-app build/vps/app
```
(The `app` folder must contain the `Dockerfile`; the script puts it there. The Hugging Face `README.md` in it is
only a package metadata file and is harmless here.)

## 2. Install Docker on the server (once)
```bash
curl -fsSL https://get.docker.com | sh
```

## 3. Upload and start
```bash
rsync -az --delete build/vps/ USER@SERVER:~/energy-brain/
ssh USER@SERVER
cd ~/energy-brain
cp .env.example .env && nano .env        # set LLM_API_KEY
docker compose up -d --build
docker compose logs -f                   # first build takes a few minutes
curl -s http://127.0.0.1:7860/health     # works before nginx is set up
```

## 4. nginx and HTTPS (on the server)
```bash
cp nginx-energybrain.conf /etc/nginx/sites-available/energybrain.xven.app
ln -s /etc/nginx/sites-available/energybrain.xven.app /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
certbot --nginx -d energybrain.xven.app
```
Open `https://YOUR-HOSTNAME/health`: it should show `"status":"ok"`, `"models":true`, `"processed_data":true`.

## Update after code changes
Repeat the export (step 1) and `rsync`, then `docker compose up -d --build` on the server.

## Notes
- Anyone with the link can use the app. The synthetic-data caveats in the UI stay visible.
- Rotate the Groq key by editing `.env` and running `docker compose up -d`.
- Logs: `docker compose logs app`. Restart: `docker compose restart app`.
