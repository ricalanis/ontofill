#!/bin/bash
# Cell "hands": headless Chromium with CDP on loopback, relayed to 0.0.0.0:9222 by a tiny TCP relay (Chrome >= 111
# ignores --remote-debugging-address outside the old headless shell). The relay keeps the client's Host header, so
# /json/version answers for 127.0.0.1:<published port> (stub) or the hands IP (Skyvern brain) with a matching ws:// URL.
set -e
CHROME=${CHROME:-$(ls -d /root/.cache/ms-playwright/chromium-*/chrome-linux/chrome 2>/dev/null | sort -V | tail -1)}
"$CHROME" --headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage --no-first-run \
  --remote-debugging-port=9221 --user-data-dir=/tmp/profile about:blank 2>/tmp/chrome.err &
exec python3 - <<'PY'
import asyncio
async def pipe(r, w):
    try:
        while (b := await r.read(65536)):
            w.write(b); await w.drain()
    except Exception:
        pass
    finally:
        w.close()
async def handle(cr, cw):
    for _ in range(100):
        try:
            ur, uw = await asyncio.open_connection("127.0.0.1", 9221); break
        except OSError:
            await asyncio.sleep(0.1)
    else:
        cw.close(); return
    await asyncio.gather(pipe(cr, uw), pipe(ur, cw))
async def main():
    srv = await asyncio.start_server(handle, "0.0.0.0", 9222)
    async with srv: await srv.serve_forever()
asyncio.run(main())
PY
