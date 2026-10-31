# contact-form

A small contact form endpoint for borck.consulting. It accepts a form POST, checks a honeypot and optional Cloudflare Turnstile, sends the message through Resend, stores a copy in SQLite, and redirects to a thank-you page.

Built in GitHub Actions, pushed to GHCR, pulled by the VPS. Secrets live in a local `.env` on the VPS.

## Endpoints

- `GET /healthz` returns `{"status":"ok"}`.
- `POST /contact` accepts the form fields `name`, `firm`, `email`, `situation`, and the honeypot `website`.
- `GET /` returns a small JSON service marker.

## Local run

```bash
cp .env.example .env
# edit .env, at minimum RESEND_API_KEY, TO_EMAIL, FROM_EMAIL
chmod 600 .env
docker compose up --build
```

Test:

```bash
curl -i http://127.0.0.1:8080/healthz
curl -i -X POST http://127.0.0.1:8080/contact \
  -d 'name=Test' \
  -d 'email=test@example.com' \
  -d 'situation=Hello'
```

Without valid Resend settings the send will fail and the endpoint returns 502.

## Tests

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

## Environment

| Variable | Required | Notes |
|---|---|---|
| `RESEND_API_KEY` | yes | Resend API key. |
| `TO_EMAIL` | yes | Where submissions go. |
| `FROM_EMAIL` | yes | Must be on a verified Resend sending domain. |
| `TURNSTILE_SECRET` | no | If set, Turnstile is enforced. |
| `SUCCESS_REDIRECT` | no | Defaults to `https://borck.consulting/thanks/`. |
| `DATABASE_PATH` | no | Defaults to `data/contact.db`. In Docker use `/data/contact.db`. |
| `IP_HASH_SALT` | yes | Random string. Change it from the example. |
| `RATE_LIMIT_PER_HOUR` | no | Defaults to 5 per IP. |
| `MAX_MESSAGE_LENGTH` | no | Defaults to 5000. |

## Deploy

On the VPS:

```bash
git clone https://github.com/michael-borck/contact-form.git
cd contact-form
cp .env.example .env
chmod 600 .env
# edit .env
docker compose pull
docker compose up -d
```

The GHCR image is public, so no registry login is needed on the VPS. The image contains no secrets. The `.env` file stays on the VPS.

Rollback: set `TAG` in `.env` to a previous commit SHA, then `docker compose up -d`.

## Caddy

The service joins the existing `caddy_default` network. Add this to the existing Caddyfile on the VPS at `/home/michael/homelab/caddy/Caddyfile`:

```caddy
forms.borck.consulting {
	encode gzip
	reverse_proxy contact:8080
	tls {
		dns cloudflare {env.CLOUDFLARE_API_TOKEN}
	}
	header {
		Strict-Transport-Security "max-age=31536000;"
		X-Content-Type-Options "nosniff"
		Referrer-Policy "strict-origin-when-cross-origin"
	}
}
```

Reload Caddy:

```bash
docker exec caddy caddy reload --config /etc/caddy/Caddyfile
```

The service uses the external Docker network `caddy_default`. If you run it locally, create that network first:

```bash
docker network create caddy_default
```

## Cloudflare DNS

Create one record:

- Type: `CNAME`
- Name: `forms`
- Target: `srv858112.hstgr.cloud`
- Proxy status: Proxied, orange cloud
- TTL: Auto

Caddy uses the Cloudflare DNS challenge, so the proxied record is fine and the certificate will issue.

If you prefer not to use a CNAME, use an `A` record for `forms` pointing to `31.97.67.189` and an `AAAA` record pointing to `2a02:4780:59:57c5::1`.

## Resend and Purelymail

- Verify the sending subdomain `send.borck.dev` in Resend.
- Add the SPF, DKIM, and DMARC records Resend provides for that subdomain.
- Keep Purelymail's MX records on `borck.dev` so `michael@borck.dev` still receives mail.
- Use `forms@send.borck.dev` as `FROM_EMAIL`, and set `TO_EMAIL=michael@borck.dev`.
- Replies go to the submitter because the service sets `reply_to`.

## Form markup

Use this on the consulting site. Post to the service, not to a third party.

```html
<form class="contact-form" method="POST" action="https://forms.borck.consulting/contact">
  <div>
    <label for="name">Your name</label>
    <input type="text" id="name" name="name" required />
  </div>
  <div>
    <label for="firm">Firm</label>
    <input type="text" id="firm" name="firm" />
  </div>
  <div>
    <label for="email">Email</label>
    <input type="email" id="email" name="email" required />
  </div>
  <div>
    <label for="situation">What's the situation?</label>
    <textarea id="situation" name="situation" rows="4"></textarea>
  </div>
  <div style="position:absolute;left:-9999px;" aria-hidden="true">
    <label for="website">Website</label>
    <input type="text" id="website" name="website" tabindex="-1" autocomplete="off" />
  </div>
  <!-- Turnstile, optional. The service checks cf-turnstile-response. -->
  <div class="cf-turnstile" data-sitekey="YOUR_TURNSTILE_SITE_KEY"></div>
  <button type="submit" class="btn">Send</button>
</form>
```

Add the Turnstile script once on the page if you use it:

```html
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
```

## Security

- Bind the container to `127.0.0.1`, not `0.0.0.0`.
- Keep `.env` at mode 600 and out of git.
- Honeypot, Turnstile, and per-IP rate limiting are included.
- The service stores a truncated IP hash, not the raw address.
- Delete submissions after 90 days. The table has a timestamp for that.
- Do not add file uploads. Use a separate intake process for documents.
