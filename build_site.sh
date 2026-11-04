#!/usr/bin/env bash
# Tạo thư mục site/ (giao diện tĩnh, đã minify) để deploy lên Vercel.
#   ./build_site.sh            -> chỉ build
#   ./build_site.sh --deploy   -> build rồi chạy "vercel deploy --prod" trong site/
set -euo pipefail
cd "$(dirname "$0")"
rm -rf site && mkdir -p site
cp static/index.html site/index.html
cp assets/logo.png site/logo.png
npx --yes esbuild@0.24.0 static/app.js --minify --outfile=site/app.js --log-level=warning
npx --yes esbuild@0.24.0 static/style.css --minify --outfile=site/style.css --log-level=warning
npx --yes esbuild@0.24.0 static/mobile.css --minify --outfile=site/mobile.css --log-level=warning
cat > site/vercel.json <<'JSON'
{
  "headers": [
    { "source": "/(.*)\\.(js|css)", "headers": [{ "key": "Cache-Control", "value": "public, max-age=0, must-revalidate" }] }
  ]
}
JSON
ls -la site
if [ "${1:-}" = "--deploy" ]; then
  (cd site && npx --yes vercel deploy --prod)
fi
